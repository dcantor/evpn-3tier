import { Link } from "react-router";
import { api, type Workflow } from "../api";
import { useLiveData } from "../live";
import { Card, ErrorNote, Icon, Spinner } from "../ui";

export function StagesPreview({ stages }: { stages: { key: string; label: string }[] }) {
  return (
    <ol className="mini-steps">
      {stages.map((s, i) => <li key={s.key}><span>{i + 1}</span>{s.label}</li>)}
    </ol>
  );
}

export default function Catalog() {
  const cat = useLiveData<Workflow[]>(() => api.catalog(), () => false);
  return (
    <div className="stack">
      <div className="page-h"><div><h1>New request</h1><p className="mute">Pick what you need. Every request is checked against the live lab, approved by an operator, then carried out by the automation.</p></div></div>
      {cat.error && <ErrorNote>{cat.error}</ErrorNote>}
      {!cat.data && !cat.error && <Spinner />}
      <h2 className="section-h">Changes <span className="mute small">— approved by an operator, built by the automation</span></h2>
      <div className="wf-grid">
        {cat.data?.map((w) => (
          <Card key={w.slug} className="wf">
            <h3>{w.name}</h3>
            <p className="mute">{w.summary}</p>
            <h4>What happens</h4>
            <StagesPreview stages={w.stages} />
            <Link to={`/new/${w.slug}`} className="btn primary">Start<Icon name="chevron" /></Link>
          </Card>
        ))}
      </div>
      <h2 className="section-h">Tools <span className="mute small">— instant and read-only, no approval</span></h2>
      <div className="wf-grid">
        <Card className="wf tool">
          <span className="tool-i"><Icon name="chart" size={22} /></span>
          <h3>Fabric Dashboard</h3>
          <p className="mute">The fabric at a glance: switches and their roles, device models, VRFs, VLANs and subnets, a live topology, and KPIs from the design — ECMP width, oversubscription, redundancy, address use — with a live check of BGP, BFD and VNIs against it.</p>
          <Link to="/tools/fabric" className="btn primary">Open<Icon name="chevron" /></Link>
        </Card>
        <Card className="wf tool">
          <span className="tool-i"><Icon name="term" size={22} /></span>
          <h3>View Device Configurations</h3>
          <p className="mute">The running configuration of any switch, firewall or server, fetched live and shown right here — find in it, copy it, download it as a text file, or download several devices at once as a zip. Secrets are redacted.</p>
          <Link to="/tools/configs" className="btn primary">Open<Icon name="chevron" /></Link>
        </Card>
        <Card className="wf tool">
          <span className="tool-i"><Icon name="network" size={22} /></span>
          <h3>Retrieve Device State</h3>
          <p className="mute">Live operational state of a device: interface status, routing tables, BGP and EVPN, learned MAC addresses, ARP, BFD, VRRP, bonds and services — as tables and command output, with auto-refresh.</p>
          <Link to="/tools/state" className="btn primary">Open<Icon name="chevron" /></Link>
        </Card>
      </div>
    </div>
  );
}
