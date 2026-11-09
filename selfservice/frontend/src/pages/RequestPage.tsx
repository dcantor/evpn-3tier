import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { api, ApiError, type Detail, type Pipeline, type PipeStep, type Stage } from "../api";
import { useLive, useLiveData } from "../live";
import { Bar, Card, ErrorNote, Icon, Modal, Spinner, StatusPill, TimeAgo, dur, useNow, when } from "../ui";

// ---- actions ----------------------------------------------------------------------------------------------------------------
type Action = "approve" | "reject" | "retry" | "cancel";
const ACTION_LABEL: Record<Action, string> = { approve: "Approve", reject: "Reject", retry: "Retry", cancel: "Cancel request" };

function ActionButton({ id, action, onDone, compact }: { id: number; action: Action; onDone?: (d: Detail) => void; compact?: boolean }) {
  const { toast } = useLive();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const tone = action === "approve" || action === "retry" ? "primary" : action === "reject" ? "danger" : "ghost";
  const go = async () => {
    setBusy(true); setErr("");
    try {
      const d = await api.act(id, action, note);
      toast({ tone: action === "reject" || action === "cancel" ? "info" : "ok", title: `#${id}: ${d.status_label.toLowerCase()}` });
      setOpen(false); setNote(""); onDone?.(d);
    } catch (e) { setErr(e instanceof ApiError ? e.message : String(e)); } finally { setBusy(false); }
  };
  return (
    <>
      <button className={`btn ${tone} ${compact ? "sm" : ""}`} onClick={() => setOpen(true)}>
        <Icon name={action === "approve" ? "check" : action === "retry" ? "retry" : "x"} />{compact && action === "approve" ? "Approve" : ACTION_LABEL[action]}</button>
      {open && (
        <Modal title={`${ACTION_LABEL[action]} #${id}`} onClose={() => setOpen(false)}>
          <div className="modal-b">
            <p className="mute">{action === "approve" ? "The worker starts carrying it out right away: Nautobot, the pipelines, phpIPAM, the checks."
              : action === "retry" ? "Every step is idempotent: the request runs again from the top; what is already done is left as it is."
              : action === "reject" ? "The requester sees your note." : "The request will not be carried out."}</p>
            <label className="field">Note {action === "reject" ? "(why)" : "(optional)"}<textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} autoFocus /></label>
            {err && <ErrorNote>{err}</ErrorNote>}
          </div>
          <footer><button className="btn ghost" onClick={() => setOpen(false)}>Back</button>
            <button className={`btn ${tone === "ghost" ? "danger" : tone}`} onClick={go} disabled={busy}>{busy && <Spinner />}{ACTION_LABEL[action]}</button></footer>
        </Modal>
      )}
    </>
  );
}

export function Decide({ id, compact, onDone }: { id: number; compact?: boolean; onDone?: (d: Detail) => void }) {
  return <div className="row gap-s"><ActionButton id={id} action="approve" compact={compact} onDone={onDone} /><ActionButton id={id} action="reject" compact={compact} onDone={onDone} /></div>;
}

// ---- stages -----------------------------------------------------------------------------------------------------------------
function secs(a: string | null, b: string | null, now: number) {
  if (!a) return 0;
  return ((b ? new Date(b).getTime() : now) - new Date(a).getTime()) / 1000;
}

function Stepper({ stages, now }: { stages: Stage[]; now: number }) {
  return (
    <ol className="stepper">
      {stages.map((s, i) => (
        <li key={s.key} className={`st-${s.status}`}>
          <span className="st-dot">{s.status === "done" ? <Icon name="check" size={14} /> : s.status === "failed" ? <Icon name="x" size={14} /> : s.status === "running" ? <Spinner size={12} /> : i + 1}</span>
          <span className="st-l">{s.label}</span>
          <span className="st-t">{s.status === "pending" ? (s.previous ? "done before" : "") : dur(secs(s.started, s.finished, now))}</span>
        </li>
      ))}
    </ol>
  );
}

// ---- a pipeline run -------------------------------------------------------------------------------------------------------
const STEP_ICON: Record<string, string> = { success: "check", failure: "x", cancelled: "x", skipped: "chevron" };

function StepRow({ s, i, active, onLog }: { s: PipeStep; i: number; active: boolean; onLog: (i: number) => void }) {
  const hosts = s.hosts ? Object.values(s.hosts) : [];
  const changed = hosts.reduce((a, h) => a + h.changed, 0), failed = hosts.reduce((a, h) => a + h.failed, 0);
  return (
    <li className={`step ps-${s.status} ${active ? "active" : ""}`}>
      <span className="ps-i">{s.status === "running" ? <Spinner size={12} /> : STEP_ICON[s.status] ? <Icon name={STEP_ICON[s.status]} size={13} /> : <span className="ps-wait" />}</span>
      <span className="ps-n">{s.name}</span>
      {hosts.length > 0 && <span className="ps-h small">{hosts.length} hosts{changed > 0 && <span className="chg"> · {changed} changed</span>}{failed > 0 && <span className="bad-t"> · {failed} failed</span>}</span>}
      <span className="ps-d mono small mute">{s.status === "waiting" || s.status === "blocked" ? "" : s.duration}</span>
      <button className="icon-btn sm" title="Full log of this step" onClick={() => onLog(i)} disabled={s.status === "waiting"}><Icon name="term" size={14} /></button>
    </li>
  );
}

function Hosts({ step }: { step: PipeStep }) {
  const hosts = Object.entries(step.hosts ?? {}).sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }));
  if (!hosts.length) return null;
  return (
    <div className="hosts">
      <div className="small mute">Devices in “{step.name}”{step.task ? <> — last task <em>{step.task}</em></> : null}</div>
      <div className="host-grid">
        {hosts.map(([h, c]) => (
          <div key={h} className={`host ${c.failed ? "h-bad" : c.changed ? "h-chg" : "h-ok"}`} title={`ok ${c.ok} · changed ${c.changed} · failed ${c.failed} · skipped ${c.skipped}`}>
            <strong>{h}</strong>
            <span className="host-c"><span className="ok-t">{c.ok}</span>{c.changed > 0 && <span className="chg">{c.changed}</span>}{c.failed > 0 && <span className="bad-t">{c.failed}</span>}</span>
          </div>
        ))}
      </div>
      <div className="legend small mute"><span className="ok-t">■</span> ok <span className="chg">■</span> changed (or would change, in check mode) <span className="bad-t">■</span> failed</div>
    </div>
  );
}

function Verify({ v }: { v: NonNullable<Pipeline["verify"]> }) {
  const total = v.total ?? 61, done = v.passed + v.failed;
  return (
    <div className="verify">
      <div className="row between"><strong>Verify</strong><span className="mono">{v.passed} / {total} passed{v.failed > 0 && <span className="bad-t"> · {v.failed} failed</span>}</span></div>
      <div className="seg-bar"><span className="seg-ok" style={{ width: `${(100 * v.passed) / total}%` }} /><span className="seg-bad" style={{ width: `${(100 * v.failed) / total}%` }} /></div>
      {done < total && v.total === null && <div className="small mute">checking…</div>}
      {v.failures.length > 0 && <ul className="fails">{v.failures.map((f) => <li key={f}><Icon name="x" size={12} />{f}</li>)}</ul>}
    </div>
  );
}

function Terminal({ lines, live }: { lines: string[]; live: boolean }) {
  const ref = useRef<HTMLPreElement>(null);
  const stick = useRef(true);
  useEffect(() => { const el = ref.current; if (el && stick.current) el.scrollTop = el.scrollHeight; }, [lines]);
  return (
    <pre className="term" ref={ref} onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 30; }}>
      {lines.map((l, i) => {
        const [step, ...rest] = l.split("│"); const msg = rest.join("│");
        const cls = /^(fatal|failed|FAIL|ERROR|##\[error\])/i.test(msg.trim()) ? "l-bad" : /^changed:/.test(msg.trim()) ? "l-chg" : /^(ok:|PASS)/.test(msg.trim()) ? "l-ok" : /^(TASK|PLAY)/.test(msg.trim()) ? "l-head" : "";
        return <div key={i} className={cls}>{rest.length ? <><span className="l-step">{step.trim()}</span>{msg}</> : l}</div>;
      })}
      {live && <div className="cursor">▍</div>}
    </pre>
  );
}

function PipelineCard({ p, rid, now, current }: { p: Pipeline; rid: number; now: number; current: boolean }) {
  const [log, setLog] = useState<{ step: number; name: string; lines: string[] | null; err?: string } | null>(null);
  const running = !["success", "failure", "cancelled", "skipped"].includes(p.status);
  const elapsed = secs(p.started, p.finished, now);
  const activeIdx = p.steps.findIndex((s) => s.status === "running");
  const hostStep = activeIdx >= 0 && p.steps[activeIdx].hosts ? p.steps[activeIdx] : [...p.steps].reverse().find((s) => s.hosts && Object.keys(s.hosts).length);
  const done = p.steps.filter((s) => ["success", "failure", "skipped", "cancelled"].includes(s.status)).length;
  const pct = p.steps.length ? (100 * done) / p.steps.length : 3;

  const openLog = async (step: number) => {
    if (!p.run) return;
    setLog({ step, name: p.steps[step]?.name ?? "", lines: null });
    try { const r = await api.log(rid, p.repo, p.run, step); setLog({ step, name: r.name, lines: r.lines }); }
    catch (e) { setLog({ step, name: p.steps[step]?.name ?? "", lines: [], err: String((e as Error).message) }); }
  };

  return (
    <Card className={`pipe ${current ? "" : "old"}`} title={
      <span className="row gap-s"><Icon name="server" />{p.repo}{p.run && <span className="mono mute">#{p.run}</span>}<span className={`pill s-${p.status === "success" ? "done" : p.status === "failure" ? "failed" : running ? "running" : "cancelled"}`}>{running && <Spinner size={10} />}{p.status}</span></span>}
      actions={p.url && <a className="btn ghost sm" href={p.url} target="_blank" rel="noreferrer">Gitea<Icon name="ext" size={13} /></a>}>
      <div className="pipe-meta small mute">
        <span><Icon name="clock" size={13} /> {dur(elapsed)}{p.eta_s && running ? <> of about {dur(p.eta_s)}{p.eta_s > elapsed ? <> · ~{dur(p.eta_s - elapsed)} left</> : <> · running long</>}</> : null}</span>
        {running && p.play && <span>Play: <strong>{p.play}</strong></span>}
      </div>
      <Bar value={running ? pct : 100} status={p.status === "failure" ? "failed" : running ? "running" : "done"} thin />
      {running && p.task && <div className="now"><Spinner size={12} /><span>Now: <strong>{p.task}</strong></span></div>}
      {p.live_error && running && <div className="small mute">Live log unavailable for a moment ({p.live_error}); retrying.</div>}
      <ol className="steps">{p.steps.map((s, i) => <StepRow key={i} s={s} i={i} active={i === activeIdx} onLog={openLog} />)}</ol>
      {hostStep && <Hosts step={hostStep} />}
      {p.verify && <Verify v={p.verify} />}
      {p.tail.length > 0 && <details className="tail" open={running && current}><summary>Live log</summary><Terminal lines={p.tail} live={running} /></details>}
      {log && (
        <Modal title={`${p.repo} #${p.run} — ${log.name}`} onClose={() => setLog(null)} wide>
          <div className="modal-b">
            {log.err && <ErrorNote>{log.err}</ErrorNote>}
            {log.lines === null ? <Spinner /> : <Terminal lines={log.lines} live={false} />}
          </div>
        </Modal>
      )}
    </Card>
  );
}

// ---- the page ---------------------------------------------------------------------------------------------------------------
const EVENT_ICON: Record<string, string> = { submitted: "plus", approved: "check", rejected: "x", cancelled: "x", started: "clock", step: "chevron", done: "check", failed: "alert", retry: "retry" };

export default function RequestPage() {
  const id = Number(useParams().id);
  const { data: r, error, setData } = useLiveData<Detail>(() => api.request(id), (c) => c.id === id, [id]);
  const now = useNow(1000);
  const [showOld, setShowOld] = useState(false);

  const pipes = useMemo(() => Object.values(r?.progress?.pipelines ?? {}), [r]);
  const attempt = r?.progress?.attempt ?? 0;
  const current = pipes.filter((p) => p.attempt === attempt);
  const earlier = pipes.filter((p) => p.attempt !== attempt).reverse();

  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (!r) return <Spinner />;
  const stages: Stage[] = r.progress?.stages?.length ? r.progress.stages
    : r.stages.map((s) => ({ ...s, status: "pending" as const, started: null, finished: null }));
  const running = r.status === "running";
  const elapsed = r.progress?.started ? secs(r.progress.started, r.progress.finished ?? null, now) : 0;
  const lastEvent = [...r.events].reverse().find((e) => e.kind === "step");

  return (
    <div className="stack">
      <div className="page-h">
        <div>
          <Link to="/" className="back">← Overview</Link>
          <h1><span className="mono mute">#{r.id}</span> {r.title}</h1>
          <div className="row gap-s wrap"><StatusPill status={r.status} /><span className="mute small">{r.workflow_name} · requested by {r.requested_by.name} <TimeAgo iso={r.created} />
            {r.decided_by && <> · {r.status === "rejected" ? "rejected" : "approved"} by {r.decided_by.name}</>}</span></div>
        </div>
        <div className="row gap-s">
          {r.can.approve && <Decide id={r.id} onDone={setData} />}
          {r.can.retry && <ActionButton id={r.id} action="retry" onDone={setData} />}
          {r.can.cancel && <ActionButton id={r.id} action="cancel" onDone={setData} />}
        </div>
      </div>

      <Card className="hero">
        <div className="row between wrap gap">
          <div>
            <div className="hero-v">{r.percent}%</div>
            <div className="mute small">{running ? <>Working on <strong>{stages.find((s) => s.status === "running")?.label ?? "…"}</strong></>
              : r.status === "submitted" ? "Waiting for an operator to approve it" : r.status === "approved" ? "Approved — the worker picks it up in a moment"
              : r.status === "done" ? "Carried out" : r.status === "failed" ? "Stopped — see below" : r.status_label}
              {elapsed > 0 && <> · {dur(elapsed)}{attempt > 1 && <> · attempt {attempt}</>}</>}</div>
          </div>
          {running && lastEvent && <div className="last-step small"><Icon name="chevron" size={13} />{lastEvent.message}</div>}
        </div>
        <Bar value={r.percent} status={r.status} />
        <Stepper stages={stages} now={now} />
      </Card>

      {r.status === "failed" && typeof r.result?.error === "string" && <ErrorNote><strong>Failed:</strong> {r.result.error as string}
        {r.can.retry && <div className="small">Fix the cause, then retry: every step is idempotent.</div>}</ErrorNote>}

      {current.map((p) => <PipelineCard key={`${p.repo}#${p.run}`} p={p} rid={r.id} now={now} current />)}
      {earlier.length > 0 && (
        <div>
          <button className="btn ghost sm" onClick={() => setShowOld(!showOld)}><Icon name="chevron" className={showOld ? "rot" : ""} />Earlier attempts ({earlier.length} pipeline runs)</button>
          {showOld && <div className="stack mt">{earlier.map((p) => <PipelineCard key={`${p.repo}#${p.run}`} p={p} rid={r.id} now={now} current={false} />)}</div>}
        </div>
      )}

      <div className="grid-2">
        <Card title="Request">
          <dl className="facts">
            {Object.entries(r.parameters).map(([k, v]) => (
              <div key={k} className="fact"><dt>{k.replace(/_/g, " ")}</dt><dd className="mono">{Array.isArray(v) ? v.join(", ") : typeof v === "boolean" ? (v ? "yes" : "no") : String(v)}</dd></div>))}
          </dl>
          {r.reason && <><h4>Reason</h4><p className="reason">{r.reason}</p></>}
          {r.result?.vip ? <><h4>Result</h4><p>VIP <span className="mono">{String(r.result.vip)}</span></p></> : null}
        </Card>
        <Card title="History">
          <ol className="timeline">
            {[...r.events].reverse().map((e) => (
              <li key={e.id} className={`ev ev-${e.kind.replace(/\s/g, "-")}`}>
                <span className="ev-i"><Icon name={EVENT_ICON[e.kind] ?? "chevron"} size={12} /></span>
                <div><div className="small"><strong>{e.kind}</strong> <span className="mute">· {e.actor?.name ?? "worker"} · <span title={when(e.at)}>{new Date(e.at).toLocaleTimeString()}</span></span></div>
                  {e.message && <div className="ev-m">{e.kind === "failed" ? e.message.split("\n")[0] : e.message}</div>}</div>
              </li>
            ))}
          </ol>
        </Card>
      </div>
    </div>
  );
}
