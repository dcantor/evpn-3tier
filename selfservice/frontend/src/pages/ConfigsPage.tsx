import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { devicesApi, saveText, type Device, type DeviceConfig } from "../api";
import { Card, Empty, ErrorNote, Icon, Spinner, TimeAgo } from "../ui";
import DevicePicker from "./DevicePicker";

function CodeView({ text, filter }: { text: string; filter: string }) {
  const lines = text.split("\n");
  const f = filter.toLowerCase();
  const shown = lines.map((l, i) => [i + 1, l] as const).filter(([, l]) => !f || l.toLowerCase().includes(f));
  return (
    <div className="code">
      {shown.length === 0 && <div className="mute small pad">No line matches “{filter}”.</div>}
      {shown.map(([n, l]) => (
        <div key={n} className={`cl ${l.includes("<redacted>") ? "red" : ""} ${l.startsWith("# =====") ? "file" : ""}`}>
          <span className="ln">{n}</span>
          <span className="lt">{f ? highlight(l, f) : l || " "}</span>
        </div>
      ))}
    </div>
  );
}
function highlight(l: string, f: string) {
  const out = []; let i = 0; const low = l.toLowerCase();
  for (let j = low.indexOf(f); j >= 0; j = low.indexOf(f, i)) { out.push(l.slice(i, j), <mark key={j}>{l.slice(j, j + f.length)}</mark>); i = j + f.length; }
  out.push(l.slice(i)); return out;
}

export default function ConfigsPage() {
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [formats, setFormats] = useState<{ key: string; label: string }[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [fmt, setFmt] = useState("commands");
  const [file, setFile] = useState(0);
  const [cfg, setCfg] = useState<DeviceConfig | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [filter, setFilter] = useState("");
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [copied, setCopied] = useState(false);

  useEffect(() => { devicesApi.list().then((d) => { setDevices(d.devices); setFormats(d.formats); }).catch((e) => setErr(e.message)); }, []);
  const dev = devices?.find((d) => d.name === sel) ?? null;

  const load = async (name: string, format = fmt) => {
    setBusy(true); setErr(""); setFile(0);
    try { setCfg(await devicesApi.config(name, format)); } catch (e) { setErr((e as Error).message); setCfg(null); } finally { setBusy(false); }
  };
  useEffect(() => { if (sel) load(sel); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [sel, fmt]);

  const text = useMemo(() => (cfg ? (cfg.kind === "linux" ? cfg.files[file]?.text ?? "" : cfg.text) : ""), [cfg, file]);
  const fname = cfg ? (cfg.kind === "vyos" ? `${cfg.device}${fmt === "tree" ? ".tree" : ""}.txt` : `${cfg.device}${cfg.files[file]?.path.replace(/\//g, "_") ?? ""}.txt`) : "";
  const copy = async () => { try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* insecure origin */ } };

  return (
    <div className="stack">
      <div className="page-h">
        <div><Link to="/new" className="back">← New request</Link><h1>View device configurations</h1>
          <p className="mute">The running configuration of any device in the fabric, fetched live. Secrets are redacted. Read-only — no approval needed.</p></div>
        <div className="row gap-s wrap">
          <select className="fmt" value={fmt} onChange={(e) => setFmt(e.target.value)} aria-label="VyOS format">
            {formats.map((f) => <option key={f.key} value={f.key}>VyOS: {f.label}</option>)}
          </select>
          <a className={`btn ${checked.size ? "primary" : "ghost"}`} href={devicesApi.zipUrl([...checked], fmt)} aria-disabled={!checked.size}
             onClick={(e) => { if (!checked.size) e.preventDefault(); }}><Icon name="ext" />Download selected ({checked.size})</a>
          <a className="btn" href={devicesApi.zipUrl([], fmt)}><Icon name="ext" />Download all (.zip)</a>
        </div>
      </div>
      <div className="split">
        <Card title="Devices" pad={false} actions={devices && <button className="btn ghost sm" onClick={() => setChecked(checked.size ? new Set() : new Set(devices.map((d) => d.name)))}>{checked.size ? "Clear" : "Select all"}</button>}>
          {!devices && !err && <div className="card-b"><Spinner /></div>}
          {devices && <DevicePicker devices={devices} selected={sel} onSelect={setSel} checked={checked} onCheck={setChecked} />}
        </Card>
        <Card pad={false} className="viewer" title={dev ? <span className="row gap-s"><Icon name="server" />{dev.name}<span className="tag">{dev.role_label}</span><span className="mono small mute">{dev.mgmt_ip}</span></span> : "Configuration"}
          actions={cfg && dev && (
            <div className="row gap-s">
              <button className="btn ghost sm" onClick={() => load(dev.name)} disabled={busy}>{busy ? <Spinner /> : <Icon name="retry" />}Refresh</button>
              <button className="btn ghost sm" onClick={copy}><Icon name={copied ? "check" : "list"} />{copied ? "Copied" : "Copy"}</button>
              <button className="btn primary sm" onClick={() => saveText(fname, text)}><Icon name="ext" />Download .txt</button>
            </div>)}>
          {!sel && <Empty icon="server" title="Pick a device">Its configuration appears here. Tick devices to download several at once.</Empty>}
          {err && <div className="card-b"><ErrorNote>{err}</ErrorNote></div>}
          {sel && busy && !cfg && <div className="card-b row gap-s"><Spinner /> Fetching {sel}…</div>}
          {cfg && (
            <>
              <div className="viewer-bar">
                {cfg.kind === "linux" && <div className="tabs">{cfg.files.map((f, i) => <button key={f.path} className={`chip ${i === file ? "on" : ""}`} onClick={() => setFile(i)}>{f.path}</button>)}</div>}
                <label className="search"><Icon name="search" /><input placeholder="Find in configuration" value={filter} onChange={(e) => setFilter(e.target.value)} /></label>
                <span className="small mute">{text.split("\n").length} lines · fetched <TimeAgo iso={cfg.fetched} /> in {(cfg.ms / 1000).toFixed(1)} s</span>
              </div>
              <div className={busy ? "dim" : ""}><CodeView text={text} filter={filter} /></div>
            </>
          )}
        </Card>
      </div>
    </div>
  );
}
