// Tools: Applications (who owns switch ports) and Port Profiles (the presets Port Provisioning offers).
import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { ownersApi, type Application } from "../api";
import { useLive, useLiveData } from "../live";
import { Card, Empty, ErrorNote, Icon, Spinner } from "../ui";

const portTags = (ps: { port: string; enabled: boolean }[]) =>
  ps.length ? ps.map((p) => <span key={p.port} className={`tag mono ${p.enabled ? "" : "mute"}`} title={p.enabled ? "enabled" : "disabled"}>{p.port}</span>) : <span className="mute">—</span>;

export function ApplicationsPage() {
  const d = useLiveData(() => ownersApi.applications(), (c) => c.status === "done");
  const { toast } = useLive();
  const blank = { app_id: "", name: "", owner: "", email: "", description: "" };
  const [f, setF] = useState(blank);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const edit = (a: Application) => setF({ app_id: a.app_id, name: a.name, owner: a.owner?.name ?? "", email: a.owner?.email ?? "", description: a.description });
  const save = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setErr("");
    try { const a = await ownersApi.saveApplication(f); toast({ tone: "ok", title: `${a.app_id} saved`, body: `${a.name}, owner ${a.owner?.name}` }); setF(blank); d.refresh(); }
    catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  };
  const field = (k: keyof typeof f, label: string, help: string, ph = "") => (
    <div className="field"><label htmlFor={`a-${k}`}>{label}</label>
      <input id={`a-${k}`} value={f[k]} placeholder={ph} onChange={(e) => setF({ ...f, [k]: e.target.value })} /><small>{help}</small></div>);
  return (
    <div className="stack">
      <Link to="/new" className="back">← New request</Link>
      <div className="page-h"><div><h1>Applications</h1>
        <p className="mute">The applications that own switch ports: an application ID, a name and an owner team. Kept in Nautobot (tenants in e3t-applications, the owner a team); Port Provisioning asks which application a spare port belongs to and shows it on the switch.</p></div></div>
      {d.error && <ErrorNote>{d.error}</ErrorNote>}
      {!d.data && !d.error && <Spinner />}
      {d.data && <div className={d.data.can_edit ? "grid-form" : ""}>
        <Card title="Applications" pad={false}>
          {d.data.applications.length === 0 ? <Empty title="No applications yet">{d.data.can_edit ? "Add the first one." : "An operator adds them."}</Empty> : (
            <div className="table-wrap"><table className="table">
              <thead><tr><th>Application</th><th>Owner team</th><th>Ports</th>{d.data.can_edit && <th></th>}</tr></thead>
              <tbody>{d.data.applications.map((a) => (
                <tr key={a.id}>
                  <td><span className="mono"><strong>{a.app_id}</strong></span> {a.name}{a.description && <div className="small mute">{a.description}</div>}</td>
                  <td>{a.owner ? <>{a.owner.name}{a.owner.email && <div className="small mute">{a.owner.email}</div>}</> : <span className="mute">—</span>}</td>
                  <td>{portTags(a.ports)}</td>
                  {d.data!.can_edit && <td><button className="btn sm" onClick={() => edit(a)}>Edit</button></td>}
                </tr>))}</tbody>
            </table></div>)}
        </Card>
        {d.data.can_edit && (
          <Card title={f.app_id && d.data.applications.some((a) => a.app_id === f.app_id) ? `Change ${f.app_id}` : "Add an application"}>
            <form className="stack-s" onSubmit={save}>
              {field("app_id", "Application ID", "APP- and 3 to 6 digits.", "APP-1042")}
              {field("name", "Name", "As people know it.", "Backup service")}
              {field("owner", "Owner team", "A Nautobot team: created if it does not exist.", "Storage team")}
              {field("email", "Team e-mail", "Optional.", "storage@example.lab")}
              {field("description", "Description", "Optional.")}
              {err && <ErrorNote>{err}</ErrorNote>}
              <div className="row gap-s"><button className="btn primary" disabled={busy}>{busy ? <Spinner /> : <Icon name="check" />}Save</button>
                {f.app_id && <button type="button" className="btn" onClick={() => setF(blank)}>Clear</button>}</div>
              <p className="small mute">Saved straight to Nautobot (a record of who owns what, not a network change); Nautobot's change log keeps every edit.</p>
            </form>
          </Card>)}
      </div>}
    </div>
  );
}

export function ProfilesPage() {
  const d = useLiveData(() => ownersApi.profiles(), (c) => c.status === "done");
  const q = (o: Record<string, string>) => "?" + new URLSearchParams(o).toString();
  return (
    <div className="stack">
      <Link to="/new" className="back">← New request</Link>
      <div className="page-h">
        <div><h1>Port Profiles</h1>
          <p className="mute">Named presets for spare ports: access (one VLAN, untagged) or trunk (VLANs tagged, optionally one untagged). Port Provisioning offers them; changing one can re-apply it to every port that uses it. Kept in Nautobot's evpn-3tier plan.</p></div>
        <Link to="/new/port-profile?action=create" className="btn primary"><Icon name="plus" />New profile</Link>
      </div>
      {d.error && <ErrorNote>{d.error}</ErrorNote>}
      {!d.data && !d.error && <Spinner />}
      {d.data && (
        <Card title="Profiles" pad={false}>
          {d.data.profiles.length === 0 ? <Empty title="No profiles yet">Create one with a request; an operator approves it.</Empty> : (
            <div className="table-wrap"><table className="table">
              <thead><tr><th>Profile</th><th>Configuration</th><th>Ports using it</th><th></th></tr></thead>
              <tbody>{d.data.profiles.map((p) => (
                <tr key={p.name}>
                  <td className="mono"><strong>{p.name}</strong>{p.description && <div className="small mute" style={{ fontFamily: "var(--font)" }}>{p.description}</div>}</td>
                  <td><span className={`pill ${p.mode === "access" ? "t-Tier" : "k-WAN"}`}>{p.mode === "access" ? "access" : "trunk"}</span> <span className="small">{p.summary}</span></td>
                  <td>{portTags(p.ports)}</td>
                  <td className="row gap-s">
                    <Link className="btn sm" to={`/new/port-profile${q({ action: "update", name: p.name, description: p.description, mode: p.mode,
                      access_vlan: p.access_vlan ? String(p.access_vlan) : "", trunk_vlans: p.trunk_vlans.join(","), native_vlan: p.native_vlan ? String(p.native_vlan) : "", reapply: "true" })}`}>Change</Link>
                    <Link className="btn sm" to={`/new/port-profile${q({ action: "delete", name: p.name })}`}>Delete</Link>
                  </td>
                </tr>))}</tbody>
            </table></div>)}
        </Card>)}
    </div>
  );
}
