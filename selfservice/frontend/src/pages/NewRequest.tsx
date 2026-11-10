import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api, ApiError, type Field, type FormSpec, type VlanAllocation } from "../api";
import { useLive } from "../live";
import { Card, ErrorNote, Icon, Spinner } from "../ui";

function fill(template: string, values: Record<string, unknown>) {
  let missing = false;
  const out = template.replace(/\{(\w+)\}/g, (_, k) => { const v = values[k]; if (v === "" || v === null || v === undefined) missing = true; return String(v ?? ""); });
  return missing ? null : out;
}

/** A field the app chooses: a button asks the server; the chosen value is shown with what came with it. */
function Allocate({ f, values, alloc, setAlloc, onFill, error }: {
  f: Field; values: Record<string, unknown>; alloc: VlanAllocation | null; setAlloc: (a: VlanAllocation | null) => void;
  onFill: (v: Record<string, unknown>) => void; error?: string[];
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const missing = (f.ui?.depends ?? []).filter((d) => !values[d]);
  const go = async () => {
    setBusy(true); setErr("");
    try {
      const a = await api.allocate(f.ui!.endpoint!, Object.fromEntries((f.ui?.depends ?? []).map((d) => [d, String(values[d] ?? "")])));
      setAlloc(a); onFill(Object.fromEntries((f.ui?.fills ?? []).map((k) => [k, (a as Record<string, unknown>)[k]])));
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const chosen = values[f.name];
  return (
    <div className={`field ${error ? "has-err" : ""}`}>
      <label>{f.label}{f.required && <span className="req">*</span>}</label>
      {chosen !== "" && chosen !== undefined && chosen !== null ? (
        <div className="alloc">
          <div className="alloc-v"><span className="alloc-big">{String(chosen)}</span>
            {alloc && <span className="small mute">L2VNI {alloc.vni} · next unused in {alloc.source.vlan}</span>}</div>
          <button type="button" className="btn ghost sm" onClick={go} disabled={busy}>{busy ? <Spinner /> : <Icon name="retry" />}Check again</button>
        </div>
      ) : (
        <button type="button" className="btn alloc-btn" onClick={go} disabled={busy || missing.length > 0}>
          {busy ? <Spinner /> : <Icon name="search" />}{f.ui?.button ?? "Allocate"}</button>
      )}
      {missing.length > 0 && !chosen && <small>Choose the {missing.join(", ").toUpperCase()} first.</small>}
      {f.help && <small>{f.help}</small>}
      {err && <small className="err">{err}</small>}
      {error?.map((m) => <small key={m} className="err">{m}</small>)}
    </div>
  );
}

function lookup(f: Field, values: Record<string, unknown>) {
  for (const k of f.ui?.keys ?? []) {
    const key = k.replace(/\{(\w+)\}/g, (_, n) => String(values[n] ?? ""));
    if (f.ui?.map && key in f.ui.map) return f.ui.map[key];
  }
  return null;
}

function Computed({ f, values, alloc, error }: { f: Field; values: Record<string, unknown>; alloc: VlanAllocation | null; error?: string[] }) {
  const v = f.ui?.map ? lookup(f, values) : f.ui?.template ? fill(f.ui.template, values) : (values[f.name] as string) || null;
  return (
    <div className={`field ${error ? "has-err" : ""}`}>
      <label>{f.label}</label>
      <div className={`computed ${v ? "" : "empty-v"}`}>{v ?? "— follows from the VLAN ID"}
        {v && f.name === "gateway" && alloc && <span className="small mute"> · subnet {alloc.subnet}, from {alloc.source.subnet}</span>}</div>
      {f.help && <small>{f.help}</small>}
      {error?.map((m) => <small key={m} className="err">{m}</small>)}
    </div>
  );
}

function Input({ f, value, onChange, error }: { f: Field; value: unknown; onChange: (v: unknown) => void; error?: string[] }) {
  const id = `f-${f.name}`;
  const common = { id, name: f.name, required: f.required, "aria-invalid": !!error, "aria-describedby": `${id}-h` };
  let control;
  if (f.type === "select") control = (
    <select {...common} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}>
      <option value="" disabled>Choose…</option>
      {f.choices?.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
    </select>);
  else if (f.type === "multiselect") {
    const sel = new Set(Array.isArray(value) ? (value as string[]) : []);
    const flip = (v: string) => { const n = new Set(sel); if (n.has(v)) n.delete(v); else n.add(v); onChange([...n]); };
    control = (
      <div className="multi" id={id} role="group" aria-describedby={`${id}-h`}>
        {f.choices?.map((c) => (
          <button type="button" key={c.value} className={`chip ${sel.has(c.value) ? "on" : ""}`} aria-pressed={sel.has(c.value)} onClick={() => flip(c.value)}>{c.label}</button>))}
      </div>);
  }
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
  const [alloc, setAlloc] = useState<VlanAllocation | null>(null);

  useEffect(() => {
    api.form(slug).then((s) => {
      setSpec(s);
      setValues(Object.fromEntries(s.fields.filter((f) => f.type !== "computed" || !(f.ui?.template || f.ui?.map))
        .map((f) => {
          // a link may pre-fill the form (?name=web-access&trunk_vlans=41,47): known fields only
          const q = new URLSearchParams(window.location.search).get(f.name);
          if (q !== null) return [f.name, f.type === "multiselect" ? q.split(",").filter(Boolean) : f.type === "checkbox" ? q === "true" || q === "on" : q];
          return [f.name, f.initial ?? (f.type === "checkbox" ? false : f.type === "multiselect" ? [] : "")];
        })));
    }).catch((e) => setLoadErr(e.message));
  }, [slug]);

  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setErrors({});
    try {
      const computedNames = new Set(spec!.fields.filter((f) => f.type === "computed" && (f.ui?.template || f.ui?.map)).map((f) => f.name));
      const r = await api.create(slug, Object.fromEntries(Object.entries(values).filter(([k]) => !computedNames.has(k))));
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
            {spec.fields.filter((f) => !f.ui?.when || values[f.ui.when.field] === f.ui.when.value).map((f) => f.type === "allocate"
              ? <Allocate key={f.name} f={f} values={values} alloc={alloc} setAlloc={setAlloc} error={errors[f.name]}
                          onFill={(v) => { setValues((s) => ({ ...s, ...v })); setErrors((e) => ({ ...e, [f.name]: [], ...Object.fromEntries(Object.keys(v).map((k) => [k, []])) })); }} />
              : f.type === "computed"
              ? <Computed key={f.name} f={f} values={values} alloc={alloc} error={errors[f.name]} />
              : <Input key={f.name} f={f} value={values[f.name]} error={errors[f.name]}
                       onChange={(v) => setValues((s) => ({ ...s, [f.name]: v }))} />)}
            <div className="row gap"><button className="btn primary" disabled={busy}>{busy ? <Spinner /> : <Icon name="check" />}Submit for approval</button>
              <Link to="/new" className="btn ghost">Cancel</Link></div>
          </form>
        </Card>
        <Card title="When it is approved" className="aside-card">
          <ol className="will-do">{spec.will_do.map((w) => <li key={w}>{w}</li>)}</ol>
          <p className="small mute">The form is checked against the live lab (Nautobot, phpIPAM) before it reaches an operator, and again when the worker carries it out.</p>
        </Card>
      </div>
    </div>
  );
}
