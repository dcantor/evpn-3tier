"""Live progress of a request while the worker carries it out — what the front end shows as it happens.

A request's `progress` (JSON on the model) is a snapshot the worker rewrites as it goes:

  stages     the workflow's stages (catalog.py) — pending / running / done / failed, with start and finish times
  pipelines  one entry per Gitea pipeline run the request started: its steps (Gitea's own: name, status, duration),
             and, parsed from the live job log, the current Ansible play and task, per-host ok / changed / failed counts
             of each step, verify's checks passed / failed, the last lines of the log, an estimate from earlier runs
  seq        bumped on every save: the event stream tells browsers "request N changed", they fetch it again

The worker's `log("message")` interface stays (a step in the request's history); stage() and pipeline() add the rest.
Saves are throttled (at most one a second) except at stage boundaries."""
import re
import time

from django.utils import timezone

TAIL = 30          # log lines kept in the snapshot (the whole log is read from Gitea on demand)
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
TASK = re.compile(r"^TASK \[(.+?)\]")
PLAY = re.compile(r"^PLAY \[(.+?)\]")
RESULT = re.compile(r"^(ok|changed|fatal|failed|skipping|unreachable): \[([^\]]+)\]")
CHECK = re.compile(r"^(PASS|FAIL)\s+(.+)$")
TOTAL = re.compile(r"^(\d+) of (\d+) checks passed")
STATE = {"ok": "ok", "changed": "changed", "fatal": "failed", "failed": "failed", "skipping": "skipped", "unreachable": "failed"}


def now():
    return timezone.now().isoformat()


class PipelineLog:
    """Turns a pipeline's log lines (per Gitea step) into the snapshot's view of it."""

    def __init__(self, snap):
        self.s = snap

    def feed(self, step, lines):
        st = self.s["steps"][step] if step < len(self.s["steps"]) else None
        if st is None: return
        hosts = st.setdefault("hosts", {})
        for raw in lines:
            line = ANSI.sub("", raw).rstrip()
            if not line: continue
            self.s["tail"] = (self.s["tail"] + [f"{st['name'][:18]}│ {line}"])[-TAIL:]
            if m := PLAY.match(line): self.s["play"] = m.group(1); continue
            if m := TASK.match(line): self.s["task"] = m.group(1); st["task"] = m.group(1); continue
            if m := RESULT.match(line):
                h = m.group(2).split(" ")[0].split("]")[0].removesuffix(":")      # "lb1 -> localhost", "lb1] => (item=x)"
                c = hosts.setdefault(h, {"ok": 0, "changed": 0, "failed": 0, "skipped": 0})
                c[STATE[m.group(1)]] += 1
                continue
            if m := CHECK.match(line):
                v = self.s.setdefault("verify", {"passed": 0, "failed": 0, "total": None, "failures": []})
                if m.group(1) == "PASS": v["passed"] += 1
                else: v["failed"] += 1; v["failures"] = (v["failures"] + [m.group(2)[:160]])[-10:]
                continue
            if m := TOTAL.match(line):
                self.s.setdefault("verify", {"passed": 0, "failed": 0, "total": None, "failures": []})["total"] = int(m.group(2))


class Progress:
    """Passed to the executors as `log`: callable for history lines, plus stage() and pipeline()."""

    def __init__(self, request, stages):
        self.r = request; self._t = 0.0
        old = request.progress or {}
        done_before = {s["key"]: s for s in old.get("stages", []) if s.get("status") == "done"}
        # a retry starts the stages again (every step is idempotent and re-runs); earlier pipeline runs stay listed
        self.p = {"stages": [{"key": k, "label": l, "status": "pending", "started": None, "finished": None,
                              "previous": bool(done_before.get(k))} for k, l in stages],
                  "pipelines": old.get("pipelines", {}), "attempt": old.get("attempt", 0) + 1, "seq": old.get("seq", 0),
                  "started": now(), "finished": None, "current": None}
        self.save(force=True)

    # ---- history ---------------------------------------------------------------------------------------------------------
    def __call__(self, msg):
        self.r.log(None, "step", msg)
        self.save(force=True)

    # ---- stages ----------------------------------------------------------------------------------------------------------
    def stage(self, key):
        for s in self.p["stages"]:
            if s["status"] == "running": s["status"], s["finished"] = "done", now()
        for s in self.p["stages"]:
            if s["key"] == key: s["status"], s["started"] = "running", now()
        self.p["current"] = key
        self.save(force=True)

    def finish(self, ok):
        for s in self.p["stages"]:
            if s["status"] == "running": s["status"], s["finished"] = ("done" if ok else "failed"), now()
        self.p["current"] = None; self.p["finished"] = now()
        self.save(force=True)

    # ---- pipelines -------------------------------------------------------------------------------------------------------
    def pipeline(self, repo, **fields):
        """Create / update the snapshot of a pipeline run; returns it (the Gitea follower feeds its log)."""
        key = f"{repo}#{fields['run']}" if "run" in fields else repo
        # the run number is known only once Gitea has created it: the placeholder (keyed by the repo) is re-keyed
        snap = self.p["pipelines"].get(key) or (self.p["pipelines"].pop(repo, None) if key != repo else None)
        if snap is None:
            snap = {"repo": repo, "run": None, "url": None, "status": "queued", "steps": [], "play": None, "task": None,
                    "tail": [], "started": now(), "finished": None, "eta_s": None, "attempt": self.p["attempt"]}
        self.p["pipelines"][key] = snap
        snap.update(fields)
        self.save()
        return snap

    def save(self, force=False):
        if not force and time.monotonic() - self._t < 1.0: return
        self._t = time.monotonic(); self.p["seq"] += 1
        self.r.progress = self.p
        self.r.save(update_fields=["progress", "updated"])


class NullProgress:
    """For callers without a request (tests, the command line): history lines are printed, the rest is ignored."""

    def __init__(self, out=print): self.out = out
    def __call__(self, msg): self.out(msg)
    def stage(self, key): pass
    def finish(self, ok): pass
    def pipeline(self, repo, **fields): return {"repo": repo, "steps": [], "tail": [], **fields}
    def save(self, force=False): pass
