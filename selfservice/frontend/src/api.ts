// The self-service API (Django Ninja, /api/; docs at /api/docs). Same origin: the session cookie signs requests in,
// changes carry the CSRF token the session endpoint hands out.

export type User = { username: string; name: string };
export type Session =
  | { authenticated: false; csrf: string }
  | { authenticated: true; csrf: string; user: User; is_operator: boolean; can_approve: boolean; is_staff: boolean };

export type Status = "submitted" | "approved" | "running" | "done" | "failed" | "rejected" | "cancelled";

export type Summary = {
  id: number; title: string; workflow: string; workflow_name: string; status: Status; status_label: string;
  requested_by: User; decided_by: User | null; created: string; updated: string; decided_at: string | null;
  percent: number; stage: string | null; seq: number; error?: string | null;
};

export type StageState = "pending" | "running" | "done" | "failed";
export type Stage = { key: string; label: string; status: StageState; started: string | null; finished: string | null; previous?: boolean };
export type HostCounts = { ok: number; changed: number; failed: number; skipped: number };
export type PipeStep = { name: string; status: string; duration: string; task?: string; hosts?: Record<string, HostCounts> };
export type Pipeline = {
  repo: string; run: number | null; url: string | null; status: string; steps: PipeStep[]; play: string | null; task: string | null;
  tail: string[]; started: string; finished: string | null; eta_s: number | null; attempt: number; live_error?: string;
  verify?: { passed: number; failed: number; total: number | null; failures: string[] };
};
export type Progress = {
  stages: Stage[]; pipelines: Record<string, Pipeline>; attempt: number; seq: number; started: string; finished: string | null; current: string | null;
};
export type Event = { id: number; at: string; kind: string; actor: User | null; message: string };
export type Detail = Summary & {
  parameters: Record<string, unknown>; reason: string; result: Record<string, unknown>; progress: Partial<Progress>;
  will_do: string[]; stages: { key: string; label: string }[]; events: Event[];
  can: { approve: boolean; reject: boolean; retry: boolean; cancel: boolean };
};
export type Workflow = { slug: string; name: string; summary: string; will_do: string[]; stages: { key: string; label: string }[] };
export type Field = {
  name: string; label: string; type: "text" | "textarea" | "number" | "select" | "checkbox" | "allocate" | "computed"; required: boolean; help: string;
  initial: unknown; choices: { value: string; label: string }[] | null; min: number | null; max: number | null; max_length: number | null;
  // allocate: a button that asks the server for values (endpoint), filling `fills`; computed: read-only, from a template or the allocation
  ui?: { button?: string; endpoint?: string; fills?: string[]; depends?: string[]; template?: string; from?: string };
};
export type VlanAllocation = { vlan_id: number; vni: number; subnet: string; gateway: string; name: string | null; source: { vlan: string; subnet: string } };
export type FormSpec = { slug: string; name: string; summary: string; will_do: string[]; fields: Field[] };
export type Lab = {
  source: string; lab: string; vrfs: string[]; tier_vrfs: string[]; vlans_used: number[]; vnis_used: number[]; asns_used: number[];
  subnets_used: string[]; switch_pairs: Record<string, string[]>;
  tier_vlans: { vlan: number; vrf: string; gateway: string; on: string }[];
  lbs: Record<string, { pair: string | null; switches: string[]; vrf: string | null; peering: { asn: number } | null }>;
  lb_peerings: { name: string; asn: number; vlan: number; subnet: string; vips: string[]; vrf: string }[];
};

export class ApiError extends Error {
  constructor(public status: number, message: string, public body?: any) { super(message); }
}

let csrf = "";
export const setCsrf = (t: string) => { csrf = t; };

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`/api${path}`, {
    method, credentials: "same-origin",
    headers: { Accept: "application/json", ...(body !== undefined ? { "Content-Type": "application/json" } : {}), ...(method !== "GET" ? { "X-CSRFToken": csrf } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  const data = text ? JSON.parse(text) : null;
  if (!r.ok) throw new ApiError(r.status, data?.detail ?? data?.message ?? `${r.status} ${r.statusText}`, data);
  return data as T;
}

export const api = {
  session: () => call<Session>("GET", "/session"),
  signIn: (username: string, password: string) => call<Session>("POST", "/session", { username, password }),
  signOut: () => call<Session>("DELETE", "/session"),
  lab: () => call<Lab>("GET", "/lab"),
  catalog: () => call<Workflow[]>("GET", "/catalog"),
  form: (slug: string) => call<FormSpec>("GET", `/catalog/${slug}/form`),
  allocate: (endpoint: string, params: Record<string, string>) => call<VlanAllocation>("GET", `${endpoint}?${new URLSearchParams(params)}`),
  requests: (scope: "mine" | "all" | "pending", params: { status?: string; q?: string } = {}) =>
    call<Summary[]>("GET", `/requests?scope=${scope}&${new URLSearchParams(params as Record<string, string>)}`),
  counts: () => call<Record<Status, number>>("GET", "/requests/counts"),
  request: (id: number) => call<Detail>("GET", `/requests/${id}`),
  create: (workflow: string, data: Record<string, unknown>) => call<Detail>("POST", "/requests", { workflow, data }),
  act: (id: number, action: "approve" | "reject" | "retry" | "cancel", note = "") => call<Detail>("POST", `/requests/${id}/${action}`, { note }),
  log: (id: number, repo: string, run: number, step: number) =>
    call<{ step: number; name: string; status: string; lines: string[] }>("GET", `/requests/${id}/pipelines/${repo}/${run}/log?step=${step}`),
};
