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
    </div>
  );
}
