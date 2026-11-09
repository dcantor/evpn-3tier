// Tools: the ASN database and the VLAN database — read from Nautobot, searchable, downloadable as Excel.
import { useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { dbApi, type AsnDb, type VlanDb } from "../api";
import { useLiveData } from "../live";
import { Card, ErrorNote, Icon, Spinner, TimeAgo } from "../ui";

function Frame({ title, intro, fetched, xlsx, tiles, children, error, loading }: {
  title: string; intro: string; fetched?: string; xlsx: string; tiles?: { v: ReactNode; l: string; s?: string; icon: string; tone: string }[];
  children: ReactNode; error: string | null; loading: boolean;
}) {
  return (
    <div className="stack">
      <Link to="/new" className="back">← New request</Link>
      <div className="page-h">
        <div><h1>{title}</h1><p className="mute">{intro}</p></div>
        <div className="row gap-s">
          {fetched && <span className="small mute">Nautobot · <TimeAgo iso={fetched} /></span>}
          <a className="btn primary" href={xlsx} download><Icon name="download" />Download Excel</a>
        </div>
      </div>
      {error && <ErrorNote>{error}</ErrorNote>}
      {loading && !error && <Spinner />}
      {tiles && <div className="tiles">{tiles.map((t) => (
        <div key={t.l} className={`tile k-${t.tone}`}><span className="tile-i"><Icon name={t.icon} size={18} /></span>
          <div><div className="tile-v">{t.v}</div><div className="tile-l">{t.l}</div>{t.s && <div className="tile-s small mute">{t.s}</div>}</div></div>))}</div>}
      {children}
    </div>
  );
}

function Filters({ q, setQ, chips, on, setOn, placeholder, count }: {
  q: string; setQ: (s: string) => void; chips: string[]; on: string; setOn: (s: string) => void; placeholder: string; count: string;
}) {
  return (
    <div className="row gap-s wrap db-bar">
      <button className={`chip ${on === "" ? "on" : ""}`} onClick={() => setOn("")}>All</button>
      {chips.map((c) => <button key={c} className={`chip ${on === c ? "on" : ""}`} onClick={() => setOn(c)}>{c}</button>)}
      <label className="search auto"><Icon name="search" /><input placeholder={placeholder} value={q} onChange={(e) => setQ(e.target.value)} /></label>
      <span className="small mute">{count}</span>
    </div>
  );
}

const tags = (xs: (string | number)[], mono = true) => xs.length ? xs.map((x) => <span key={String(x)} className={`tag ${mono ? "mono" : ""}`}>{x}</span>) : <span className="mute">—</span>;
const match = (q: string, ...xs: unknown[]) => !q || xs.flat().join(" ").toLowerCase().includes(q.toLowerCase());

export function AsnPage() {
  const d = useLiveData<AsnDb>(() => dbApi.asns(), () => false);
  const [q, setQ] = useState(""), [kind, setKind] = useState("");
  const [sq, setSq] = useState(""), [skind, setSkind] = useState("");
  const rows = useMemo(() => (d.data?.rows ?? []).filter((r) => (!kind || r.kind === kind) && match(q, r.asn, r.devices, r.roles, r.racks, r.router_ids, r.peers, r.vrfs)), [d.data, q, kind]);
  const sess = useMemo(() => (d.data?.sessions ?? []).filter((x) => (!skind || x.kind === skind) && match(sq, x.a, x.b, x.a_asn, x.b_asn, x.a_addr, x.b_addr, x.vrf, x.note)), [d.data, sq, skind]);
  const s = d.data?.summary;
  return (
    <Frame title="ASN Database" fetched={d.data?.fetched} xlsx={dbApi.asnsXlsx} error={d.error} loading={!d.data}
      intro="Every BGP autonomous system on the fabric — the switches, the firewalls, the WAN and the load balancers — with the devices that use it, their router IDs and who they peer with. From Nautobot, the plan the pipelines deploy from."
      tiles={s && [
        { v: s.asns, l: "ASNs", s: `${s.speakers} BGP speakers + the route-target AS`, icon: "network", tone: "run" },
        { v: s.sessions, l: "BGP sessions (design)", s: "underlay + EVPN, firewalls, WAN, LB peerings", icon: "layers", tone: "ok" },
        { v: d.data!.rows.filter((r) => r.kind === "Firewall" || r.kind === "Load balancer").length, l: "Firewall and LB ASNs", s: "firewall pair, load balancer peerings", icon: "server", tone: "wait" },
        { v: `${s.private_used}/${s.private_pool}`, l: "Private ASNs used", s: "64512-65534", icon: "chart", tone: "run" },
      ]}>
      {d.data && <>
        <Card title="ASNs" pad={false}>
          <div className="card-b"><Filters q={q} setQ={setQ} chips={[...new Set(d.data.rows.map((r) => r.kind))]} on={kind} setOn={setKind}
            placeholder="ASN, device, rack, router ID, VRF" count={`${rows.length} of ${d.data.rows.length}`} /></div>
          <div className="table-wrap"><table className="table db-table">
            <thead><tr><th>ASN</th><th>Kind</th><th>Devices</th><th>Router IDs</th><th>Peers with</th><th>Sessions</th><th>VRFs</th></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.asn}>
                <td className="mono"><strong>{r.asn}</strong>{r.private && <div className="small mute">private</div>}</td>
                <td><span className={`pill k-${r.kind.replace(/\s/g, "")}`}>{r.kind}</span><div className="small mute">{r.roles.join(", ")}</div></td>
                <td>{tags(r.devices)}{r.racks.length > 0 && <div className="small mute">rack {r.racks.join(", ")}</div>}</td>
                <td className="mono small">{r.router_ids.join(", ") || <span className="mute">—</span>}</td>
                <td>{tags(r.peers)}{r.kind === "Route target" && <div className="small mute">not a BGP speaker: the AS of every EVPN route target</div>}</td>
                <td className="mono">{r.sessions || <span className="mute">—</span>}</td>
                <td>{tags(r.vrfs, false)}</td>
              </tr>))}</tbody>
          </table></div>
        </Card>
        <Card title="BGP sessions" pad={false}>
          <div className="card-b"><Filters q={sq} setQ={setSq} chips={[...new Set(d.data.sessions.map((x) => x.kind))]} on={skind} setOn={setSkind}
            placeholder="device, ASN, address, VRF" count={`${sess.length} of ${d.data.sessions.length}`} /></div>
          <div className="table-wrap"><table className="table db-table">
            <thead><tr><th>Device A</th><th>Address A</th><th>Device B</th><th>Address B</th><th>VRF</th><th>Kind</th><th>BFD</th><th>Note</th></tr></thead>
            <tbody>{sess.map((x, i) => (
              <tr key={i}>
                <td><span className="mono">{x.a}</span> <span className="small mute">AS {x.a_asn}</span></td><td className="mono small">{x.a_addr}</td>
                <td><span className="mono">{x.b}</span> <span className="small mute">AS {x.b_asn}</span></td><td className="mono small">{x.b_addr}</td>
                <td><span className="tag">{x.vrf}</span></td><td className="small">{x.kind}</td>
                <td>{x.bfd ? <span className="pill s-done">BFD</span> : <span className="mute">—</span>}</td><td className="small mute">{x.note}</td>
              </tr>))}</tbody>
          </table></div>
        </Card>
      </>}
    </Frame>
  );
}

export function VlanPage() {
  const d = useLiveData<VlanDb>(() => dbApi.vlans(), (c) => c.status === "done");
  const [q, setQ] = useState(""), [type, setType] = useState(""), [vrf, setVrf] = useState("");
  const rows = useMemo(() => (d.data?.rows ?? []).filter((r) => (!type || r.type === type) && (!vrf || r.vrf === vrf)
    && match(q, r.vlan, r.name, r.vrf, r.vni, r.subnet, r.gateway, r.where, r.hosts, r.origin, r.description)), [d.data, q, type, vrf]);
  const s = d.data?.summary;
  return (
    <Frame title="VLAN Database" fetched={d.data?.fetched} xlsx={dbApi.vlansXlsx} error={d.error} loading={!d.data}
      intro="Every VLAN of the fabric — tier VLANs, firewall transit, load balancer peering and the L3VNI VLANs — with its subnet, gateway, VRF, VNI, where it lives and what is in it. From Nautobot, the plan the pipelines deploy from."
      tiles={s && [
        { v: s.vlans, l: "VLANs", s: Object.entries(s.by_type).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(" · "), icon: "list", tone: "run" },
        { v: s.subnets, l: "Subnets", s: "every VLAN but the L3VNI ones", icon: "network", tone: "ok" },
        { v: s.vrfs, l: "VRFs", s: "web, app, db, outside", icon: "layers", tone: "wait" },
        { v: s.self_service, l: "From self-service requests", s: "the rest is the lab's design", icon: "check", tone: "ok" },
      ]}>
      {d.data && (
        <Card title="VLANs" pad={false}>
          <div className="card-b stack-s">
            <Filters q={q} setQ={setQ} chips={[...new Set(d.data.rows.map((r) => r.type))]} on={type} setOn={setType}
              placeholder="VLAN, name, subnet, host, request" count={`${rows.length} of ${d.data.rows.length}`} />
            <div className="row gap-s wrap"><span className="small mute">VRF</span>
              <button className={`chip ${vrf === "" ? "on" : ""}`} onClick={() => setVrf("")}>any</button>
              {[...new Set(d.data.rows.map((r) => r.vrf).filter(Boolean))].map((v) => <button key={v} className={`chip ${vrf === v ? "on" : ""}`} onClick={() => setVrf(v!)}>{v}</button>)}
            </div>
          </div>
          <div className="table-wrap"><table className="table db-table">
            <thead><tr><th>VLAN</th><th>Name</th><th>Type</th><th>VRF</th><th>VNI</th><th>Subnet</th><th>Gateway</th><th>Where</th><th>Hosts / members</th><th>Origin</th></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.vlan}>
                <td className="mono"><strong>{r.vlan}</strong></td>
                <td className="mono small">{r.name ?? <span className="mute">—</span>}</td>
                <td><span className={`pill t-${r.type.replace(/\W/g, "")}`}>{r.type}</span></td>
                <td>{r.vrf ? <span className="tag">{r.vrf}</span> : <span className="mute">—</span>}</td>
                <td className="mono small">{r.vni ?? "—"}</td>
                <td className="mono small">{r.subnet ?? <span className="mute">—</span>}{r.size != null && <div className="mute">{r.size} usable</div>}</td>
                <td className="mono small">{r.gateway ?? <span className="mute">—</span>}</td>
                <td className="small">{r.where}</td>
                <td>{tags(r.hosts)}</td>
                <td className="small">{r.request ? <Link to={`/requests/${r.request}`}>{r.origin}</Link> : <span className="mute">{r.origin}</span>}</td>
              </tr>))}</tbody>
          </table></div>
        </Card>
      )}
    </Frame>
  );
}
