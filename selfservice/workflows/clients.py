"""The systems a workflow touches: Nautobot (the source of truth), Gitea (the deploy pipelines) and phpIPAM (the external
IPAM). Credentials are read at run time — ~/.config/evpn-3tier/selfservice.env if it has them, else where the lab's other
tools find them (the NMS's /opt/nautobot/.env over SSH, phpIPAM's ~/.config/evpn-3tier/phpipam.env) — never stored in git."""
import os
import subprocess
import re
import time
from pathlib import Path

import requests
from django.conf import settings

NAUTOBOT_URL = os.environ.get("NAUTOBOT_URL", "http://10.0.0.10:8080")
GITEA_URL = os.environ.get("GITEA_URL", "http://10.0.0.10:3000")
GITEA_WEB = "http://192.168.50.231:3000"                     # what users can open (the LAN relay to Gitea)
LOCATION = "evpn-3tier"
_secret = {}


def _nms(key):
    """A value from the NMS's /opt/nautobot/.env (Nautobot token, Gitea password), as the lab's tools read it."""
    if key in settings.ENV: return settings.ENV[key]
    if key not in _secret:
        out = subprocess.run(["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                              "-o", "ConnectTimeout=10", "lab@10.0.0.10", f"grep ^{key}= /opt/nautobot/.env | cut -d= -f2"],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        if not out: raise RuntimeError(f"{key}: not in selfservice.env and not readable on the NMS")
        _secret[key] = out
    return _secret[key]


# ---- Nautobot -------------------------------------------------------------------------------------------------------------------
class Nautobot:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Token {_nms('NAUTOBOT_SUPERUSER_API_TOKEN')}", "Accept": "application/json",
                               "Content-Type": "application/json"})

    def get(self, path, **params):
        r = self.s.get(f"{NAUTOBOT_URL}/api/{path.strip('/')}/", params=params, timeout=30); r.raise_for_status(); return r.json()

    def one(self, path, **params):
        res = self.get(path, **params)["results"]; return res[0] if res else None

    def post(self, path, data):
        r = self.s.post(f"{NAUTOBOT_URL}/api/{path.strip('/')}/", json=data, timeout=30)
        if r.status_code >= 400: raise RuntimeError(f"Nautobot POST {path}: {r.status_code} {r.text[:300]}")
        return r.json()

    def patch(self, path, data):
        r = self.s.patch(f"{NAUTOBOT_URL}/api/{path.strip('/')}/", json=data, timeout=30)
        if r.status_code >= 400: raise RuntimeError(f"Nautobot PATCH {path}: {r.status_code} {r.text[:300]}")
        return r.json()

    def delete(self, path):
        """DELETE; False when it was not there (404)."""
        r = self.s.delete(f"{NAUTOBOT_URL}/api/{path.strip('/')}/", timeout=30)
        if r.status_code == 404: return False
        if r.status_code >= 400: raise RuntimeError(f"Nautobot DELETE {path}: {r.status_code} {r.text[:300]}")
        return True

    def graphql(self, query):
        r = self.s.post(f"{NAUTOBOT_URL}/api/graphql/", json={"query": query}, timeout=60); r.raise_for_status()
        body = r.json()
        if body.get("errors"): raise RuntimeError(f"Nautobot GraphQL: {body['errors']}")
        return body["data"]

    def context(self):
        return self.one("extras/config-contexts", name=LOCATION)


# ---- Gitea (the deploy pipelines) ------------------------------------------------------------------------------------------------
class Gitea:
    def __init__(self):
        self.auth = ("lab", _nms("GITEA_PASSWORD"))

    def runs(self, repo, n=10):
        r = requests.get(f"{GITEA_URL}/api/v1/repos/lab/{repo}/actions/tasks", params={"limit": n}, auth=self.auth, timeout=30); r.raise_for_status()
        return r.json()["workflow_runs"]

    def eta(self, repo):
        """Typical duration of the repo's pipeline: the median of its last successful runs (seconds), or None."""
        from datetime import datetime
        d = sorted((datetime.fromisoformat(t["updated_at"]) - datetime.fromisoformat(t["run_started_at"])).total_seconds()
                   for t in self.runs(repo, 20) if t["status"] == "success" and t.get("run_started_at"))
        return int(d[len(d) // 2]) if d else None

    def dispatch_and_wait(self, repo, log, timeout=3600):
        """Start the repo's deploy workflow on main and wait for it; returns (success, run number, url).
        While it runs, its job log is followed live (Gitea's web UI endpoint — there is no API for running jobs' logs)
        and parsed into `log`'s progress snapshot: steps, Ansible plays / tasks, per-host results, verify's checks."""
        from .progress import PipelineLog
        snap = log.pipeline(repo, status="queued", eta_s=self._safe(self.eta, repo))
        before = max((t["run_number"] for t in self.runs(repo, 5)), default=0)
        r = requests.post(f"{GITEA_URL}/api/v1/repos/lab/{repo}/actions/workflows/deploy.yml/dispatches", json={"ref": "main"}, auth=self.auth, timeout=30)
        if r.status_code != 204: raise RuntimeError(f"Gitea dispatch {repo}: {r.status_code} {r.text[:200]}")
        t0, run, web, cursors, last_poll = time.time(), None, None, {}, 0.0
        while time.time() - t0 < timeout:
            if run is None or time.time() - last_poll > 15:                       # the API: the run, and its final status
                last_poll = time.time()
                run = next((t for t in self.runs(repo, 5) if t["run_number"] > before and t["event"] == "workflow_dispatch"), None) or run
                if run and snap.get("run") is None:
                    url = f"{GITEA_WEB}/lab/{repo}/actions/runs/{run['run_number']}"
                    snap = log.pipeline(repo, run=run["run_number"], url=url, status=run["status"], started=run.get("run_started_at") or snap["started"])
                    log(f"{repo} run #{run['run_number']} started: {url}")
                if run and run["status"] not in ("running", "waiting", "queued", "blocked"): break
            if run:                                                               # the web UI endpoint: live steps and log
                try:
                    web = web or GiteaWeb(self.auth)
                    state, steps_log = web.job(repo, run["run_number"], cursors)
                    snap["status"] = state["run"]["status"]
                    old = snap["steps"]
                    snap["steps"] = [{**(old[i] if i < len(old) else {}), "name": st["summary"], "status": st["status"], "duration": st["duration"]}
                                     for i, st in enumerate(state["currentJob"]["steps"])]
                    for sl in steps_log:
                        cursors[sl["step"]] = sl["cursor"]
                        PipelineLog(snap).feed(sl["step"], [ln["message"] for ln in sl["lines"]])
                    log.pipeline(repo, run=run["run_number"])                      # save (throttled)
                    if state["run"]["done"]: run = {**run, "status": state["run"]["status"]}; break
                except Exception as e:                                            # noqa: BLE001 — live view is a bonus
                    snap["live_error"] = f"{type(e).__name__}: {e}"[:200]; web = None
            time.sleep(3)
        else:
            raise RuntimeError(f"{repo}: no result after {timeout} s")
        # the final state (one more read, so the last lines and step durations are in)
        try:
            state, steps_log = (web or GiteaWeb(self.auth)).job(repo, run["run_number"], cursors)
            snap["steps"] = [{**(snap["steps"][i] if i < len(snap["steps"]) else {}), "name": st["summary"], "status": st["status"], "duration": st["duration"]}
                             for i, st in enumerate(state["currentJob"]["steps"])]
            for sl in steps_log: PipelineLog(snap).feed(sl["step"], [ln["message"] for ln in sl["lines"]])
        except Exception:                                                         # noqa: BLE001
            pass
        ok = run["status"] == "success"
        log.pipeline(repo, run=run["run_number"], status=run["status"], finished=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        log.save(force=True)
        return ok, run["run_number"], f"{GITEA_WEB}/lab/{repo}/actions/runs/{run['run_number']}"

    @staticmethod
    def _safe(f, *a):
        try: return f(*a)
        except Exception: return None                                             # noqa: BLE001


class GiteaWeb:
    """Gitea's web UI session — for what its API does not offer: a running job's steps and log (POST
    /<owner>/<repo>/actions/runs/<run>/jobs/<job> with log cursors; the response carries only new lines)."""

    def __init__(self, auth):
        self.s = requests.Session(); self.csrf = None
        page = self.s.get(f"{GITEA_URL}/user/login", timeout=30).text
        tok = re.search(r'name="_csrf" value="([^"]+)"', page).group(1)
        r = self.s.post(f"{GITEA_URL}/user/login", data={"_csrf": tok, "user_name": auth[0], "password": auth[1]}, allow_redirects=False, timeout=30)
        if r.status_code not in (302, 303): raise RuntimeError(f"Gitea web login: {r.status_code}")

    def job(self, repo, run, cursors, job=0):
        url = f"{GITEA_URL}/lab/{repo}/actions/runs/{run}/jobs/{job}"
        if self.csrf is None:
            page = self.s.get(url, timeout=30).text
            m = re.search(r"csrfToken: '([^']+)'", page) or re.search(r'name="_csrf" content="([^"]+)"', page)
            self.csrf = m.group(1)
        head = self.s.post(url, json={"logCursors": []}, headers={"X-Csrf-Token": self.csrf}, timeout=30); head.raise_for_status()
        n = len(head.json()["state"]["currentJob"]["steps"])
        r = self.s.post(url, json={"logCursors": [{"step": i, "cursor": cursors.get(i), "expanded": True} for i in range(n)]},
                        headers={"X-Csrf-Token": self.csrf}, timeout=30); r.raise_for_status()
        d = r.json()
        return d["state"], d["logs"]["stepsLog"]

    def full_log(self, repo, run, step):
        """Every line of one step (the front end's log viewer)."""
        state, logs = self.job(repo, run, {})
        return state, next(([ln["message"] for ln in sl["lines"]] for sl in logs if sl["step"] == step), [])


# ---- phpIPAM ------------------------------------------------------------------------------------------------------------------------
class Ipam:
    def __init__(self):
        env = dict(l.split("=", 1) for l in (Path.home() / ".config/evpn-3tier/phpipam.env").read_text().split() if "=" in l)
        self.api = f"{env['PHPIPAM_URL'].rstrip('/')}/api/{env['API_APP']}"
        self.s = requests.Session(); self.s.verify = False
        self.s.headers.update({"phpipam-token": env["TOKEN"], "Content-Type": "application/json"})
        requests.packages.urllib3.disable_warnings()

    def req(self, method, path, **kw):
        r = self.s.request(method, f"{self.api}/{path.strip('/')}/", timeout=30, **kw)
        body = r.json()
        if not body.get("success") and not (method == "GET" and r.status_code == 404):
            raise RuntimeError(f"phpIPAM {method} {path}: {r.status_code} {body.get('message')}")
        return body.get("data") if body.get("success") else None
