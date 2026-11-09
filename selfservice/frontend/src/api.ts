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

// ---- devices: configurations and state (read-only tools) --------------------------------------------------------------
export type Device = { name: string; role: string; role_label: string; kind: "vyos" | "linux"; mgmt_ip: string; rack: string | null; asn: number | null };
export type StateCategory = { key: string; label: string; help: string; kinds: string[]; roles: string[] | null };
export type DeviceList = { devices: Device[]; formats: { key: string; label: string }[]; state: StateCategory[] };
export type DeviceConfig = { device: string; kind: string; format: string; text: string; files: { path: string; text: string }[]; lines: number; fetched: string; ms: number };
export type IfRow = { name: string; addresses: string[]; mac: string | null; vrf: string | null; mtu: string | null; admin: string; link: string; description: string | null };
export type PathRow = { vrf: string; prefix: string; best: boolean; multipath: boolean; valid: boolean; as_path: string; origin: string | null;
  next_hop: string; peer: string | null; reason: string | null; local_as: number | null };
export type StateResult = { key: string; label: string; sections: { title: string; command: string; output: string }[]; table?: IfRow[] | null; paths?: PathRow[] | null };
export type DeviceState = { device: string; kind: string; role: string; results: StateResult[]; skipped: string[]; fetched: string; ms: number };

export const devicesApi = {
  list: () => call<DeviceList>("GET", "/devices"),
  config: (name: string, format = "commands") => call<DeviceConfig>("GET", `/devices/${name}/config?format=${format}`),
  state: (name: string, cats: string[]) => call<DeviceState>("GET", `/devices/${name}/state?cats=${cats.join(",")}`),
  zipUrl: (names: string[], format: string) => `/api/devices-configs.zip?format=${format}&names=${names.join(",")}`,
};

/** Which state categories apply to a device (the same rule as the server's). */
export function applies(c: StateCategory, d: Device) {
  if (!c.kinds.includes(d.kind)) return false;
  return !c.roles || c.roles.includes(d.kind) || c.roles.includes(d.role);
}

/** Save text as a file in the browser. */
export function saveText(filename: string, text: string) {
  const url = URL.createObjectURL(new Blob([text.endsWith("\n") ? text : text + "\n"], { type: "text/plain;charset=utf-8" }));
  const a = document.createElement("a"); a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---- fabric dashboard ----------------------------------------------------------------------------------------------------
export type Kpi = { key: string; label: string; value: string; detail: string; tone?: "ok" | "warn" | "bad" };
export type FabricDevice = { name: string; role: string; rack: string | null; model: string; manufacturer: string; platform: string | null;
  status: string | null; mgmt_ip: string; asn: number | null; ports: number };
export type FabricVrf = { name: string; l3vni: number; l3vlan: number; table: number; vlans: number[]; subnets: string[]; transit: string[];
  peerings: string[]; hosts: string[]; kind: "tier" | "external" };
export type FabricVlan = { vlan: number; name: string | null; vrf: string; vni: number; gateway: string; subnet: string; on: string[] };
export type Fabric = {
  lab: string; source: string; fetched: string; age_s: number;
  counts: Record<string, number>;
  roles: { role: string; label: string; count: number; switch: boolean; devices: string[] }[];
  models: { manufacturer: string; model: string; platform: string | null; count: number; roles: { role: string; label: string; count: number }[] }[];
  devices: FabricDevice[]; kpis: Kpi[];
  links: { a: string; a_port: string; b: string; b_port: string; gbps: number; kind: string }[];
  oversubscription: { device: string; role: string; uplinks: number; uplink_gbps: number; downlinks: number; downlink_gbps: number; ratio: number | null }[];
  links_by_kind: { kind: string; count: number; gbps: number }[];
  racks: { rack: string; devices: string[] }[];
  vrfs: FabricVrf[]; vlans: FabricVlan[]; subnet_kinds: { kind: string; count: number; detail: string }[];
  protocols: { underlay: string; overlay: string; mtu: number; max_paths: number; vrf_max_paths: number; rt_as: number; anycast_mac: string; asns: number[] };
  changes: { total: number; done: number; failed: number; open: number; success_rate: number | null; median_s: number | null;
    by_status: Record<string, number>; by_workflow: Record<string, number>; last_change: { id: number; title: string; at: string } | null };
};
export type HealthRow = { device: string; role: string; ok: boolean; error?: string; software?: string | null; uptime_s?: number | null; ms?: number;
  bgp?: { up: number; total: number; down: string[]; underlay_up: number }; bfd?: { up: number; total: number }; vnis?: number[] | null };
export type FabricHealth = { kpis: Kpi[]; devices: HealthRow[]; fetched: string; age_s: number };

export type RecIssue = { object: string; kind: "only_phpipam" | "only_nautobot" | "mismatch"; detail: string; phpipam: string | null; nautobot: string | null;
  severity: "error" | "warning" | "in_flight"; requests: number[]; in_flight: number[] };
export type RecCheck = { key: string; label: string; matched: number; total: number; errors: number; warnings: number; in_flight: number; issues: RecIssue[] };
export type Reconcile = { checks: RecCheck[]; ignored: { object: string; why: string }[]; fetched: string; age_s: number; ms: number;
  summary: { matched: number; total: number; errors: number; warnings: number; in_flight: number; in_sync: boolean };
  counts: Record<string, number> };

export const fabricApi = {
  reconcile: (refresh = false) => call<Reconcile>("GET", `/fabric/reconcile${refresh ? "?refresh=true" : ""}`),
  design: (refresh = false) => call<Fabric>("GET", `/fabric${refresh ? "?refresh=true" : ""}`),
  health: (refresh = false) => call<FabricHealth>("GET", `/fabric/health${refresh ? "?refresh=true" : ""}`),
};

// ---- databases (Tools) ---------------------------------------------------------------------------------------------------
export type AsnRow = { asn: number; kind: string; private: boolean; devices: string[]; roles: string[]; racks: string[]; router_ids: string[];
  peers: number[]; peer_devices: string[]; sessions: number; vrfs: string[]; source: string };
export type BgpSession = { a: string; a_asn: number | null; a_addr: string; b: string; b_asn: number | null; b_addr: string; vrf: string; kind: string; bfd: boolean; note: string };
export type AsnDb = { rows: AsnRow[]; sessions: BgpSession[]; fetched: string; source: string;
  summary: { asns: number; speakers: number; sessions: number; private_used: number; private_pool: number } };
export type VlanRow = { vlan: number; name: string | null; type: string; vrf: string | null; vni: number | null; subnet: string | null; gateway: string | null;
  size: number | null; where: string; hosts: string[]; tenant: string | null; status: string | null; origin: string; request: number | null; description: string; in_nautobot: boolean };
export type VlanDb = { rows: VlanRow[]; fetched: string; source: string;
  summary: { vlans: number; by_type: Record<string, number>; vrfs: number; subnets: number; self_service: number } };

export const dbApi = {
  asns: () => call<AsnDb>("GET", "/tools/asns"),
  vlans: () => call<VlanDb>("GET", "/tools/vlans"),
  asnsXlsx: "/api/tools/asns.xlsx",
  vlansXlsx: "/api/tools/vlans.xlsx",
};
