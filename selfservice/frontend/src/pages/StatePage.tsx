import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { applies, devicesApi, saveText, type Device, type DeviceState, type IfRow, type PathRow, type StateCategory, type StateResult } from "../api";
import { Card, Empty, ErrorNote, Icon, Spinner, TimeAgo } from "../ui";
import DevicePicker from "./DevicePicker";

function IfTable({ rows }: { rows: IfRow[] }) {
  const up = rows.filter((r) => r.admin === "up" && r.link === "up").length;
  return (
    <>
      <div className="small mute if-sum"><span className="ok-t">● {up} up</span>{rows.length - up > 0 && <span className="bad-t">● {rows.length - up} down</span>}<span>{rows.length} interfaces</span></div>
      <div className="table-wrap">
        <table className="table if-table">
          <thead><tr><th>Interface</th><th>State</th><th>Addresses</th><th>MAC</th><th>VRF</th><th>Description</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.name}>
              <td className="mono">{r.name}</td>
              <td><span className={`pill ${r.admin !== "up" ? "s-cancelled" : r.link === "up" ? "s-done" : "s-failed"}`}>{r.admin !== "up" ? "admin down" : r.link === "up" ? "up" : "link down"}</span></td>
              <td className="mono small">{r.addresses.join(", ") || <span className="mute">—</span>}</td>
              <td className="mono small mute">{r.mac ?? ""}</td>
              <td className="small">{r.vrf && r.vrf !== "default" ? <span className="tag">{r.vrf}</span> : <span className="mute">{r.vrf ?? ""}</span>}</td>
              <td className="small">{r.description ?? ""}</td>
            </tr>))}</tbody>
        </table>
      </div>
    </>
  );
}

/** BGP paths: one row per path, the AS path as a chain of ASN chips; filter by VRF, best only, text. */
function PathTable({ rows }: { rows: PathRow[] }) {
  const vrfs = [...new Set(rows.map((r) => r.vrf))];
  const [vrf, setVrf] = useState("");
  const [bestOnly, setBestOnly] = useState(false);
  const [q, setQ] = useState("");
  const shown = rows.filter((r) => (!vrf || r.vrf === vrf) && (!bestOnly || r.best) &&
    (!q || `${r.prefix} ${r.as_path} ${r.next_hop}`.toLowerCase().includes(q.toLowerCase())));
  const longest = Math.max(0, ...rows.map((r) => (r.as_path ? r.as_path.split(" ").length : 0)));
  return (
    <>
      <div className="row gap-s wrap path-bar">
        <button className={`chip ${vrf === "" ? "on" : ""}`} onClick={() => setVrf("")}>All VRFs</button>
        {vrfs.map((v) => <button key={v} className={`chip ${vrf === v ? "on" : ""}`} onClick={() => setVrf(v)}>{v}</button>)}
        <label className="small row gap-s auto"><input type="checkbox" checked={bestOnly} onChange={(e) => setBestOnly(e.target.checked)} />Best paths only</label>
        <label className="search"><Icon name="search" /><input placeholder="Prefix, ASN or next hop" value={q} onChange={(e) => setQ(e.target.value)} /></label>
        <span className="small mute">{shown.length} of {rows.length} paths · longest AS path {longest}</span>
      </div>
      <div className="table-wrap path-wrap">
        <table className="table path-table">
          <thead><tr><th>VRF</th><th>Prefix</th><th></th><th>AS path</th><th>Next hop</th><th>Origin</th></tr></thead>
          <tbody>{shown.map((r, i) => (
            <tr key={i} className={r.best ? "best" : ""}>
              <td className="small">{r.vrf}</td>
              <td className="mono">{r.prefix}</td>
              <td>{r.best ? <span className="pill s-done" title={r.reason ? `best: ${r.reason}` : "best"}>best</span> : r.multipath ? <span className="pill s-running">multipath</span> : !r.valid ? <span className="pill s-failed">invalid</span> : null}</td>
              <td>{r.as_path ? <span className="aspath">{r.as_path.split(" ").map((a, j) => <span key={j} className={`asn ${String(r.local_as) === a ? "own" : ""}`}>{a}</span>)}</span>
                : <span className="mute small">local (this router)</span>}</td>
              <td className="mono small">{r.next_hop}</td>
              <td className="small mute">{r.origin}</td>
            </tr>))}</tbody>
        </table>
      </div>
    </>
  );
}

function Output({ text, filter }: { text: string; filter: string }) {
  const f = filter.toLowerCase();
  const lines = text.split("\n");
  const shown = f ? lines.filter((l, i) => i < 2 || l.toLowerCase().includes(f)) : lines;
  return <pre className="term state-out">{shown.join("\n") || "(no output)"}</pre>;
}

function ResultCard({ r, device }: { r: StateResult; device: string }) {
  const [filter, setFilter] = useState("");
  const [raw, setRaw] = useState(false);
  const text = r.sections.map((s) => `### ${s.title}  (${s.command})\n${s.output}`).join("\n\n");
  return (
    <Card title={r.label} pad={false} actions={
      <div className="row gap-s">
        {(r.table || r.paths) && <button className="btn ghost sm" onClick={() => setRaw(!raw)}>{raw ? "Table" : "Raw output"}</button>}
        {(!(r.table || r.paths) || raw) && <label className="search"><Icon name="search" /><input placeholder="Filter lines" value={filter} onChange={(e) => setFilter(e.target.value)} /></label>}
        <button className="btn ghost sm" onClick={() => saveText(`${device}-${r.key}.txt`, text)} title="Download"><Icon name="ext" />.txt</button>
      </div>}>
      <div className="card-b stack-s">
        {r.table && !raw ? <IfTable rows={r.table} /> : r.paths && !raw ? <PathTable rows={r.paths} /> : r.sections.map((s) => (
          <div key={s.title}>
            <div className="sec-h"><strong>{s.title}</strong><code className="small mute">{s.command}</code></div>
            <Output text={s.output} filter={filter} />
          </div>
        ))}
      </div>
    </Card>
  );
}

export default function StatePage() {
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [cats, setCats] = useState<StateCategory[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [want, setWant] = useState<Set<string>>(new Set(["interfaces", "routes", "bgp", "mac"]));
  const [st, setSt] = useState<DeviceState | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [auto, setAuto] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => { devicesApi.list().then((d) => { setDevices(d.devices); setCats(d.state); }).catch((e) => setErr(e.message)); }, []);
  const dev = devices?.find((d) => d.name === sel) ?? null;
  const avail = dev ? cats.filter((c) => applies(c, dev)) : cats;
  const chosen = avail.filter((c) => want.has(c.key)).map((c) => c.key);

  const fetchState = async () => {
    if (!dev || !chosen.length) return;
    setBusy(true); setErr("");
    try { setSt(await devicesApi.state(dev.name, chosen)); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  useEffect(() => { setSt(null); if (dev) fetchState(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [sel]);
  useEffect(() => {
    window.clearInterval(timer.current);
    if (auto && dev) timer.current = window.setInterval(() => fetchState(), 15000);
    return () => window.clearInterval(timer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auto, sel, chosen.join(",")]);
  const toggle = (k: string) => { const s = new Set(want); s.has(k) ? s.delete(k) : s.add(k); setWant(s); };
  const all = st ? st.results.map((r) => r.sections.map((s) => `##### ${r.label} — ${s.title}  (${s.command})\n${s.output}`).join("\n\n")).join("\n\n") : "";

  return (
    <div className="stack">
      <div className="page-h">
        <div><Link to="/new" className="back">← New request</Link><h1>Retrieve device state</h1>
          <p className="mute">Live operational state: interfaces, routing tables, BGP, EVPN, learned MACs, ARP, BFD, VRRP… Read-only — no approval needed.</p></div>
      </div>
      <div className="split">
        <Card title="Devices" pad={false}>
          {!devices && !err && <div className="card-b"><Spinner /></div>}
          {devices && <DevicePicker devices={devices} selected={sel} onSelect={setSel} />}
        </Card>
        <div className="stack">
          <Card title={dev ? <span className="row gap-s"><Icon name="server" />{dev.name}<span className="tag">{dev.role_label}</span><span className="mono small mute">{dev.mgmt_ip}</span></span> : "What to retrieve"}
            actions={dev && (
              <div className="row gap-s">
                <label className="small row gap-s auto"><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />Auto-refresh (15 s)</label>
                {st && <button className="btn ghost sm" onClick={() => saveText(`${dev.name}-state.txt`, all)}><Icon name="ext" />All as .txt</button>}
                <button className="btn primary sm" onClick={fetchState} disabled={busy || !chosen.length}>{busy ? <Spinner /> : <Icon name="retry" />}Retrieve</button>
              </div>)}>
            <div className="cats">
              {avail.map((c) => (
                <button key={c.key} className={`chip ${want.has(c.key) ? "on" : ""}`} title={c.help} onClick={() => toggle(c.key)}>
                  {want.has(c.key) && <Icon name="check" size={12} />}{c.label}</button>))}
            </div>
            {!dev && <p className="small mute">Pick a device: the categories that apply to it stay (VyOS switches and firewalls, or the Alpine servers).</p>}
            {st && <p className="small mute">Fetched <TimeAgo iso={st.fetched} /> in {(st.ms / 1000).toFixed(1)} s{auto && " · refreshing every 15 s"}.</p>}
          </Card>
          {err && <ErrorNote>{err}</ErrorNote>}
          {!dev && <Card><Empty icon="server" title="Pick a device">Then choose what to look at and press Retrieve.</Empty></Card>}
          {dev && busy && !st && <Card><div className="row gap-s"><Spinner />Asking {dev.name}…</div></Card>}
          <div className={busy && st ? "dim stack" : "stack"}>{st?.results.map((r) => <ResultCard key={r.key} r={r} device={st.device} />)}</div>
        </div>
      </div>
    </div>
  );
}
