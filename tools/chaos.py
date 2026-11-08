#!/usr/bin/env python3
"""Failure scenarios under load: a component dies while wan's test clients use the application, then it comes back.

The load (on wan, from 198.51.100.1, through the firewall to the web VIP): an HTTPS GET /api/info every 100 ms and an
order (POST /api/orders) every second, each on a new TCP + TLS connection with a 5 s timeout — so every request is a
fresh trip through every tier, and a failed one is visible. Each result is a line: time, ok, kind, lb / web / app / db.

  scenario  what fails                              what should take over
  lb        lb1 powered off                         keepalived moves the VIP to lb2 (GARP; EVPN moves the MAC/IP)
  web       web1 powered off                        HAProxy's health check takes it out (2 x 1 s); retries redispatch
  app       app1 powered off                        nginx: connect timeout 2 s, then the other app server
  db        db1 powered off; db2 promoted after     the app finds the new primary (target_session_attrs=read-write);
            --promote-after s (the operator)        restore: db1 re-seeded as db2's replica, then switched back
  leaf      leaf1 powered off                       web1 / app1 / db1 keep their other bond leg (EVPN multihoming)
  border    border1 powered off                     fw / lb bonds keep border2; wan keeps its other link
  fw        fw1 powered off                         BFD + BGP move every VRF to fw2; conntrack-sync keeps sessions

  chaos.py SCENARIO [--down S] [--promote-after S] [--json]     a summary per phase: requests, failures, longest gap
  chaos.py all                                                  every scenario in turn"""
import argparse, json, subprocess, sys, time
from labssh import inventory, run

inv = inventory(); N = {n["name"]: n for n in inv["nodes"]}
VIP = inv["web_vip"]; CLIENT = "198.51.100.1"
LOG = "/tmp/e3t-load.log"; GEN = "/tmp/e3t-load.py"
LOADGEN = f'''import http.client, json, ssl, sys, threading, time   # e3t-loadgen
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
out = open("{LOG}", "w", buffering=1); lock = threading.Lock()
def req(kind, n):
    t = time.time(); rec = {{"t": round(t, 3), "kind": kind, "ok": False}}
    try:
        c = http.client.HTTPSConnection("{VIP}", 443, timeout=5, context=ctx, source_address=("{CLIENT}", 0))
        if kind == "read": c.request("GET", "/api/info")
        else: c.request("POST", "/api/orders", body=json.dumps({{"item": "load-%d" % n, "qty": 1}}), headers={{"Content-Type": "application/json"}})
        r = c.getresponse(); body = r.read(); c.close()
        rec["code"] = r.status; rec["ok"] = r.status in (200, 201)
        try: j = json.loads(body); rec.update({{k: j.get(k) for k in ("lb", "web", "app", "db") if k in j}})
        except Exception: pass
        rec["lb"] = rec.get("lb") or r.getheader("X-LB-Node")
    except Exception as e: rec["err"] = type(e).__name__
    rec["ms"] = round((time.time() - t) * 1000)
    with lock: out.write(json.dumps(rec) + "\\n")
i = 0
while True:
    threading.Thread(target=req, args=("read", i), daemon=True).start()
    if i % 10 == 0: threading.Thread(target=req, args=("write", i), daemon=True).start()
    i += 1; time.sleep(0.1)
'''


def virsh(*a):
    return subprocess.run(["virsh", "-q", "-c", "qemu:///system", *a], capture_output=True, text=True)


def ssh_up(node, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            rc, _ = run(node, "true", timeout=10, inv=inv)
            if rc == 0: return True
        except Exception: pass                                           # noqa: BLE001
        time.sleep(3)
    return False


def power_off(node): virsh("destroy", N[node]["domain"])
def power_on(node): virsh("start", N[node]["domain"]); return ssh_up(node)


# ---- the load ---------------------------------------------------------------------------------------------------------
def load_start():
    run("wan", "pkill -f [e]3t-load.py", inv=inv)
    import paramiko
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(N["wan"]["mgmt_ip"], username="vyos", password="vyos", look_for_keys=False, allow_agent=False, timeout=20)
    i, o, _ = c.exec_command(f"cat > {GEN}"); i.write(LOADGEN); i.channel.shutdown_write(); o.channel.recv_exit_status(); c.close()
    run("wan", f"nohup python3 {GEN} >/dev/null 2>&1 &", inv=inv)
    time.sleep(2)


def load_stop():
    run("wan", "pkill -f [e]3t-load.py", inv=inv); time.sleep(6)                    # in-flight requests finish (5 s timeout)
    _, o = run("wan", f"cat {LOG}", timeout=60, inv=inv)
    return sorted((json.loads(l) for l in o.splitlines() if l.startswith("{")), key=lambda r: r["t"])


def summarize(recs, t0, t1, label):
    """Requests started in [t0, t1): counts, failures, and the longest stretch without a successful read / write."""
    out = {"phase": label, "seconds": round(t1 - t0, 1)}
    for kind in ("read", "write"):
        rs = [r for r in recs if r["kind"] == kind and t0 <= r["t"] < t1]
        ok = [r["t"] for r in rs if r["ok"]]
        gap = max((b - a for a, b in zip([t0] + ok, ok + [t1])), default=t1 - t0)
        out[kind] = {"requests": len(rs), "failed": sum(not r["ok"] for r in rs), "longest_gap_s": round(gap, 2)}
    served = lambda k: sorted({str(r.get(k)) for r in recs if t0 <= r["t"] < t1 and r["ok"] and r.get(k)})
    out["served_by"] = {k: served(k) for k in ("lb", "web", "app", "db")}
    return out


# ---- scenarios ---------------------------------------------------------------------------------------------------------
def wait_for(pred, timeout, step=2):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if pred(): return True
        except Exception: pass                                           # noqa: BLE001
        time.sleep(step)
    return False


def vip_on(node):
    return f" {VIP}/" in run(node, "ip -br a", inv=inv)[1]           # on vrrp.10 (the virtual MAC)


def bgp_up(node):
    """Every BGP session up — and on a leaf / border every Ethernet Segment bond raised again by the protodown watcher
    (~20 s after EVPN settles) and aggregated: the leg's return must happen inside this scenario, not the next one's
    baseline."""
    rc, o = run(node, "vtysh -c 'show bgp vrf all summary json'", inv=inv)
    d = json.loads(o); peers = [p for v in d.values() for p in (v.get("ipv4Unicast") or {}).get("peers", {}).values()]
    if not (peers and all(p.get("state") == "Established" for p in peers)): return False
    if N[node]["role"] in ("leaf", "border"):
        _, links = run(node, "ip -br link | grep '^bond'", inv=inv)
        if not links.strip() or any(" UP " not in l + " " for l in links.splitlines()): return False
        time.sleep(5)                                                     # LACP aggregates the returning leg
    return True


def s_power(node, settle_check):
    def inject(a): power_off(node)
    def restore(a):
        if not power_on(node): raise RuntimeError(f"{node}: no SSH after power on")
        if not wait_for(settle_check, 300): raise RuntimeError(f"{node}: did not settle after power on")
    return inject, restore


def s_db():
    import dbctl
    def inject(a):
        run("db1", "doas rc-update del postgresql default", inv=inv)     # db1 must not come back as a second primary
        power_off("db1"); time.sleep(a.promote_after); dbctl.promote("db2")
    def restore(a):
        power_on("db1"); dbctl.reseed("db1", "db2")
        print("    planned switchover back to db1 (part of the restore, not measured) ...", flush=True)
        dbctl.switchover("db1")
    return inject, restore


SCENARIOS = {
    "lb":     lambda: s_power("lb1", lambda: vip_on("lb1")),
    "web":    lambda: s_power("web1", lambda: "UP" in run("lb1", "echo 'show servers state web' | doas nc -U /var/run/haproxy.sock 2>/dev/null; curl -s -m2 http://172.16.10.11/health", inv=inv)[1] or True),
    "app":    lambda: s_power("app1", lambda: run("app1", "curl -s -m2 http://127.0.0.1:8080/health", inv=inv)[1].count("true") == 1),
    "db":     s_db,
    "leaf":   lambda: s_power("leaf1", lambda: bgp_up("leaf1")),
    "border": lambda: s_power("border1", lambda: bgp_up("border1")),
    "fw":     lambda: s_power("fw1", lambda: bgp_up("fw1")),
}


def wan_offset():
    """wan's clock minus the host's (the load's timestamps are wan's, the phases the host's): ~2 s apart here."""
    best = None
    for _ in range(3):
        a = time.time(); _, o = run("wan", "date +%s.%N", inv=inv); b = time.time()
        if best is None or b - a < best[0]: best = (b - a, float(o.strip()) - (a + b) / 2)
    return best[1]


def scenario(name, down=20, promote_after=5, quiet=False):
    a = argparse.Namespace(down=down, promote_after=promote_after)
    inject, restore = SCENARIOS[name]()
    say = (lambda *x: None) if quiet else (lambda *x: print(*x, flush=True))
    say(f"== {name}: baseline 15 s"); load_start(); t0 = time.time(); time.sleep(15)
    t1 = time.time(); say(f"   inject"); inject(a); time.sleep(down)
    t2 = time.time(); say(f"   restore"); restore(a)
    t3 = time.time(); say(f"   after: 15 s"); time.sleep(15); t4 = time.time()
    recs = load_stop(); off = wan_offset()
    t0, t1, t2, t3, t4 = (t + off for t in (t0, t1, t2, t3, t4))
    # a request that started before the injection but was still in flight (up to its 5 s timeout) belongs to the
    # failure: the baseline ends INFLIGHT s before the injection
    INFLIGHT = 5.5
    res = {"scenario": name, "phases": [summarize(recs, t0, t1 - INFLIGHT, "baseline"), summarize(recs, t1 - INFLIGHT, t2, "failure"),
                                        summarize(recs, t2, t3, "restore"), summarize(recs, t3, t4, "after")]}
    return res


def show(res):
    print(f"\n{res['scenario']}:")
    for p in res["phases"]:
        r, w = p["read"], p["write"]
        print(f"  {p['phase']:8} {p['seconds']:6.1f} s   reads {r['requests']:4} failed {r['failed']:3} gap {r['longest_gap_s']:5.2f} s"
              f"   writes {w['requests']:3} failed {w['failed']:3} gap {w['longest_gap_s']:5.2f} s   {p['served_by']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("scenario", choices=[*SCENARIOS, "all"])
    ap.add_argument("--down", type=int, default=20); ap.add_argument("--promote-after", type=int, default=5)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    out = []
    for s in (SCENARIOS if a.scenario == "all" else [a.scenario]):
        res = scenario(s, a.down, a.promote_after); out.append(res); show(res)
    if a.json: print(json.dumps(out, indent=1))
