import { useState } from "react";
import { api, type Status } from "../api";
import { useLiveData } from "../live";
import { Card, Empty, ErrorNote, Icon, Spinner } from "../ui";
import { RequestTable } from "./Dashboard";

const FILTERS: { label: string; value: string }[] = [
  { label: "All", value: "" }, { label: "Awaiting approval", value: "submitted" }, { label: "Active", value: "approved,running" },
  { label: "Done", value: "done" }, { label: "Failed", value: "failed" }, { label: "Closed", value: "rejected,cancelled" },
];

export default function AllRequests({ pendingOnly }: { pendingOnly?: boolean }) {
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const rows = useLiveData(() => (pendingOnly ? api.requests("pending", { q }) : api.requests("all", { status, q })), () => true, [status, q, pendingOnly]);
  return (
    <div className="stack">
      <div className="page-h"><div><h1>{pendingOnly ? "Approvals" : "All requests"}</h1>
        <p className="mute">{pendingOnly ? "Submitted requests waiting for an operator's decision, oldest first." : "Every user's requests."}</p></div></div>
      <Card pad={false} title={
        <div className="filters">
          {!pendingOnly && FILTERS.map((f) => <button key={f.label} className={`chip ${status === f.value ? "on" : ""}`} onClick={() => setStatus(f.value as Status | "")}>{f.label}</button>)}
          <label className="search"><Icon name="search" /><input placeholder="Search titles" value={q} onChange={(e) => setQ(e.target.value)} /></label>
        </div>}>
        {rows.error && <div className="card-b"><ErrorNote>{rows.error}</ErrorNote></div>}
        {!rows.data && !rows.error && <div className="card-b"><Spinner /></div>}
        {rows.data?.length === 0 && <Empty title={pendingOnly ? "Nothing waiting for approval" : "No requests match"} icon={pendingOnly ? "check" : "inbox"} />}
        {rows.data && rows.data.length > 0 && <RequestTable rows={rows.data} showWho />}
      </Card>
    </div>
  );
}
