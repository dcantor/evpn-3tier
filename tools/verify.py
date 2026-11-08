#!/usr/bin/env python3
"""The lab end to end: control plane, overlay, firewalls, data plane and the firewall policy. Exit 1 on any failure.

  fabric    every underlay / EVPN session up on every spine, leaf and border; BFD up; every Ethernet Segment seen
            by both of its switches with the peer as remote VTEP
  firewall  every border <-> firewall session up in every VRF; fw1 is the preferred next hop (the default route in
            each tier VRF on the borders points at fw1); fw1 is the conntrack-sync VRRP master
  data      each Linux VM reaches its anycast gateway and the other VMs of its VRF
  app       the web VIP on the first load balancer only; from wan's test clients, requests through the VIP reach every
            web and app server and the database primary; an order written from outside reaches every replica
  policy    for each (source tier, destination, port): a TCP connection succeeds exactly when FW_POLICY (or one VRF)
            allows it — temporary listeners on the targets, python3 clients on the sources (wan for "outside")

  verify.py [--quick]     --quick: skip the policy matrix"""
import ipaddress, sys, time
from concurrent.futures import ThreadPoolExecutor
from labssh import inventory, run, vtysh_json

inv = inventory(); N = {n["name"]: n for n in inv["nodes"]}
results = []


def check(name, ok, detail=""):
    results.append(ok); print(f"{'PASS' if ok else 'FAIL'}  {name}{'' if ok or not detail else '  — ' + detail}")


def by_role(*roles):
    return [n for n in inv["nodes"] if n["role"] in roles]


# ---- fabric ---------------------------------------------------------------------------------------------------------
def fabric(n):
    out = []
    s = vtysh_json(n["name"], "show bgp summary", inv) or {}
    peers = (s.get("ipv4Unicast") or {}).get("peers", {})
    want = [p["name"] for p in n["ports"] if p.get("kind") == "fabric"]
    down = [p for p in want if (peers.get(p) or {}).get("state") != "Established"]
    out.append((f"{n['name']}: underlay sessions {len(want) - len(down)}/{len(want)}", not down, f"down: {down}"))
    ev = vtysh_json(n["name"], "show bgp l2vpn evpn summary", inv) or {}
    edown = [p for p in want if ((ev.get("peers") or {}).get(p) or {}).get("state") != "Established"]
    out.append((f"{n['name']}: EVPN sessions {len(want) - len(edown)}/{len(want)}", not edown, f"down: {edown}"))
    bfd = vtysh_json(n["name"], "show bfd peers", inv) or []
    bdown = [b.get("peer") or b.get("interface") for b in bfd if b.get("status") != "up"]
    out.append((f"{n['name']}: BFD {len(bfd) - len(bdown)}/{len(bfd)} up", bfd and not bdown, f"down: {bdown}"))
    if n["role"] in ("leaf", "border"):
        es = {e["esi"]: e for e in vtysh_json(n["name"], "show evpn es", inv) or []}
        mine = [s for s in inv["evpn"]["segments"] if any(l["switch"] == n["name"] for l in s["legs"])]
        peer_lo = {s["es"]: [N[l["switch"]]["loopback"] for l in s["legs"] if l["switch"] != n["name"]] for s in mine}
        bad = []
        for s in mine:
            e = next((e for k, e in es.items() if k.endswith(f":{s['es']:02x}") and s["sys_mac"].replace(":", "")[-2:] in k.replace(":", "")), None)
            vteps = [v.get("vtep") for v in (e or {}).get("vteps", [])]
            if not e or "local" not in e.get("flags", []) or not all(p in vteps for p in peer_lo[s["es"]]): bad.append(f"ES {s['es']} ({s['node']})")
        out.append((f"{n['name']}: Ethernet Segments {len(mine) - len(bad)}/{len(mine)} local + peer", not bad, f"bad: {bad}"))
    return out


# ---- firewalls ------------------------------------------------------------------------------------------------------
def firewalls():
    out = []
    fw1 = next(n for n in by_role("fw") if n["fw_no"] == 1)
    for b in by_role("border"):
        for t in inv["evpn"]["transit"]:
            s = (vtysh_json(b["name"], f"show bgp vrf {t['vrf']} summary", inv) or {}).get("ipv4Unicast", {}).get("peers", {})
            down = [ip.split("/")[0] for ip in t["fws"].values() if (s.get(ip.split("/")[0]) or {}).get("state") != "Established"]
            out.append((f"{b['name']} VRF {t['vrf']}: firewall sessions {2 - len(down)}/2", not down, f"down: {down}"))
            if t["vrf"] == "outside": continue
            r = vtysh_json(b["name"], f"show ip route vrf {t['vrf']} 0.0.0.0/0", inv) or {}
            nhs = [h.get("ip") for p in r.get("0.0.0.0/0", []) if p.get("selected") for h in p.get("nexthops", [])]
            want = t["fws"][fw1["name"]].split("/")[0]
            out.append((f"{b['name']} VRF {t['vrf']}: default route via fw1 ({want})", nhs == [want], f"next hops {nhs}"))
    # `show vrrp` makes keepalived dump its state to a file and reads it back: two readers at once (the portal samples it
    # too) and one gets "VRRP data is not available" — ask up to three times
    for _ in range(3):
        rc, o = run(fw1["name"], "/opt/vyatta/bin/vyatta-op-cmd-wrapper show vrrp", inv=inv)
        if "MASTER" in o or "BACKUP" in o: break
        time.sleep(2)
    out.append(("fw1 is the conntrack-sync VRRP master", "MASTER" in o, o.strip()[-200:]))
    return out


# ---- data plane -----------------------------------------------------------------------------------------------------
def linux():
    return [n for n in inv["nodes"] if n.get("net")]


def data(n):
    out = []; net = n["net"]
    peers = [m for m in linux() if m["net"]["vrf"] == net["vrf"] and m["name"] != n["name"]]
    targets = [net["gateway"]] + [m["net"]["address"].split("/")[0] for m in peers]
    cmd = "; ".join(f"ping -c2 -W2 -q {t} >/dev/null 2>&1 && echo OK {t} || echo FAIL {t}" for t in targets)
    _, o = run(n["name"], cmd, inv=inv)
    bad = [l.split()[1] for l in o.splitlines() if l.startswith("FAIL")]
    out.append((f"{n['name']}: gateway + {len(peers)} peer(s) in VRF {net['vrf']}", not bad and o.count("OK") == len(targets), f"unreachable: {bad or o[-200:]}"))
    return out


# ---- the application -------------------------------------------------------------------------------------------------
import json, time


def app():
    out = []; vip = inv["web_vip"]; clients = str(ipaddress.ip_network(inv["wan"]["clients"]).network_address + 1)
    lbs = sorted(by_role("lb"), key=lambda n: n["idx"])
    has = {l["name"]: f" {vip}/" in run(l["name"], "ip -br a", inv=inv)[1] for l in lbs}
    out.append((f"web VIP {vip} on {lbs[0]['name']} only", has[lbs[0]["name"]] and not any(has[l["name"]] for l in lbs[1:]), f"held: {has}"))
    curl = f"curl -sk -m5 --interface {clients}"
    _, o = run("wan", "; ".join([f"{curl} https://{vip}/api/info; echo"] * 12), timeout=120, inv=inv)
    got = [json.loads(l) for l in o.splitlines() if l.startswith("{")]
    seen = lambda k: sorted({g.get(k) for g in got})
    webs = sorted(n["name"] for n in by_role("web")); apps = sorted(n["name"] for n in by_role("app"))
    primary = ip_of(sorted(by_role("db"), key=lambda n: n["idx"])[0])
    out.append((f"12 requests from {clients} through the VIP: all answered", len(got) == 12, f"{len(got)} answered"))
    out.append((f"...spread over every web server {webs}", seen("web") == webs, f"saw {seen('web')}"))
    out.append((f"...and every app server {apps}", seen("app") == apps, f"saw {seen('app')}"))
    out.append((f"...all on the database primary {primary}", seen("db") == [primary] and seen("db_role") == ["primary"], f"saw {seen('db')} {seen('db_role')}"))
    out.append((f"...the client address seen by the app is {clients}", seen("client") == [clients], f"saw {seen('client')}"))
    item = f"verify-{int(time.time())}"
    _, o = run("wan", f"{curl} -X POST -H 'Content-Type: application/json' -d '{{\"item\":\"{item}\",\"qty\":1}}' https://{vip}/api/orders", inv=inv)
    out.append(("an order placed from outside is written", f'"{item}"' in o, o[-200:]))
    time.sleep(1)
    for d in sorted(by_role("db"), key=lambda n: n["idx"])[1:]:
        _, o = run(d["name"], f"doas su postgres -s /bin/sh -c \"psql -qtAX -d shop -c \\\"select pg_is_in_recovery(), count(*) from orders where item = '{item}'\\\"\"", inv=inv)
        out.append((f"...and replicated to {d['name']} (a hot standby)", o.strip() == "t|1", o.strip()[-200:]))
    return out


def ip_of(n):
    return n["net"]["address"].split("/")[0]


# ---- the firewall policy ---------------------------------------------------------------------------------------------
PORTS = sorted({int(p["port"]) for p in inv.get("fw_policy", []) if p["proto"] == "tcp" and p["port"]} | {9999})
LISTEN = ("python3 -c \"import socket,threading,time\n"
          "def l(p):\n s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\n"
          " try: s.bind(('0.0.0.0',p));s.listen(64)\n except OSError: return\n"
          " while True: c,_=s.accept();c.close()\n"
          "for p in {ports}: threading.Thread(target=l,args=(p,),daemon=True).start()\n"
          "time.sleep({secs})\" >/dev/null 2>&1 &")
CONNECT = ("python3 -c \"import socket,sys\n"
           "for a in sys.argv[1:]:\n h,p=a.rsplit(':',1)\n s=socket.socket();s.settimeout(2)\n"
           "{bind}"
           " try: s.connect((h,int(p)));print('OPEN',a)\n except Exception: print('CLOSED',a)\n s.close()\" {targets}")


def allowed(src_vrf, dst_vrf, dst_ip, port):
    if src_vrf == dst_vrf: return True
    for p in inv.get("fw_policy", []):
        if (p["from"], p["to"], p["proto"]) == (src_vrf, dst_vrf, "tcp") and str(port) == p["port"] \
                and ipaddress.ip_address(dst_ip) in ipaddress.ip_network(p["dest"], strict=False):
            return True
    return False


def policy():
    hosts = [m for m in linux() if m["role"] != "lb"]
    sources = [(m["name"], m["net"]["vrf"], "") for m in hosts if m["name"].endswith("1")]
    clients = str(ipaddress.ip_network(inv["wan"]["clients"]).network_address + 1)
    sources.append(("wan", "outside", f" s.bind(('{clients}',0))\n"))
    with ThreadPoolExecutor(8) as ex:   # listeners for 3 minutes on every server (ports already in use stay as they are)
        list(ex.map(lambda m: run(m["name"], "doas " + LISTEN.format(ports=PORTS, secs=180), inv=inv), hosts))

    def one(src_vrf_bind):
        src, vrf, bind = src_vrf_bind
        vipt = {"name": "VIP", "net": {"address": inv["web_vip"] + "/24", "vrf": "web"}}
        tg = [(m, port) for m in hosts + [vipt] for port in PORTS if m["name"] != src]
        py = CONNECT.format(bind=bind, targets=" ".join(f"{m['net']['address'].split('/')[0]}:{p}" for m, p in tg))
        _, o = run(src, py, timeout=180, inv=inv)
        seen = {l.split()[1]: l.split()[0] == "OPEN" for l in o.splitlines() if l.split()[:1] in (["OPEN"], ["CLOSED"])}
        wrong = []
        for m, port in tg:
            ip = m["net"]["address"].split("/")[0]; want = allowed(vrf, m["net"]["vrf"], ip, port)
            if m["name"] == "VIP": want = want and port in (80, 443)      # HAProxy serves only these on the VIP
            if seen.get(f"{ip}:{port}") != want: wrong.append(f"{m['name']}:{port} {'open' if seen.get(f'{ip}:{port}') else 'closed'}, want {'open' if want else 'closed'}")
        n_open = sum(allowed(vrf, m["net"]["vrf"], m["net"]["address"].split("/")[0], p) and (m["name"] != "VIP" or p in (80, 443)) for m, p in tg)
        return (f"policy from {src} ({vrf}): {len(tg)} flows, {n_open} allowed, the rest blocked", not wrong, "; ".join(wrong[:6]))
    with ThreadPoolExecutor(len(sources)) as ex:   # in parallel: each blocked flow costs its 2 s timeout
        return list(ex.map(one, sources))


def main():
    quick = "--quick" in sys.argv
    print("== fabric")
    with ThreadPoolExecutor(8) as ex:
        for res in ex.map(fabric, by_role("spine", "leaf", "border")): [check(*r) for r in res]
    print("== firewalls")
    [check(*r) for r in firewalls()]
    print("== data plane")
    with ThreadPoolExecutor(8) as ex:
        for res in ex.map(data, linux()): [check(*r) for r in res]
    print("== application")
    [check(*r) for r in app()]
    if not quick:
        print("== firewall policy")
        [check(*r) for r in policy()]
    print(f"\n{sum(results)} of {len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
