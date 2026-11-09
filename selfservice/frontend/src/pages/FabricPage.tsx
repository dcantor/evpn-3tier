// Fabric Dashboard: the fabric at a glance. The design (devices, roles, models, links, VRFs, VLANs, subnets and the KPIs
// they imply) comes from Nautobot; the live check compares the running network with it over read-only SSH.
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { fabricApi, type Fabric, type FabricHealth, type Kpi } from "../api";
import { useLiveData } from "../live";
import { Card, dur, ErrorNote, Icon, Spinner, TimeAgo } from "../ui";

const ROLE_TONE: Record<string, string> = { spine: "r-spine", leaf: "r-leaf", border: "r-border", fw: "r-fw", wan: "r-wan", lb: "r-lb",
  web: "r-web", app: "r-app", db: "r-db", ipam: "r-ipam" };
const KIND_LABEL: Record<string, string> = { fabric: "Fabric (leaf/border to spine)", access: "Access (bond legs)", wan: "WAN uplinks", sync: "Firewall state sync" };

function KpiGrid({ kpis }: { kpis: Kpi[] }) {
  return (
    <div className="kpi-grid">
      {kpis.map((k) => (
        <div key={k.key} className={`kpi ${k.tone ? `t-${k.tone}` : ""}`}>
          <div className="kpi-l">{k.tone && <span className="kpi-dot" />}{k.label}</div>
          <div className="kpi-v">{k.value}</div>
          <div className="kpi-d small mute">{k.detail}</div>
        </div>
      ))}
    </div>
  );
}

/** Three-tier drawing: spines, then leaves and borders, then what hangs off them; every cable from Nautobot. */
function Topology({ f }: { f: Fabric }) {
  const [hover, setHover] = useState<string | null>(null);
  const W = 1000, NW = 84, NH = 30;
  const layout = useMemo(() => {
    const pos: Record<string, { x: number; y: number; role: string; w: number }> = {};
    const row = (names: string[], y: number) => names.forEach((n, i) => {
      pos[n] = { x: (W / (names.length + 1)) * (i + 1), y, role: f.devices.find((d) => d.name === n)!.role, w: Math.min(NW, W / (names.length + 1) - 8) };
    });
    const by = (r: string[]) => f.devices.filter((d) => r.includes(d.role)).map((d) => d.name);
    row(by(["spine"]), 40); row(by(["leaf", "border"]), 150);
    const peerX = (n: string) => {
      const xs = f.links.filter((l) => l.a === n || l.b === n).map((l) => pos[l.a === n ? l.b : l.a]?.x).filter((x): x is number => x !== undefined);
      return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : W;
    };
    const ends = by(["fw", "wan", "lb", "web", "app", "db"]).sort((a, b) => peerX(a) - peerX(b) || a.localeCompare(b));
    row(ends, 270);
    return pos;
  }, [f]);
  const lit = (n: string) => !hover || hover === n || f.links.some((l) => (l.a === hover && l.b === n) || (l.b === hover && l.a === n));
  return (
    <div className="topo-wrap">
      <svg viewBox={`0 0 ${W} 310`} className="topo" role="img" aria-label="Fabric topology">
        {f.links.map((l, i) => {
          const a = layout[l.a], b = layout[l.b];
          if (!a || !b) return null;
          const on = !hover || l.a === hover || l.b === hover;
          const curve = a.y === b.y ? `M${a.x},${a.y + NH / 2} C${a.x},${a.y + 46} ${b.x},${b.y + 46} ${b.x},${b.y + NH / 2}` : `M${a.x},${a.y} L${b.x},${b.y}`;
          return <path key={i} d={curve} className={`lk lk-${l.kind} ${on ? "" : "dim"}`}><title>{`${l.a} ${l.a_port} ↔ ${l.b} ${l.b_port} · ${l.gbps} Gbps · ${l.kind}`}</title></path>;
        })}
        {Object.entries(layout).map(([n, p]) => (
          <g key={n} className={`nd ${ROLE_TONE[p.role] ?? ""} ${lit(n) ? "" : "dim"}`} transform={`translate(${p.x - p.w / 2},${p.y - NH / 2})`}
             onMouseEnter={() => setHover(n)} onMouseLeave={() => setHover(null)}>
            <rect width={p.w} height={NH} rx={7} />
            <text x={p.w / 2} y={NH / 2 + 4.5} textAnchor="middle">{n}</text>
          </g>
        ))}
      </svg>
      <div className="topo-legend small mute">
        {f.links_by_kind.map((k) => <span key={k.kind}><i className={`lk-sw lk-${k.kind}`} />{KIND_LABEL[k.kind] ?? k.kind} · {k.count}</span>)}
        <span>Hover a device to trace its links</span>
      </div>
    </div>
  );
}

function Health({ h, loading, onRefresh, err }: { h: FabricHealth | null; loading: boolean; onRefresh: () => void; err: string | null }) {
  return (
    <Card title={<><Icon name="gauge" />Live health vs. design</>} actions={
      <div className="row gap-s">
        {h && <span className="small mute">checked <TimeAgo iso={h.fetched} /></span>}
        <button className="btn sm" onClick={onRefresh} disabled={loading}>{loading ? <Spinner /> : <Icon name="retry" />}Check now</button>
      </div>}>
      {err && <ErrorNote>{err}</ErrorNote>}
      {!h && !err && <div className="row gap-s mute"><Spinner />Asking every device over SSH (read-only, about 10 s)…</div>}
      {h && <>
        <KpiGrid kpis={h.kpis} />
        <div className="table-wrap health-wrap">
          <table className="table">
            <thead><tr><th>Device</th><th>Software</th><th>Uptime</th><th>BGP</th><th>BFD</th><th>VNIs</th></tr></thead>
            <tbody>{h.devices.map((r) => (
              <tr key={r.device}>
                <td><span className={`role-dot ${ROLE_TONE[r.role] ?? ""}`} /><span className="mono">{r.device}</span></td>
                {!r.ok ? <td colSpan={5}><span className="pill s-failed">unreachable</span> <span className="small mute">{r.error}</span></td> : <>
                  <td className="small">{r.software}</td>
                  <td className="small mute">{r.uptime_s != null ? fmtUptime(r.uptime_s) : ""}</td>
                  <td>{r.bgp ? <span className={`pill ${r.bgp.up === r.bgp.total ? "s-done" : "s-failed"}`} title={r.bgp.down.join(", ") || "all established"}>{r.bgp.up}/{r.bgp.total}</span> : <span className="mute">—</span>}</td>
                  <td>{r.bfd ? <span className={`pill ${r.bfd.up === r.bfd.total ? "s-done" : "s-failed"}`}>{r.bfd.up}/{r.bfd.total}</span> : <span className="mute">—</span>}</td>
                  <td className="small">{r.vnis ? r.vnis.length : <span className="mute">—</span>}</td>
                </>}
              </tr>))}</tbody>
          </table>
        </div>
      </>}
    </Card>
  );
}

function fmtUptime(s: number) {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}

export default function FabricPage() {
  // the design changes when a request finishes: reload then
  const design = useLiveData(() => fabricApi.design(), (c) => c.status === "done");
  const [health, setHealth] = useState<FabricHealth | null>(null);
  const [hLoading, setHLoading] = useState(false);
  const [hErr, setHErr] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const checkHealth = async (force: boolean) => {
    setHLoading(true); setHErr(null);
    try { setHealth(await fabricApi.health(force)); } catch (e) { setHErr((e as Error).message); } finally { setHLoading(false); }
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { checkHealth(false); }, []);
  const refresh = async () => {
    setRefreshing(true);
    try { design.setData(await fabricApi.design(true)); } catch { /* the error shows on the next load */ } finally { setRefreshing(false); }
    checkHealth(true);
  };

  const f = design.data;
  const sw = new Map(health?.devices.map((r) => [r.device, r.software]) ?? []);
  return (
    <div className="stack">
      <Link to="/new" className="back">← New request</Link>
      <div className="page-h">
        <div><h1>Fabric Dashboard</h1>
          <p className="mute">The fabric at a glance — what it is built from and how the design holds up. Read from Nautobot, the source of truth the pipelines deploy from; the live check asks the devices themselves.</p></div>
        <div className="row gap-s">
          {f && <span className="small mute">Nautobot · <TimeAgo iso={f.fetched} /></span>}
          <button className="btn" onClick={refresh} disabled={refreshing}>{refreshing ? <Spinner /> : <Icon name="retry" />}Refresh</button>
        </div>
      </div>
      {design.error && <ErrorNote>{design.error}</ErrorNote>}
      {!f && !design.error && <Spinner />}
      {f && <>
        <div className="tiles fab-tiles">
          {[
            { icon: "server", tone: "run", v: f.counts.switches, l: "Switches", s: `${f.counts.spines} spine · ${f.counts.leaves} leaf · ${f.counts.borders} border` },
            { icon: "layers", tone: "ok", v: f.counts.vrfs, l: "VRFs", s: `${f.counts.tier_vrfs} tiers + outside` },
            { icon: "network", tone: "wait", v: f.counts.subnets, l: "Subnets", s: `${f.counts.vlans} tier VLAN subnets` },
            { icon: "list", tone: "run", v: f.counts.vlans, l: "Tier VLANs", s: `${f.counts.l2vnis} L2 VNIs · ${f.counts.l3vnis} L3 VNIs` },
            { icon: "server", tone: "ok", v: f.counts.devices, l: "Devices", s: `${f.models.length} models · ${f.counts.racks} racks/zones` },
            { icon: "chart", tone: "wait", v: f.counts.links, l: "Cabled links", s: `${f.counts.fabric_links} fabric · ${f.links_by_kind.find((k) => k.kind === "access")?.count ?? 0} access` },
            { icon: "term", tone: "run", v: f.counts.asns, l: "BGP ASNs", s: `RT AS ${f.protocols.rt_as}` },
            { icon: "check", tone: "ok", v: f.counts.fw_rules, l: "Firewall rules", s: `${f.counts.fw_rules_standard} standard · ${f.counts.fw_rules - f.counts.fw_rules_standard} custom` },
          ].map((t) => (
            <div key={t.l} className={`tile k-${t.tone}`}><span className="tile-i"><Icon name={t.icon} size={18} /></span>
              <div><div className="tile-v">{t.v}</div><div className="tile-l">{t.l}</div><div className="tile-s small mute">{t.s}</div></div></div>
          ))}
        </div>

        <Card title={<><Icon name="chart" />Design KPIs</>} actions={<span className="small mute">computed from the cabling and the plan in Nautobot</span>}>
          <KpiGrid kpis={f.kpis} />
        </Card>

        <Card title={<><Icon name="network" />Topology</>} actions={<span className="small mute">{f.counts.links} cables · {f.protocols.underlay} · {f.protocols.overlay}</span>}>
          <Topology f={f} />
        </Card>

        <div className="grid-2 fab-2">
          <Card title="Devices by role" pad={false}>
            <div className="table-wrap"><table className="table">
              <thead><tr><th>Role</th><th>Count</th><th>Devices</th></tr></thead>
              <tbody>{f.roles.map((r) => (
                <tr key={r.role}>
                  <td><span className={`role-dot ${ROLE_TONE[r.role] ?? ""}`} />{r.label}{r.switch && <span className="tag sw-tag">switch</span>}</td>
                  <td><strong>{r.count}</strong></td>
                  <td>{r.devices.map((d) => <span key={d} className="tag mono">{d}</span>)}</td>
                </tr>))}</tbody>
            </table></div>
          </Card>
          <Card title="Device models" pad={false}>
            <div className="models">
              {f.models.map((m) => {
                const names = f.devices.filter((d) => d.model === m.model && d.platform === m.platform).map((d) => d.name);
                const vers = [...new Set(names.map((n) => sw.get(n)).filter(Boolean))];
                return (
                  <div key={`${m.model}-${m.platform}`} className="model">
                    <div className="row"><div><strong>{m.model}</strong><div className="small mute">{m.manufacturer} · platform {m.platform}</div></div>
                      <span className="model-n">{m.count}</span></div>
                    <div className="model-bar">{m.roles.map((r) => <span key={r.role} className={ROLE_TONE[r.role]} style={{ flex: r.count }} title={`${r.label}: ${r.count}`} />)}</div>
                    <div className="small">{m.roles.map((r) => <span key={r.role} className="model-r"><span className={`role-dot ${ROLE_TONE[r.role]}`} />{r.label} {r.count}</span>)}</div>
                    <div className="small mute">{vers.length ? `Running ${vers.join(", ")}` : health ? "" : "Software version: from the live check"}</div>
                  </div>);
              })}
            </div>
          </Card>
        </div>

        <div className="grid-2 fab-2">
          <Card title="VRFs" pad={false}>
            <div className="table-wrap"><table className="table">
              <thead><tr><th>VRF</th><th>L3 VNI</th><th>VLANs</th><th>Subnets</th><th>Servers</th></tr></thead>
              <tbody>{f.vrfs.map((v) => (
                <tr key={v.name}>
                  <td><strong>{v.name}</strong><div className="small mute">{v.kind === "external" ? "external (WAN)" : "tier"} · table {v.table}</div></td>
                  <td className="mono small">{v.l3vni}</td>
                  <td>{v.vlans.length ? v.vlans.map((x) => <span key={x} className="tag mono">{x}</span>) : <span className="mute small">—</span>}</td>
                  <td className="small mono">{[...v.subnets, ...v.transit.map((t) => `${t} (transit)`)].map((s) => <div key={s}>{s}</div>)}</td>
                  <td>{v.hosts.map((h) => <span key={h} className="tag mono">{h}</span>)}{v.peerings.map((p) => <span key={p} className="tag mono" title="BGP peering">{p}</span>)}</td>
                </tr>))}</tbody>
            </table></div>
          </Card>
          <div className="stack">
            <Card title="Subnets" pad={false}>
              <table className="table"><tbody>{f.subnet_kinds.map((s) => (
                <tr key={s.kind}><td>{s.kind}<div className="small mute">{s.detail}</div></td><td className="num"><strong>{s.count}</strong></td></tr>))}
                <tr><td><strong>Total in Nautobot</strong></td><td className="num"><strong>{f.counts.subnets}</strong></td></tr></tbody></table>
            </Card>
            <Card title="Uplinks and oversubscription" pad={false}>
              <table className="table"><thead><tr><th>Switch</th><th>Up</th><th>Down</th><th>Ratio</th></tr></thead>
                <tbody>{f.oversubscription.map((r) => (
                  <tr key={r.device}><td className="mono">{r.device}</td><td className="small">{r.uplinks} × {r.uplinks ? r.uplink_gbps / r.uplinks : 0}G</td>
                    <td className="small">{r.downlinks} × {r.downlinks ? r.downlink_gbps / r.downlinks : 0}G</td>
                    <td><span className={`pill ${r.ratio !== null && r.ratio <= 3 ? "s-done" : "s-submitted"}`}>{r.ratio}:1</span></td></tr>))}</tbody></table>
            </Card>
          </div>
        </div>

        <Health h={health} loading={hLoading} err={hErr} onRefresh={() => checkHealth(true)} />

        <div className="grid-2 fab-2">
          <Card title={<><Icon name="clock" />Change KPIs</>} actions={<span className="small mute">requests made through Fabric Factory</span>}>
            <KpiGrid kpis={[
              { key: "n", label: "Changes completed", value: String(f.changes.done), detail: Object.entries(f.changes.by_workflow).map(([k, v]) => `${k} ${v}`).join(" · ") || "none yet" },
              { key: "sr", label: "Success rate", value: f.changes.success_rate != null ? `${f.changes.success_rate}%` : "—", tone: f.changes.success_rate == null ? undefined : f.changes.success_rate >= 90 ? "ok" : "warn",
                detail: `${f.changes.done} done, ${f.changes.failed} failed (of those carried out)` },
              { key: "md", label: "Median change time", value: f.changes.median_s != null ? dur(f.changes.median_s) : "—", detail: "approval to verified, pipelines and checks included" },
              { key: "op", label: "Open requests", value: String(f.changes.open), detail: "awaiting approval, queued or running" },
            ]} />
            {f.changes.last_change && <p className="small mute fab-last">Last change: <Link to={`/requests/${f.changes.last_change.id}`}>#{f.changes.last_change.id} {f.changes.last_change.title}</Link>, <TimeAgo iso={f.changes.last_change.at} /></p>}
          </Card>
          <Card title="Design parameters" pad={false}>
            <table className="table"><tbody>
              <tr><td className="mute">Underlay</td><td>{f.protocols.underlay}</td></tr>
              <tr><td className="mute">Overlay</td><td>{f.protocols.overlay}</td></tr>
              <tr><td className="mute">Fabric MTU</td><td className="mono">{f.protocols.mtu}</td></tr>
              <tr><td className="mute">ECMP paths (underlay / VRF)</td><td className="mono">{f.protocols.max_paths} / {f.protocols.vrf_max_paths}</td></tr>
              <tr><td className="mute">Anycast gateway MAC</td><td className="mono">{f.protocols.anycast_mac}</td></tr>
              <tr><td className="mute">Route-target AS</td><td className="mono">{f.protocols.rt_as}</td></tr>
              <tr><td className="mute">LB peerings / VIPs</td><td>{f.counts.lb_peerings} / {f.counts.vips}</td></tr>
            </tbody></table>
          </Card>
        </div>
      </>}
    </div>
  );
}
