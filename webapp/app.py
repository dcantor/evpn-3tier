#!/usr/bin/env python3
"""The evpn-3tier portal: the three-tier application on the EVPN fabric, live — the path a request takes right now
(load balancer, web, firewall, app, database), the nodes, the firewall pair, the load balancers, the database roles,
the firewall policy — and the runs: verify, the failure scenarios, the firewall failover, the Robot suites.

A sampler thread refreshes the state every 5 s (SSH to the nodes, in parallel; skipped for VMs that are off) and every
viewer reads the same snapshot. Runs go through the shared run engine (labportal): one at a time, streamed, kept in
runs/<id>.json, resumable. /shop/ is the Order Desk itself, relayed through wan (tools/vyos_files.py: the host has no
route into the tier VRFs, so wan forwards from the test clients' address, through the firewall, to the web VIP).
Sign-in is the Lab Hub's (labportal.gate). Start with ./lab.sh webapp (port 8119)."""
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from labportal import RunBase, RunRegistry, exposition, gate, install_runs_api, metric_line as line, metrics_generated, run_metrics
from pydantic import BaseModel, Field

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB / "tools"))
from labssh import inventory, run as ssh  # noqa: E402

NAME = "evpn-3tier"
PORT = int(os.environ.get("WEBAPP_PORT", "8119"))
HERE = Path(__file__).resolve().parent
RUNS_DIR = HERE / "runs"; RUNS_DIR.mkdir(exist_ok=True)
STATIC = HERE / "static"
VERSION = (LAB / "VERSION").read_text().strip()
SUITES = sorted(p.stem for p in (LAB / "tests" / "suites").glob("*.robot"))
SCENARIOS = ("lb", "web", "app", "db", "leaf", "border", "fw")
INV = inventory(); N = {n["name"]: n for n in INV["nodes"]}
VYOS = ("spine", "leaf", "border", "fw", "wan")
SHOP = f"https://{N['wan']['mgmt_ip']}:8443"               # the wan relay to the web VIP (tools/vyos_files.py SHOP_RELAY)
OP = "/opt/vyatta/bin/vyatta-op-cmd-wrapper"

app = FastAPI(title="evpn-3tier portal API", version="1.0", docs_url="/docs",
              description="A three-tier application on an EVPN/VXLAN fabric: live state, failure scenarios, tests. UI: [/](/)")
gate.install(app, open_paths={"/metrics", "/api/sd"}, open_from=["10.0.0.0/24"])
registry = RunRegistry(RUNS_DIR)


def lab_sh(*args, timeout=60):
    env = {k: v for k, v in os.environ.items() if k != "FORCE_COLOR"}
    r = subprocess.run([str(LAB / "lab.sh"), *args], capture_output=True, text=True, timeout=timeout, env=env)
    return r.returncode, r.stdout


def vm_states():
    """{node: running?} from libvirt in one call (lab.sh status asks once per node)."""
    r = subprocess.run(["virsh", "-q", "-c", "qemu:///system", "list", "--all"], capture_output=True, text=True, timeout=20)
    up = {l.split()[1] for l in r.stdout.splitlines() if len(l.split()) >= 3 and l.split()[2] == "running"}
    return {n["name"]: n["domain"] in up for n in INV["nodes"]}


# ---- the sampler -------------------------------------------------------------------------------------------------------
def j(node, cmd):
    rc, o = ssh(node, f"vtysh -c '{cmd} json'", timeout=20, inv=INV)
    return json.loads(o) if rc == 0 and o.strip() else None


def bgp_sessions(node):
    """(established, total) over every VRF."""
    d = j(node, "show bgp vrf all summary") or {}
    peers = [p for v in d.values() for af in ("ipv4Unicast", "l2VpnEvpn") for p in ((v.get(af) or {}).get("peers") or {}).values()]
    return sum(p.get("state") == "Established" for p in peers), len(peers)


def s_switch(n):
    up, tot = bgp_sessions(n["name"])
    out = {"bgp_up": up, "bgp_total": tot}
    if n["role"] in ("leaf", "border"):
        _, o = ssh(n["name"], "ip -br link | grep '^bond' | awk '{print $1, $2}'", inv=INV)
        bonds = dict(l.split()[:2] for l in o.splitlines() if len(l.split()) >= 2)
        out["bonds_up"], out["bonds"] = sum(v == "UP" for v in bonds.values()), len(bonds)
    return out


def s_fw(n):
    up, tot = bgp_sessions(n["name"])
    _, vr = ssh(n["name"], f"{OP} show vrrp", inv=INV)
    _, ct = ssh(n["name"], "sudo /usr/sbin/conntrack -C", inv=INV)
    return {"bgp_up": up, "bgp_total": tot, "vrrp": "MASTER" if "MASTER" in vr else ("BACKUP" if "BACKUP" in vr else "?"),
            "conntrack": int(ct.strip()) if ct.strip().isdigit() else None}


def s_lb(n):
    _, o = ssh(n["name"], "ip -br a; pidof haproxy >/dev/null && echo HAPROXY-UP; pidof keepalived >/dev/null && echo KA-UP", inv=INV)
    out = {"vip": f" {INV['web_vip']}/" in o, "haproxy": "HAPROXY-UP" in o, "keepalived": "KA-UP" in o, "backends": {}}
    try:   # HAProxy's stats CSV on the OOB address (the host reaches it)
        csv = httpx.get(f"http://{n['mgmt_ip']}:8404/;csv", timeout=3).text.splitlines()
        hdr = csv[0].lstrip("# ").split(",")
        for row in csv[1:]:
            f = dict(zip(hdr, row.split(",")))
            if f.get("pxname") == "web" and f.get("svname") not in ("FRONTEND", "BACKEND"):
                out["backends"][f["svname"]] = {"status": f.get("status"), "rate": int(f.get("rate") or 0), "total": int(f.get("stot") or 0)}
    except Exception: pass                                                # noqa: BLE001
    return out


def s_linux(n):
    svc = {"web": "nginx", "app": "e3t-app", "db": "postgresql"}.get(n["role"])
    cmd = f"cat /sys/class/net/bond0/bonding/slaves 2>/dev/null; echo; " + (f"doas rc-service -q {svc} status >/dev/null 2>&1 && echo SVC-UP" if svc else "true")
    _, o = ssh(n["name"], cmd, inv=INV)
    out = {"legs": len(o.splitlines()[0].split()) if o.strip() else 0, "service": svc, "service_up": "SVC-UP" in o if svc else None}
    if n["role"] == "db":
        _, r = ssh(n["name"], "doas su postgres -s /bin/sh -c \"psql -qtAX -c 'select pg_is_in_recovery()'\" 2>/dev/null", inv=INV)
        out["db_role"] = {"f": "primary", "t": "standby"}.get(r.strip(), "down")
    return out


def s_path():
    """One request through the whole application, from wan's clients (the relay is wan's: same path as theirs)."""
    t = time.time()
    try:
        r = httpx.get(f"{SHOP}/api/info", verify=False, timeout=6)
        d = r.json(); d["ok"] = r.status_code == 200; d["code"] = r.status_code
    except Exception as e:                                                # noqa: BLE001
        d = {"ok": False, "error": f"{type(e).__name__}"}
    d["ms_total"] = round((time.time() - t) * 1000)
    return d


def s_active_fw():
    """The firewall the tier VRFs' default route points at (border1, VRF web)."""
    d = j("border1", "show ip route vrf web 0.0.0.0/0") or {}
    nh = [h.get("ip") for p in d.get("0.0.0.0/0", []) if p.get("selected") for h in p.get("nexthops", [])]
    t = next(t for t in INV["evpn"]["transit"] if t["vrf"] == "web")
    return next((fw for fw, ip in t["fws"].items() if ip.split("/")[0] in nh), None)


STATE = {"at": 0, "nodes": {}, "path": None, "active_fw": None, "error": None}
_lock = threading.Lock()


def sample():
    vm = vm_states()
    jobs = {}
    for n in INV["nodes"]:
        if not vm[n["name"]]: continue
        f = {"spine": s_switch, "leaf": s_switch, "border": s_switch, "fw": s_fw, "lb": s_lb}.get(n["role"], s_linux if n["role"] in ("web", "app", "db") else None)
        if f: jobs[n["name"]] = (f, n)
    out = {}
    with ThreadPoolExecutor(12) as ex:
        fut = {k: ex.submit(f, n) for k, (f, n) in jobs.items()}
        path = ex.submit(s_path) if vm["wan"] else None
        afw = ex.submit(s_active_fw) if vm["border1"] else None
        for k, f in fut.items():
            try: out[k] = {"ok": True, **f.result(timeout=40)}
            except Exception as e: out[k] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:80]}"}   # noqa: BLE001
    nodes = {n["name"]: {"role": n["role"], "rack": n["rack"], "mgmt": n["mgmt_ip"], "running": vm[n["name"]], **out.get(n["name"], {})} for n in INV["nodes"]}
    res = {"at": time.time(), "nodes": nodes, "path": None, "active_fw": None, "error": None}
    try: res["path"] = path.result(timeout=10) if path else None
    except Exception: pass                                                # noqa: BLE001
    try: res["active_fw"] = afw.result(timeout=10) if afw else None
    except Exception: pass                                                # noqa: BLE001
    return res


def sampler():
    while True:
        t0 = time.time()
        try: s = sample()
        except Exception as e: s = {**STATE, "error": f"{type(e).__name__}: {e}"}   # noqa: BLE001
        with _lock: STATE.clear(); STATE.update(s)
        time.sleep(max(1.0, 5 - (time.time() - t0)))


threading.Thread(target=sampler, daemon=True).start()


# ---- runs ----------------------------------------------------------------------------------------------------------------
class Run(RunBase):
    LAB = NAME
    STEP_TITLES = {"verify": "Verify: fabric, firewalls, data plane, application, policy matrix",
                   "chaos": "Failure scenario under load", "fw_failover": "A live connection through a firewall failover",
                   "test": "Robot Framework suites", "services": "Deploy the application"}

    def __init__(self, mode, options=None, resume_of=None):
        super().__init__(mode, options, resume_of, runs_dir=RUNS_DIR, cwd=LAB)
        o = self.options
        if mode == "chaos": self.steps[0]["title"] = f"Failure scenario under load: {o['scenario']} ({SCEN_TEXT[o['scenario']]}), {o.get('down', 20)} s down"
        if mode == "test": self.steps[0]["title"] = ("Robot suite " + o["suite"]) if o.get("suite") else ("Robot suites" + (" (read-only)" if o.get("quick") else " (all, ~35 min)"))

    def plan(self): return [self.mode]

    def do_verify(self, s):
        rc = self.sh([LAB / "lab.sh", "verify"] + (["--quick"] if self.options.get("quick") else []), check=False)
        last = next((l["line"] for l in reversed(self.log) if "checks passed" in l["line"]), ""); s["summary"] = last
        if rc: raise RuntimeError(last or f"verify failed (rc={rc})")

    def do_chaos(self, s):
        o = self.options
        rc = self.sh([LAB / "tests/.venv/bin/python", LAB / "tools/chaos.py", o["scenario"], "--down", int(o.get("down", 20)), "--json"], check=False)
        try:
            txt = "\n".join(l["line"] for l in self.log); res = json.loads(txt[txt.index("\n[") + 1:])[0]
            f = next(p for p in res["phases"] if p["phase"] == "failure")
            s["summary"] = f"while down: {f['read']['failed']}/{f['read']['requests']} reads, {f['write']['failed']}/{f['write']['requests']} writes failed; longest gap {f['read']['longest_gap_s']} s"
            self.say("RESULT " + json.dumps(res))
        except Exception: pass                                            # noqa: BLE001
        if rc: raise RuntimeError(f"scenario failed (rc={rc})")

    def do_fw_failover(self, s):
        rc = self.sh([LAB / "tests/.venv/bin/python", LAB / "tools/fw_failover.py"], check=False)
        s["summary"] = "; ".join(l["line"] for l in self.log if "longest gap" in l["line"] or "connection " in l["line"][:20])[-200:]
        if rc: raise RuntimeError("the connection did not survive")

    def do_test(self, s):
        o = self.options; args = [LAB / "tests/run.sh"]
        if o.get("suite"): args.append(f"suites/{o['suite']}.robot")
        elif o.get("quick"): args += ["--exclude", "disruptive"]
        rc = self.sh(args, check=False)
        self.record_tests(LAB / "results", s, rc)

    def do_services(self, s):
        self.sh([LAB / "lab.sh", "services"])


SCEN_TEXT = {"lb": "lb1 off: the VIP to lb2", "web": "web1 off: HAProxy routes around it", "app": "app1 off: nginx routes around it",
             "db": "db1 off: db2 promoted", "leaf": "leaf1 off: the servers' other leg", "border": "border1 off: border2 carries all",
             "fw": "fw1 off: every VRF to fw2"}


class RunRequest(BaseModel):
    mode: str = Field(..., description="verify | chaos | fw_failover | test | services")
    options: dict = Field(default_factory=dict, description="verify: {quick}; chaos: {scenario, down}; test: {suite} or {quick}")


install_runs_api(app, registry, resume_factory=lambda d: Run(d["mode"], d.get("options"), resume_of=d))


@app.post("/api/runs", tags=["runs"], summary="Start a run (queued behind a running one)")
def start_run(req: RunRequest):
    o = dict(req.options)
    if req.mode not in Run.STEP_TITLES: raise HTTPException(400, f"mode: one of {', '.join(Run.STEP_TITLES)}")
    if req.mode == "chaos":
        if o.get("scenario") not in SCENARIOS: raise HTTPException(400, f"scenario: one of {', '.join(SCENARIOS)}")
        o["down"] = max(5, min(120, int(o.get("down") or 20)))
    if req.mode == "test" and o.get("suite") and o["suite"] not in SUITES: raise HTTPException(400, f"suite: one of {', '.join(SUITES)}")
    r = Run(req.mode, o); registry.start(r); return registry.describe(r)


# ---- the page and its data ---------------------------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def index(): return FileResponse(STATIC / "index.html")


@app.get("/api/state", tags=["state"], summary="The live snapshot: nodes, request path, active firewall (refreshed every 5 s)")
def state():
    with _lock: return {**STATE, "age_s": round(time.time() - STATE["at"], 1) if STATE["at"] else None}


@app.get("/api/lab", tags=["state"], summary="The lab's design: nodes, VRFs, VLANs, transit, policy, scenarios, suites")
def lab():
    ev = INV["evpn"]
    return {"name": NAME, "version": VERSION, "web_vip": INV["web_vip"], "clients": INV["wan"]["clients"],
            "nodes": [{**{k: n.get(k) for k in ("name", "role", "rack", "mgmt_ip", "loopback", "asn")}, "addr": (n.get("net") or {}).get("address", "").split("/")[0] or None}
                      for n in INV["nodes"]],
            "vrfs": ev["vrfs"], "vlans": ev["vlans"], "transit": ev["transit"], "policy": INV.get("fw_policy", []),
            "segments": [{"es": s["es"], "node": s["node"], "legs": [l["switch"] for l in s["legs"]]} for s in ev["segments"]],
            "scenarios": SCEN_TEXT, "suites": SUITES, "links": inventory()["links"]}


@app.get("/api/db", tags=["state"], summary="PostgreSQL roles and replication (tools/dbctl.py status)")
def db():
    import dbctl
    return [{"node": n, "role": r, "detail": x} for n, r, x in dbctl.status()]


# ---- the Order Desk, relayed (and HAProxy's stats page) -----------------------------------------------------------------
_shop = httpx.AsyncClient(base_url=SHOP, verify=False, timeout=15)        # the lab's self-signed certificate


async def _relay(client, path, request: Request):
    body = await request.body()
    hdrs = {k: v for k, v in request.headers.items() if k.lower() in ("content-type", "accept")}
    try:
        r = await client.request(request.method, "/" + path, params=request.query_params, content=body, headers=hdrs)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"the application did not answer through wan: {type(e).__name__}")
    keep = {k: v for k, v in r.headers.items() if k.lower() in ("content-type", "x-lb-node", "x-web-node", "x-app-node", "cache-control")}
    return Response(r.content, status_code=r.status_code, headers=keep)


@app.api_route("/shop/{path:path}", methods=["GET", "POST"], tags=["application"], summary="The Order Desk (relayed through wan to the web VIP)")
async def shop(path: str, request: Request): return await _relay(_shop, path, request)


@app.get("/shop", include_in_schema=False)
def shop_slash(): return Response(status_code=307, headers={"Location": "/shop/"})


@app.get("/haproxy/{lb}/", tags=["application"], summary="HAProxy's stats page on a load balancer (its OOB address)")
async def haproxy(lb: str, request: Request):
    if N.get(lb, {}).get("role") != "lb": raise HTTPException(404, "no such load balancer")
    async with httpx.AsyncClient(base_url=f"http://{N[lb]['mgmt_ip']}:8404", timeout=5) as c:
        return await _relay(c, "", request)


# ---- monitoring --------------------------------------------------------------------------------------------------------
@app.get("/api/sd", tags=["monitoring"], summary="Prometheus HTTP service discovery: this portal")
def prometheus_sd():
    return [{"targets": [f"10.0.0.1:{PORT}"], "labels": {"lab": NAME, "job": "portal", "role": "portal"}}]


@app.get("/metrics", tags=["monitoring"], summary="Prometheus metrics: VMs, sessions, firewalls, VIP, database, the application", response_class=PlainTextResponse)
def metrics():
    with _lock: s = json.loads(json.dumps(STATE))
    L = {"lab": NAME}; out = []
    def g(name, help_): out.extend([f"# HELP {name} {help_}", f"# TYPE {name} gauge"])
    nodes = s.get("nodes") or {}
    g("lab_vm_running", "1 if the node's VM is running (the hub's word for it)")
    for k, v in nodes.items(): out.append(line("lab_vm_running", {**L, "node": k, "role": v["role"]}, int(v["running"])))
    g("e3t_node_answers", "1 if the running node answered the portal's sampler over SSH")
    for k, v in nodes.items():
        if v["running"]: out.append(line("e3t_node_answers", {**L, "node": k, "role": v["role"]}, int(v.get("ok", False))))
    g("e3t_bgp_sessions_established", "BGP sessions Established on the node (every VRF; IPv4 + EVPN)"); g("e3t_bgp_sessions", "BGP sessions configured on the node")
    for k, v in nodes.items():
        if "bgp_total" in v:
            out += [line("e3t_bgp_sessions_established", {**L, "node": k, "role": v["role"]}, v["bgp_up"]), line("e3t_bgp_sessions", {**L, "node": k, "role": v["role"]}, v["bgp_total"])]
    g("e3t_segment_bonds_up", "Ethernet Segment bonds up on the leaf / border"); g("e3t_segment_bonds", "Ethernet Segment bonds on the leaf / border")
    for k, v in nodes.items():
        if "bonds" in v: out += [line("e3t_segment_bonds_up", {**L, "node": k}, v["bonds_up"]), line("e3t_segment_bonds", {**L, "node": k}, v["bonds"])]
    g("e3t_server_bond_legs", "Active legs in the Linux VM's bond (2 = both switches)")
    for k, v in nodes.items():
        if "legs" in v: out.append(line("e3t_server_bond_legs", {**L, "node": k, "role": v["role"]}, v["legs"]))
    g("e3t_service_up", "1 if the VM's tier service runs (nginx / e3t-app / postgresql)")
    for k, v in nodes.items():
        if v.get("service"): out.append(line("e3t_service_up", {**L, "node": k, "role": v["role"], "service": v["service"]}, int(bool(v.get("service_up")))))
    g("e3t_fw_vrrp_master", "1 if the firewall is the conntrack-sync VRRP master"); g("e3t_fw_conntrack_entries", "Connection-tracking entries on the firewall")
    for k, v in nodes.items():
        if v["role"] == "fw" and v.get("ok"):
            out.append(line("e3t_fw_vrrp_master", {**L, "node": k}, int(v.get("vrrp") == "MASTER")))
            if v.get("conntrack") is not None: out.append(line("e3t_fw_conntrack_entries", {**L, "node": k}, v["conntrack"]))
    g("e3t_fw_active", "1 for the firewall the tier VRFs route through (border1, VRF web: the default route's next hop)")
    for fw in (n for n in nodes if nodes[n]["role"] == "fw"): out.append(line("e3t_fw_active", {**L, "node": fw}, int(s.get("active_fw") == fw)))
    g("e3t_vip_holder", "1 for the load balancer holding the web VIP"); g("e3t_haproxy_backend_up", "1 if HAProxy (on the VIP holder) has the web server UP")
    for k, v in nodes.items():
        if v["role"] == "lb" and v.get("ok"):
            out.append(line("e3t_vip_holder", {**L, "node": k}, int(v.get("vip", False))))
            if v.get("vip"):
                for b, x in (v.get("backends") or {}).items(): out.append(line("e3t_haproxy_backend_up", {**L, "lb": k, "server": b}, int(x["status"] == "UP")))
    g("e3t_db_primary", "1 if the db node is the PostgreSQL primary"); g("e3t_db_standby", "1 if the db node is a streaming standby")
    for k, v in nodes.items():
        if v["role"] == "db" and v.get("db_role"):
            out += [line("e3t_db_primary", {**L, "node": k}, int(v["db_role"] == "primary")), line("e3t_db_standby", {**L, "node": k}, int(v["db_role"] == "standby"))]
    p = s.get("path")
    if p is not None:
        g("e3t_app_ok", "1 if the sampler's request through the VIP (from wan's clients) succeeded"); out.append(line("e3t_app_ok", L, int(bool(p.get("ok")))))
        g("e3t_app_latency_ms", "That request's time, end to end (portal -> wan -> firewall -> lb -> web -> app -> db)"); out.append(line("e3t_app_latency_ms", L, p.get("ms_total", 0)))
        if p.get("orders") is not None: g("e3t_orders", "Orders in the database"); out.append(line("e3t_orders", L, p["orders"]))
    act = registry.active()
    g("e3t_lab_run_active", "1 while a portal run goes (failure scenarios and tests power nodes off on purpose)"); out.append(line("e3t_lab_run_active", L, int(act is not None)))
    out += run_metrics(NAME, registry.list())
    return exposition(out + metrics_generated(NAME))


def main():
    import uvicorn; uvicorn.run(app, host=os.environ.get("WEBAPP_HOST", "0.0.0.0"), port=PORT)


if __name__ == "__main__": main()
