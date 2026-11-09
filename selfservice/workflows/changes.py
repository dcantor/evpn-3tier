"""Executors of the change workflows: a VLAN's span, custom firewall rules, moving a server. Same rules as executors.py:
every stage checks before it writes, so a refusal changes nothing, and every stage is idempotent, so a failed request is
retried from the top. The checks read the lab from Nautobot (executors._lab), not lab.conf.

vlan-span       pre-flight (nothing on the borders uses it, if they are to go) -> Nautobot (span, description) -> switches
                pipeline (managed deletions take it off the borders) -> the VLAN on exactly the new span -> ping from the
                WAN -> phpIPAM (the gateway's description)
fw-rule         Nautobot (fw_policy: the rule with its own number, 500-899, and the request) -> firewalls pipeline ->
                the rule on both firewalls -> a connection / ping from the source zone to a VM at the destination
fw-rule-remove  Nautobot (the rule out) -> firewalls pipeline (managed deletions) -> gone from both firewalls
move-server     web and app servers, within their VRF, keeping their host number. The lab stays whole at every step:
                phpIPAM reserves the new address -> Nautobot adds bond0.<new VLAN> (no address) -> switches pipeline:
                the leaves carry both VLANs -> custom rules that reach the old address get a twin for the new one
                (firewalls pipeline) -> Nautobot moves the address -> servers pipeline (the move; nginx, pg_hba) and, for
                a web server, load balancers (HAProxy), both without verify (the lab is mid-move) -> Nautobot drops
                bond0.<old VLAN> and the old address's twins -> switches (and firewalls) pipeline WITH verify: the whole
                lab, on Nautobot's model -> checks, ping from the WAN -> phpIPAM releases the old address"""
import ipaddress
import re

from . import forms as F
from . import labmodel
from .clients import LOCATION, Gitea, Ipam, Nautobot
from .executors import _check, _lab, _ping_from_wan

OP = "/opt/vyatta/bin/vyatta-op-cmd-wrapper"
RULE_RANGE = range(500, 900)


def _pipeline(repo, log, verify=True):
    ok, num, url = Gitea().dispatch_and_wait(repo, log, verify=verify)
    _check(ok, f"{repo} run #{num} failed — {url}")
    log(f"{repo} run #{num} succeeded{'' if verify else ' (verify skipped: the lab is mid-change; it is verified at the end)'}: {url}")
    return {"run": num, "url": url}


def _show(ssh, inv, node, pattern):
    """A device's configuration lines matching a pattern, VyOS's quotes stripped."""
    _, out = ssh(node, f"{OP} show configuration commands | grep -E '{pattern}'", inv=inv, timeout=120)
    return out.replace("'", "")


# ---- change a VLAN's span ---------------------------------------------------------------------------------------------------
def vlan_span(r, log):
    p = r.parameters; vid, vrf, vni = p["vlan_id"], p["vrf"], p["vni"]; new = p["span_to"].split("+")
    gw_ip = str(ipaddress.ip_interface(p["gateway"]).ip); nb = Nautobot(); runs = {}
    log.stage("preflight")
    ctx = nb.context(); data = ctx["data"]
    entry = next((v for v in data["evpn"]["vlans"] if v["vlan"] == vid), None)
    _check(entry is not None, f"VLAN {vid} is not in Nautobot's plan — nothing changed")
    inv, ssh = _lab()
    if "borders" not in new:
        borders = {n["name"] for n in inv["nodes"] if n["role"] == "border"}
        users = [n["name"] for n in inv["nodes"] if vid in (n.get("vlans") or [])
                 and any(x.get("peer") in borders for x in n["ports"] if x.get("kind") == "access")]
        _check(not users, f"VLAN {vid} must stay on the border leaves: {', '.join(users)} use it — nothing changed")
    log(f"pre-flight: VLAN {vid} ({vrf}) is on {' + '.join(entry['on'])}; it can go to {' + '.join(new)}")
    log.stage("nautobot")
    if sorted(entry["on"]) != sorted(new):
        for v in data["evpn"]["vlans"]:
            if v["vlan"] == vid: v["on"] = new
        nb.patch(f"extras/config-contexts/{ctx['id']}", {"data": data})
        log(f"Nautobot: VLAN {vid} on {' + '.join(new)} in the evpn-3tier plan")
    else:
        log(f"Nautobot: VLAN {vid} already on {' + '.join(new)} (a retry)")
    vg = nb.one("ipam/vlan-groups", name=LOCATION); vo = nb.one("ipam/vlans", vid=vid, vlan_group=vg["id"])
    if vo and (d := re.sub(r" on [a-z+]+", f" on {'+'.join(new)}", vo.get("description") or "")) != vo.get("description"):
        nb.patch(f"ipam/vlans/{vo['id']}", {"description": d})
    labmodel.invalidate()
    log.stage("switches")
    runs["switches"] = _pipeline("evpn-3tier-switches", log)
    log.stage("checks")
    inv, ssh = _lab(); bad, on = [], []
    for n in (x for x in inv["nodes"] if x["role"] in ("leaf", "border")):
        out = _show(ssh, inv, n["name"], f"vlan-to-vni {vid} |peth{vid} address")
        has = f"vlan-to-vni {vid} vni {vni}" in out and f"peth{vid} address {gw_ip}/32" in out
        want = ("leaves" if n["role"] == "leaf" else "borders") in new
        if has: on.append(n["name"])
        if has != want: bad.append(f"{n['name']} ({'has it' if has else 'lacks it'})")
    _check(not bad, f"VLAN {vid} is not on exactly the new span: {', '.join(bad)}")
    log(f"on the devices: VLAN {vid} -> VNI {vni} and the gateway {gw_ip} on {', '.join(on)} — and nowhere else")
    log.stage("ping")
    pings = _ping_from_wan(inv, ssh, [(f"VLAN {vid} gateway", gw_ip)], log)
    log.stage("phpipam")
    ip = Ipam(); sec = next(s for s in ip.req("GET", "sections") if s["name"] == LOCATION); net = ipaddress.ip_network(p["subnet"])
    sub = next((x for x in ip.req("GET", f"subnets/cidr/{net.network_address}/{net.prefixlen}") or [] if x["sectionId"] == sec["id"]), None)
    a = next((x for x in ip.req("GET", f"subnets/{sub['id']}/addresses") or [] if x["ip"] == gw_ip), None) if sub else None
    want = f"anycast gateway, every leaf{' + border' if 'borders' in new else ''}"
    if a and a.get("description") != want:
        ip.req("PATCH", f"addresses/{a['id']}", json={"description": want}); log(f"phpIPAM: gateway {gw_ip} — {want}")
    else:
        log("phpIPAM: the gateway's description already says where it lives")
    return {"runs": runs, "ping": pings, "span": new}


# ---- custom firewall rules -------------------------------------------------------------------------------------------------
def _fw_name(src, dst):
    return f"{src.upper()}-{dst.upper()}"


def _fw_has(inv, ssh, rule):
    """Which firewalls carry the rule (number, action, protocol, destination, port) in its zone pair."""
    name, num = _fw_name(rule["from"], rule["to"]), rule["rule"]
    dest = rule["dest"]; out = {}
    for fw in (n["name"] for n in inv["nodes"] if n["role"] == "fw"):
        t = _show(ssh, inv, fw, f"ipv4 name {name} rule {num} ")
        pre = f"firewall ipv4 name {name} rule {num} "
        ok = all(pre + x in t for x in ["action accept", f"protocol {rule['proto']}", f"destination address {dest}"]
                 + ([f"destination port {rule['port']}"] if rule.get("port") else []))
        out[fw] = (ok, pre in t)
    return out


def _probe(inv, ssh, src, dst, proto, dest, port, log):
    """Traffic through the firewall the way a user's would go: from a VM of the source zone (the WAN's client address for
    outside) to a lab VM at the destination — a TCP connection or a ping. Returns a result, or None when the destination
    holds no lab VM (or for UDP, which has no handshake to prove)."""
    net = ipaddress.ip_network(dest, strict=False)
    vms = [n for n in inv["nodes"] if n.get("net") and n["net"]["vrf"] == dst and ipaddress.ip_interface(n["net"]["address"]).ip in net]
    vms.sort(key=lambda n: n["role"] == "lb")
    if proto == "udp" or not vms: return None
    tgt = vms[0]; tip = tgt["net"]["address"].split("/")[0]
    if src == "outside":
        s_node = next(n["name"] for n in inv["nodes"] if n["role"] == "wan")
        bind = str(ipaddress.ip_network(inv["wan"]["clients"]).network_address + 1)
    else:
        s = next((n for n in inv["nodes"] if n.get("net") and n["net"]["vrf"] == src and n["role"] != "lb"), None) or \
            next((n for n in inv["nodes"] if n.get("net") and n["net"]["vrf"] == src), None)
        if s is None: return None
        s_node, bind = s["name"], s["net"]["address"].split("/")[0]
    if proto == "icmp":
        _, o = ssh(s_node, f"ping -n -c 3 -i 0.3 -W 2 -I {bind} {tip} | tail -2", inv=inv, timeout=60)
        m = re.search(r"(\d+) received", o); got = int(m.group(1)) if m else 0
        return {"from": s_node, "to": f"{tgt['name']} {tip}", "what": "ping", "ok": got >= 2, "detail": f"{got}/3 answered"}
    lo = int(str(port).split("-")[0])
    # a listener for 60 s (if the port is free; a service already listening answers as well), then one connection
    listen = ("python3 -c \"import socket,time\ns=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\n"
              f"try: s.bind(('0.0.0.0',{lo}));s.listen(8)\nexcept OSError: raise SystemExit\ns.settimeout(60)\n"
              "try:\n while True: c,_=s.accept();c.close()\nexcept Exception: pass\" >/dev/null 2>&1 &")
    ssh(tgt["name"], ("doas " if lo < 1024 else "") + listen, inv=inv, timeout=30)
    import time as _t
    _t.sleep(1.5)
    conn = (f"python3 -c \"import socket\ns=socket.socket();s.settimeout(4);s.bind(('{bind}',0))\n"
            f"try: s.connect(('{tip}',{lo}));print('OPEN')\nexcept Exception as e: print('CLOSED',e)\"")
    _, o = ssh(s_node, conn, inv=inv, timeout=30)
    return {"from": f"{s_node} {bind}", "to": f"{tgt['name']} {tip}:{lo}", "what": "tcp connect", "ok": "OPEN" in o, "detail": o.strip()[:80]}


def fw_rule(r, log):
    p = r.parameters; src, dst, proto, dest, port = p["source"], p["target"], p["proto"], p["dest"], p.get("port")
    nb = Nautobot()
    log.stage("nautobot")
    ctx = nb.context(); data = ctx["data"]; pol = data.get("fw_policy", [])
    mine = next((x for x in pol if x.get("request") == r.pk and not x.get("follows")), None)
    if mine is None:
        hit = next((x for x in pol if F.covers(x, src, dst, proto, dest, port)), None)
        _check(hit is None, f"already allowed by {hit and (hit['from'] + ' → ' + hit['to'] + ' ' + hit['dest'] + ' ' + hit['proto'] + ' ' + str(hit.get('port') or ''))} — nothing changed")
        used = {x["rule"] for x in pol if x.get("rule") and x["from"] == src and x["to"] == dst}
        num = next((n for n in RULE_RANGE if n not in used), None); _check(num, f"no free rule number in {_fw_name(src, dst)}")
        mine = {"from": src, "to": dst, "dest": dest, "proto": proto, "port": port, "rule": num, "request": r.pk}
        data["fw_policy"] = pol + [mine]; nb.patch(f"extras/config-contexts/{ctx['id']}", {"data": data})
        log(f"Nautobot: {F.rule_label(mine)} in the firewall policy (zone pair {_fw_name(src, dst)})")
    else:
        log(f"Nautobot: the rule is already in the policy as rule {mine['rule']} (a retry)")
    labmodel.invalidate()
    log.stage("firewalls")
    run = _pipeline("evpn-3tier-firewalls", log)
    log.stage("checks")
    inv, ssh = _lab(); has = _fw_has(inv, ssh, mine)
    _check(all(ok for ok, _ in has.values()), "the rule is not complete on: " + ", ".join(k for k, (ok, _) in has.items() if not ok))
    log(f"on the firewalls: {', '.join(has)} carry {_fw_name(src, dst)} rule {mine['rule']} — accept {proto} to {dest}{' port ' + str(port) if port else ''}")
    log.stage("test")
    t = _probe(inv, ssh, src, dst, proto, dest, port, log)
    if t is None:
        log(f"traffic test: {'UDP has no handshake to prove' if proto == 'udp' else f'no lab VM at {dest}'} — the rule is on both firewalls; no traffic test")
    else:
        _check(t["ok"], f"traffic test failed: {t['what']} from {t['from']} to {t['to']}: {t['detail']}")
        log(f"traffic test: {t['what']} from {t['from']} to {t['to']} gets through the firewall ({t['detail']})")
    return {"rule": mine["rule"], "zone_pair": _fw_name(src, dst), "firewalls_run": run, "test": t}


def fw_rule_remove(r, log):
    p = r.parameters; src, dst, num = p["source"], p["target"], p["rule"]
    nb = Nautobot()
    log.stage("nautobot")
    ctx = nb.context(); data = ctx["data"]; pol = data.get("fw_policy", [])
    keep = [x for x in pol if not (x.get("rule") == num and x["from"] == src and x["to"] == dst and not x.get("standard"))]
    if len(keep) != len(pol):
        data["fw_policy"] = keep; nb.patch(f"extras/config-contexts/{ctx['id']}", {"data": data})
        log(f"Nautobot: rule {num} ({src} → {dst} {p['dest']} {p['proto']}{' ' + str(p['port']) if p.get('port') else ''}) out of the firewall policy")
    else:
        log(f"Nautobot: rule {num} is already out of the policy (a retry)")
    labmodel.invalidate()
    log.stage("firewalls")
    run = _pipeline("evpn-3tier-firewalls", log)
    log.stage("checks")
    inv, ssh = _lab(); has = _fw_has(inv, ssh, {"from": src, "to": dst, "rule": num, "proto": p["proto"], "dest": p["dest"], "port": p.get("port")})
    left = [k for k, (_, any_) in has.items() if any_]
    _check(not left, f"rule {num} is still on: {', '.join(left)}")
    log(f"on the firewalls: {_fw_name(src, dst)} rule {num} is gone from {', '.join(has)}")
    return {"removed": num, "zone_pair": _fw_name(src, dst), "firewalls_run": run}


# ---- move a server to another VLAN -------------------------------------------------------------------------------------------
def _twins(pol, server, old_ip, new_ip):
    """Custom rules that let traffic reach the server's old address but not its new one: the twins it needs, as
    (template rule, twin) pairs. Twins carry "follows": the server, and the new address as their destination."""
    out = []
    for x in pol:
        if x.get("standard") or x["to"] == "outside": continue
        d = ipaddress.ip_network(x["dest"], strict=False)
        if ipaddress.ip_address(old_ip) in d and ipaddress.ip_address(new_ip) not in d:
            # any rule already letting the same traffic reach the new address (the lab's own subnet rule when a server
            # moves back, or a twin from a retry): no twin
            if any(F.covers(y, x["from"], x["to"], x["proto"], new_ip, x.get("port")) for y in pol):
                continue
            if any(t["from"] == x["from"] and t["to"] == x["to"] and t["proto"] == x["proto"] and t.get("port") == x.get("port") for _, t in out):
                continue
            out.append((x, {"from": x["from"], "to": x["to"], "dest": new_ip, "proto": x["proto"], "port": x.get("port"), "follows": server}))
    return out


def move_server(r, log):
    p = r.parameters; srv, role, vrf = p["server"], p["role"], p["vrf"]
    old_v, new_v = p["from_vlan"], p["to_vlan"]
    old_a, new_a = ipaddress.ip_interface(p["from_address"]), ipaddress.ip_interface(p["to_address"])
    nb, ip = Nautobot(), Ipam(); runs = {}; tag = f"request #{r.pk}"
    dev = nb.one("dcim/devices", name=f"e3t-{srv}"); _check(dev, f"no device e3t-{srv} in Nautobot")
    ifs = {i["name"]: i for i in nb.get("dcim/interfaces", device_id=dev["id"], limit=200)["results"]}
    ns = nb.one("ipam/namespaces", name=LOCATION)
    # ---- 1 pre-flight -------------------------------------------------------------------------------------------------------
    log.stage("preflight")
    _check(role in F.MOVABLE, f"{srv}: {F.NOT_MOVABLE.get(role, 'not movable')} — nothing changed")
    ctx = nb.context(); plan = {v["vlan"]: v for v in ctx["data"]["evpn"]["vlans"]}
    _check(new_v in plan and plan[new_v]["vrf"] == vrf and "leaves" in plan[new_v]["on"], f"VLAN {new_v} is not a VLAN of VRF {vrf} on the leaves — nothing changed")
    nb_new = nb.one("ipam/ip-addresses", address=str(new_a), namespace=ns["id"])
    nb_old = nb.one("ipam/ip-addresses", address=str(old_a), namespace=ns["id"])
    done_before = nb_old is None and nb_new is not None                      # a retry after the address moved
    if not done_before:
        _check(f"bond0.{old_v}" in ifs and nb_old is not None, f"{srv} is not at {old_a} in VLAN {old_v} in Nautobot — nothing changed")
    if nb_new is not None:
        owner = nb.get("ipam/ip-address-to-interface", ip_address=nb_new["id"])["results"]
        _check(not owner or all(o["interface"]["id"] == (ifs.get(f"bond0.{new_v}") or {}).get("id") for o in owner),
               f"{new_a.ip} is assigned elsewhere in Nautobot — nothing changed")
    sec = next(s for s in ip.req("GET", "sections") if s["name"] == LOCATION)
    sub = lambda net: next((x for x in ip.req("GET", f"subnets/cidr/{net.network_address}/{net.prefixlen}") or [] if x["sectionId"] == sec["id"]), None)   # noqa: E731
    new_sub = sub(new_a.network); _check(new_sub, f"phpIPAM has no subnet {new_a.network} — nothing changed")
    have = next((a for a in ip.req("GET", f"subnets/{new_sub['id']}/addresses") or [] if a["ip"] == str(new_a.ip)), None)
    _check(have is None or tag in (have.get("description") or "") or (have.get("hostname") or "") == srv,
           f"phpIPAM: {new_a.ip} is taken ({have and (have.get('hostname') or have.get('description'))}) — nothing changed")
    log(f"pre-flight: {srv} ({role}, VRF {vrf}) moves from VLAN {old_v} {old_a} to VLAN {new_v} {new_a}; the address is free")
    # ---- 2 phpIPAM reserves the new address ------------------------------------------------------------------------------------
    log.stage("phpipam")
    if have is None:
        ip.req("POST", "addresses", json={"subnetId": new_sub["id"], "ip": str(new_a.ip), "hostname": srv, "description": f"{srv} bond0.{new_v} ({role} tier), {tag}"[:64]})
        log(f"phpIPAM: {new_a.ip} reserved for {srv}")
    else:
        log(f"phpIPAM: {new_a.ip} already reserved for {srv} (a retry)")
    # ---- 3 both VLANs on the bond: Nautobot, then the switches ----------------------------------------------------------------
    log.stage("both")
    active = nb.one("extras/statuses", name="Active"); vrf_obj = nb.one("ipam/vrfs", name=vrf, namespace=ns["id"])
    if f"bond0.{new_v}" not in ifs:
        ifs[f"bond0.{new_v}"] = nb.post("dcim/interfaces", {"device": dev["id"], "name": f"bond0.{new_v}", "type": "virtual", "parent_interface": ifs["bond0"]["id"],
                                                            "vrf": vrf_obj["id"], "status": active["id"], "description": f"VLAN {new_v} ({vrf} tier)"})
        log(f"Nautobot: e3t-{srv} bond0.{new_v} (VRF {vrf}) next to bond0.{old_v}")
    labmodel.invalidate()
    runs["switches_both"] = _pipeline("evpn-3tier-switches", log)
    inv, ssh = _lab(); leaves = next(n for n in inv["nodes"] if n["name"] == srv)
    peers = sorted({x["peer"] for x in leaves["ports"] if x.get("kind") == "access"})
    seg = next(s for s in inv["evpn"]["segments"] if s["node"] == srv)
    for lf in peers:
        out = _show(ssh, inv, lf, f"bond{seg['es']} allowed-vlan")
        _check(f"allowed-vlan {new_v}" in out, f"{lf}: bond{seg['es']} does not carry VLAN {new_v}")
    log(f"on the devices: {', '.join(peers)} carry VLANs {old_v} and {new_v} to {srv} (bond{seg['es']})")
    # ---- 4 firewall rules follow the server ------------------------------------------------------------------------------------
    log.stage("rules")
    ctx = nb.context(); data = ctx["data"]; pol = data.get("fw_policy", [])
    twins = _twins(pol, srv, str(old_a.ip), str(new_a.ip))
    if twins:
        for tmpl, tw in twins:
            used = {x["rule"] for x in pol if x.get("rule") and x["from"] == tw["from"] and x["to"] == tw["to"]}
            tw.update(rule=next(n for n in RULE_RANGE if n not in used), request=r.pk); pol = pol + [tw]
            log(f"rule for {srv}: {F.rule_label(tw)} (twin of {tmpl['from']} → {tmpl['to']} {tmpl['dest']} {tmpl['proto']} {tmpl.get('port') or ''})")
        data["fw_policy"] = pol; nb.patch(f"extras/config-contexts/{ctx['id']}", {"data": data})
        runs["firewalls"] = _pipeline("evpn-3tier-firewalls", log)
    else:
        log(f"no rule change needed: every custom rule that reaches {old_a.ip} reaches {new_a.ip} too")
    # ---- 5 the address moves: Nautobot, then the servers (and load balancers) pipelines -------------------------------------
    log.stage("servers")
    if not done_before:
        tenant = nb.one("tenancy/tenants", name=f"e3t-{vrf}")
        if nb_new is None:
            nb_new = nb.post("ipam/ip-addresses", {"address": str(new_a), "namespace": ns["id"], "status": active["id"], "type": "host",
                                                   "tenant": tenant["id"], "description": f"{srv} ({role} tier)"})
        if not nb.get("ipam/ip-address-to-interface", ip_address=nb_new["id"])["results"]:
            nb.post("ipam/ip-address-to-interface", {"ip_address": nb_new["id"], "interface": ifs[f"bond0.{new_v}"]["id"]})
        nb.delete(f"ipam/ip-addresses/{nb_old['id']}")
        log(f"Nautobot: {srv}'s address is {new_a} on bond0.{new_v} ({old_a} deleted)")
    else:
        log(f"Nautobot: {srv} is already at {new_a} (a retry)")
    labmodel.invalidate()
    runs["servers"] = _pipeline("evpn-3tier-servers", log, verify=False)
    log.stage("loadbalancers")
    if role == "web":
        runs["loadbalancers"] = _pipeline("evpn-3tier-loadbalancers", log, verify=False)
    else:
        log("not needed: the load balancers send to the web servers only")
    # ---- 6 clean-up: the old VLAN off the bond, the old address's twins out; then the whole lab verified ----------------------
    log.stage("cleanup")
    old_if = ifs.get(f"bond0.{old_v}")
    if old_if:
        nb.delete(f"dcim/interfaces/{old_if['id']}"); log(f"Nautobot: e3t-{srv} bond0.{old_v} deleted")
    ctx = nb.context(); data = ctx["data"]; pol = data.get("fw_policy", [])
    keep = [x for x in pol if not (x.get("follows") == srv and x["dest"] == str(old_a.ip))]
    if len(keep) != len(pol):
        data["fw_policy"] = keep; nb.patch(f"extras/config-contexts/{ctx['id']}", {"data": data})
        log(f"Nautobot: {len(pol) - len(keep)} rule(s) for {srv}'s old address {old_a.ip} removed")
        runs["firewalls_cleanup"] = _pipeline("evpn-3tier-firewalls", log)
    labmodel.invalidate()
    runs["switches_cleanup"] = _pipeline("evpn-3tier-switches", log)          # with verify: the whole lab, on Nautobot's model
    # ---- 7 checks ------------------------------------------------------------------------------------------------------------
    log.stage("checks")
    inv, ssh = _lab()
    _, o = ssh(srv, f"ip -br addr show bond0.{new_v}; ip route show default", inv=inv, timeout=30)
    _check(str(new_a) in o and str(ipaddress.ip_interface(p["to_gateway"]).ip) in o, f"{srv} is not at {new_a} with its gateway: {o.strip()[:120]}")
    for lf in peers:
        out = _show(ssh, inv, lf, f"bond{seg['es']} allowed-vlan")
        _check(f"allowed-vlan {new_v}" in out and f"allowed-vlan {old_v}" not in out.split(), f"{lf}: bond{seg['es']} carries {out.split()}")
    log(f"on the devices: {srv} at {new_a} via {p['to_gateway']}; {', '.join(peers)} carry only VLAN {new_v} to it")
    if role == "web":
        lbs = [n["name"] for n in inv["nodes"] if n["role"] == "lb"]
        for lb in lbs:
            _, o = ssh(lb, "doas cat /etc/haproxy/haproxy.cfg | grep -E '^ +server '", inv=inv, timeout=30)
            _check(f"{srv} {new_a.ip}:" in o, f"{lb}: HAProxy does not send to {srv} at {new_a.ip}")
        log(f"HAProxy on {', '.join(lbs)} sends to {srv} at {new_a.ip}")
    else:
        webs = [n["name"] for n in inv["nodes"] if n["role"] == "web"]
        for w in webs:
            _, o = ssh(w, "cat /etc/nginx/http.d/default.conf | grep -E 'server .*:8080'", inv=inv, timeout=30)
            _check(f"{new_a.ip}:8080" in o, f"{w}: nginx does not send to {srv} at {new_a.ip}")
        log(f"nginx on {', '.join(webs)} sends to {srv} at {new_a.ip}")
    pings = _ping_from_wan(inv, ssh, [], log)
    # ---- 8 phpIPAM releases the old address -------------------------------------------------------------------------------------
    log.stage("release")
    old_sub = sub(old_a.network)
    a = next((x for x in ip.req("GET", f"subnets/{old_sub['id']}/addresses") or [] if x["ip"] == str(old_a.ip)), None) if old_sub else None
    if a:
        ip.req("DELETE", f"addresses/{a['id']}"); log(f"phpIPAM: {old_a.ip} released")
    else:
        log(f"phpIPAM: {old_a.ip} already released (a retry)")
    log(f"done: {srv} serves from VLAN {new_v} at {new_a}")
    return {"runs": runs, "ping": pings, "server": srv, "address": str(new_a), "vlan": new_v, "rules_added": [tw for _, tw in twins]}
