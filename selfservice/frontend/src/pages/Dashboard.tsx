import { Link, useNavigate } from "react-router";
import { api, type Summary } from "../api";
import { useLive, useLiveData } from "../live";
import { Bar, Card, Empty, ErrorNote, Icon, StatusPill, TimeAgo } from "../ui";
import { Decide } from "./RequestPage";

export function RequestTable({ rows, showWho }: { rows: Summary[]; showWho?: boolean }) {
  const nav = useNavigate();
  return (
    <div className="table-wrap">
      <table className="table">
        <thead><tr><th className="hide-s">#</th><th>Request</th>{showWho && <th className="hide-s">By</th>}<th>Status</th><th className="w-prog">Progress</th><th className="hide-s">Updated</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} onClick={() => nav(`/requests/${r.id}`)} className="click">
              <td className="mono mute hide-s">{r.id}</td>
              <td><Link to={`/requests/${r.id}`} onClick={(e) => e.stopPropagation()}>{r.title}</Link><div className="small mute hide-s">{r.workflow_name}</div></td>
              {showWho && <td className="hide-s">{r.requested_by.name}</td>}
              <td><StatusPill status={r.status} /></td>
              <td className="w-prog">{["running", "done", "failed"].includes(r.status)
                ? <div className="prog-cell"><Bar value={r.percent} status={r.status} thin /><span className="small mute">{r.status === "running" ? (r.stage ?? "") : `${r.percent}%`}</span></div>
                : <span className="small mute">—</span>}</td>
              <td className="mute small hide-s"><TimeAgo iso={r.updated} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ActiveCard({ r }: { r: Summary }) {
  return (
    <Link to={`/requests/${r.id}`} className="active-card">
      <div className="row between"><span className="mono mute">#{r.id}</span><StatusPill status={r.status} /></div>
      <strong>{r.title}</strong>
      <Bar value={r.status === "approved" ? 2 : r.percent} status={r.status} />
      <div className="row between small mute"><span>{r.status === "approved" ? "Waiting for the worker" : (r.stage ?? "Starting")}</span><span>{r.percent}%</span></div>
    </Link>
  );
}

export default function Dashboard() {
  const { session } = useLive();
  const op = session?.authenticated && session.is_operator;
  const approver = session?.authenticated && session.can_approve;
  const mine = useLiveData(() => api.requests("mine"), () => true);
  const active = useLiveData(() => api.requests(op ? "all" : "mine", { status: "approved,running" }), () => true, [op]);
  const pending = useLiveData(() => (approver ? api.requests("pending") : Promise.resolve([] as Summary[])), () => true, [approver]);
  const counts = useLiveData(() => api.counts(), () => true);
  const lab = useLiveData(() => api.lab(), () => false);

  const c = counts.data;
  const tiles = [
    { label: "Running", value: (c?.running ?? 0) + (c?.approved ?? 0), tone: "run", icon: "clock" },
    { label: "Awaiting approval", value: c?.submitted ?? 0, tone: "wait", icon: "inbox" },
    { label: "Done", value: c?.done ?? 0, tone: "ok", icon: "check" },
    { label: "Failed", value: c?.failed ?? 0, tone: "bad", icon: "alert" },
  ];

  return (
    <div className="stack">
      <div className="page-h">
        <div><h1>Overview</h1><p className="mute">{op ? "Every request on the lab" : "Your requests"}, updated live as the automation works.</p></div>
        <Link to="/new" className="btn primary"><Icon name="plus" />New request</Link>
      </div>

      <div className="tiles">
        {tiles.map((t) => (
          <div key={t.label} className={`tile k-${t.tone}`}><span className="tile-i"><Icon name={t.icon} size={18} /></span>
            <div><div className="tile-v">{c ? t.value : "–"}</div><div className="tile-l">{t.label}</div></div></div>
        ))}
      </div>

      {active.data && active.data.length > 0 && (
        <Card title={<><span className="pulse" />In progress</>}>
          <div className="active-grid">{active.data.map((r) => <ActiveCard key={r.id} r={r} />)}</div>
        </Card>
      )}

      {approver && pending.data && pending.data.length > 0 && (
        <Card title={`Waiting for approval (${pending.data.length})`} actions={<Link to="/approvals" className="btn ghost sm">All<Icon name="chevron" /></Link>}>
          <ul className="approvals">
            {pending.data.slice(0, 5).map((r) => (
              <li key={r.id}>
                <div className="grow"><Link to={`/requests/${r.id}`}><strong>{r.title}</strong></Link>
                  <div className="small mute">#{r.id} · {r.requested_by.name} · <TimeAgo iso={r.created} /></div></div>
                <Decide id={r.id} compact onDone={() => { pending.refresh(); counts.refresh(); }} />
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="grid-2">
        <Card title="My requests" actions={<Link to="/new" className="btn ghost sm"><Icon name="plus" />Request</Link>} pad={false}>
          {mine.error && <div className="card-b"><ErrorNote>{mine.error}</ErrorNote></div>}
          {mine.data && mine.data.length === 0 && <Empty title="No requests yet"><Link to="/new">Request a change to the lab</Link></Empty>}
          {mine.data && mine.data.length > 0 && <RequestTable rows={mine.data.slice(0, 12)} />}
        </Card>
        <Card title="The lab" actions={lab.data && <span className="tag">{lab.data.source.startsWith("Nautobot") ? "from Nautobot" : "from lab.conf"}</span>}>
          {lab.error && <ErrorNote>{lab.error}</ErrorNote>}
          {lab.data && (
            <dl className="facts">
              <dt>VRFs</dt><dd>{lab.data.vrfs.map((v) => <span key={v} className="tag">{v}</span>)}</dd>
              <dt>Tier VLANs</dt><dd>{lab.data.tier_vlans.map((v) => <span key={v.vlan} className="tag" title={`${v.gateway} on ${v.on}`}>{v.vlan} <span className="mute">{v.vrf}</span></span>)}</dd>
              <dt>Switch pairs</dt><dd>{Object.entries(lab.data.switch_pairs).map(([k, v]) => <div key={k} className="small">{k}: <span className="mono">{v.join(", ")}</span></div>)}</dd>
              <dt>Load balancers</dt><dd>{Object.entries(lab.data.lbs).map(([k, v]) => (
                <div key={k} className="small"><strong>{k}</strong> · VRF {v.vrf} · {v.peering ? <span className="ok-t">peering AS {v.peering.asn}</span> : <span className="mute">no BGP</span>}</div>))}</dd>
            </dl>
          )}
        </Card>
      </div>
    </div>
  );
}
