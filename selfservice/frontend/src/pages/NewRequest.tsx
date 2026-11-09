import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api, ApiError, type Field, type FormSpec } from "../api";
import { useLive } from "../live";
import { Card, ErrorNote, Icon, Spinner } from "../ui";

function Input({ f, value, onChange, error }: { f: Field; value: unknown; onChange: (v: unknown) => void; error?: string[] }) {
  const id = `f-${f.name}`;
  const common = { id, name: f.name, required: f.required, "aria-invalid": !!error, "aria-describedby": `${id}-h` };
  let control;
  if (f.type === "select") control = (
    <select {...common} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}>
      <option value="" disabled>Choose…</option>
      {f.choices?.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
    </select>);
  else if (f.type === "checkbox") return (
    <div className={`field check ${error ? "has-err" : ""}`}>
      <label><input type="checkbox" id={id} checked={!!value} onChange={(e) => onChange(e.target.checked)} /> {f.label}</label>
      {f.help && <small id={`${id}-h`}>{f.help}</small>}
      {error?.map((m) => <small key={m} className="err">{m}</small>)}
    </div>);
  else if (f.type === "textarea") control = <textarea {...common} rows={3} value={String(value ?? "")} maxLength={f.max_length ?? undefined} onChange={(e) => onChange(e.target.value)} />;
  else if (f.type === "number") control = <input {...common} type="number" min={f.min ?? undefined} max={f.max ?? undefined} value={value === null || value === undefined ? "" : String(value)} onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))} />;
  else control = <input {...common} type="text" maxLength={f.max_length ?? undefined} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />;
  return (
    <div className={`field ${error ? "has-err" : ""}`}>
      <label htmlFor={id}>{f.label}{f.required && <span className="req">*</span>}</label>
      {control}
      {f.help && <small id={`${id}-h`}>{f.help}</small>}
      {error?.map((m) => <small key={m} className="err">{m}</small>)}
    </div>
  );
}

export default function NewRequest() {
  const { slug = "" } = useParams();
  const nav = useNavigate();
  const { toast } = useLive();
  const [spec, setSpec] = useState<FormSpec | null>(null);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [loadErr, setLoadErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.form(slug).then((s) => {
      setSpec(s);
      setValues(Object.fromEntries(s.fields.map((f) => [f.name, f.initial ?? (f.type === "checkbox" ? false : "")])));
    }).catch((e) => setLoadErr(e.message));
  }, [slug]);

  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setErrors({});
    try {
      const r = await api.create(slug, values);
      toast({ tone: "info", title: `Request #${r.id} submitted`, body: "An operator approves it next." });
      nav(`/requests/${r.id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 400 && err.body?.errors) setErrors(err.body.errors);
      else setErrors({ __all__: [err instanceof Error ? err.message : String(err)] });
    } finally { setBusy(false); }
  };

  if (loadErr) return <ErrorNote>{loadErr}</ErrorNote>;
  if (!spec) return <Spinner />;
  return (
    <div className="stack">
      <div className="page-h"><div><Link to="/new" className="back">← All workflows</Link><h1>{spec.name}</h1><p className="mute">{spec.summary}</p></div></div>
      <div className="grid-form">
        <Card>
          <form onSubmit={submit} className="form" noValidate>
            {errors.__all__ && <ErrorNote>{errors.__all__.join(" ")}</ErrorNote>}
            {spec.fields.map((f) => <Input key={f.name} f={f} value={values[f.name]} error={errors[f.name]}
                                           onChange={(v) => setValues((s) => ({ ...s, [f.name]: v }))} />)}
            <div className="row gap"><button className="btn primary" disabled={busy}>{busy ? <Spinner /> : <Icon name="check" />}Submit for approval</button>
              <Link to="/new" className="btn ghost">Cancel</Link></div>
          </form>
        </Card>
        <Card title="When it is approved" className="aside-card">
          <ol className="will-do">{spec.will_do.map((w) => <li key={w}>{w}</li>)}</ol>
          <p className="small mute">The form is checked against the live lab (Nautobot): VLAN IDs, VNIs, ASNs and subnets already in use are refused.</p>
        </Card>
      </div>
    </div>
  );
}
