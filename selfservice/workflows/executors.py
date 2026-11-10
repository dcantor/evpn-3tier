"""Carrying out approved requests. The worker (manage.py e3t_worker) calls run(request, log); each step logs to the
request's history and is idempotent, so a failed request can be retried from the top.

add-vlan       (phpIPAM is the source of truth for addressing: it is written first)
  1 phpIPAM    reserve the VLAN (domain evpn-3tier), the subnet (under 172.16.0.0/16, its VRF and VLAN) and the gateway
               (its first host) — refused, with nothing changed, if any of it was taken since the request was submitted
  2 Nautobot   from phpIPAM's reservation (read back): the VLAN in the evpn-3tier plan (config context: VLAN, VRF, VNI,
               gateway, span) — what the pipelines render from — plus the native VLAN and its prefix (in the VRF)
  2b rules     Standard Firewall Rules in Nautobot's fw_policy: ICMP to the VLAN from every other zone (fwrules.py)
  3 Gitea      the evpn-3tier-switches pipeline: inventory from Nautobot, check, apply to the leaves / borders, verify;
               then evpn-3tier-firewalls: the subnet into PUBLIC (what outside, and so the WAN, learns), the ICMP rules
  4 checks     (below) + 5 ping   from the WAN: every web, app and db VM and the new VLAN's gateway
  4 checks     every switch in the span carries the VLAN (vlan-to-vni) and its anycast gateway; both borders permit the
               subnet to the WAN (prefix-list FABRIC-SUBNETS, called by route-map TO-WAN on the eBGP sessions to the WAN)
remove-vlan    (the mirror of add-vlan; phpIPAM last: addresses are released only once nothing uses them)
  1 pre-flight removable (not lab.conf's, no hosts, not its VRF's last), phpIPAM holds only the gateway — else nothing changes
  2 Nautobot   out of the plan; its prefix and VLAN object deleted
  3 Gitea      evpn-3tier-switches: managed deletions take off every line rendered no more
  4 checks     no trace of the VLAN, its VNI or subnet on any switch
  5 phpIPAM    the subnet (with its gateway address) and the VLAN released
lb-peering     add a new load balancer / delete the last one added: lbs.py"""
import ipaddress
import json
import re
import sys

from django.conf import settings

from . import allocate, fwrules, labmodel
from .clients import LOCATION, Gitea, Ipam, Nautobot


class NotAutomated(Exception):
    pass


def _check(cond, msg):
    if not cond: raise RuntimeError(msg)


def _lab():
    """(inventory, ssh): the lab model from Nautobot — the live plan, with self-service VLANs and moved servers; lab.conf
    does not know them — and tools/labssh.py's SSH runner."""
    sys.path.insert(0, str(settings.LAB_DIR / "tools"))
    import labssh                                                             # noqa: E402
    from .clients import NAUTOBOT_URL, _nms
    return labssh.nautobot_model(NAUTOBOT_URL, _nms("NAUTOBOT_SUPERUSER_API_TOKEN")), labssh.run


def _fw_and_wan(inv, ssh, vid, net, present, log):
    """Both firewalls have (or no longer have) the subnet in PUBLIC, and the WAN learns (or no longer has) its route —
    the latter given BGP up to a minute to converge."""
    import time as _t
    fws = [n["name"] for n in inv["nodes"] if n["role"] == "fw"]
    bad = []
    for fw in fws:
        _, out = ssh(fw, "/opt/vyatta/bin/vyatta-op-cmd-wrapper show configuration commands | grep -E 'prefix-list PUBLIC rule'", inv=inv, timeout=120)
        has = f"prefix-list PUBLIC rule {vid} prefix {net}" in out.replace("'", "")
        if has != present: bad.append(fw)
    _check(not bad, f"PUBLIC on {', '.join(bad)}: {net} {'missing' if present else 'still there'}")
    wan = next(n["name"] for n in inv["nodes"] if n["role"] == "wan")
    deadline, seen = _t.time() + 60, None
    while _t.time() < deadline:
        _, out = ssh(wan, f"vtysh -c 'show ip route {net} longer-prefixes'", inv=inv, timeout=60)
        seen = bool(re.search(rf"^B\S*\s+{re.escape(str(net))}\s", out, re.M))     # a BGP route line for exactly the subnet
        if seen == present: break
        _t.sleep(10)
    _check(seen == present, f"the WAN {'has not learned' if present else 'still has'} {net}")
    log(f"routes to outside: {', '.join(fws)} {'export' if present else 'no longer export'} {net} (prefix-list PUBLIC rule {vid}); "
        f"the WAN {'learns' if present else 'no longer has'} it — traffic stays subject to the firewall zone policy")


def _fw_standard(inv, ssh, vid, vrf, net, present, log):
    """Both firewalls carry (or no longer carry) the VLAN's Standard Firewall Rules: rule 1000 + VLAN ID, ICMP to its
    subnet, in every zone pair from another zone to its VRF."""
    n = fwrules.rule_number(vid); fws = [x["name"] for x in inv["nodes"] if x["role"] == "fw"]
    pairs = [f"{z.upper()}-{vrf.upper()}" for z in fwrules.ZONES if z != vrf]
    bad = []
    for fw in fws:
        _, out = ssh(fw, f"/opt/vyatta/bin/vyatta-op-cmd-wrapper show configuration commands | grep -E ' rule {n} '", inv=inv, timeout=120)
        out = out.replace("'", "")
        for pr in pairs:
            has = (f"name {pr} rule {n} protocol icmp" in out and f"name {pr} rule {n} destination address {net}" in out
                   and f"name {pr} rule {n} action accept" in out)
            if has != present: bad.append(f"{fw} {pr}")
    _check(not bad, f"Standard Firewall Rules for VLAN {vid} {'missing' if present else 'still there'} on: {', '.join(bad)}")
    log(f"Standard Firewall Rules: {', '.join(fws)} {'allow' if present else 'no longer have'} ICMP to {net} "
        f"({', '.join(pairs)}, rule {n})")


def _ping_from_wan(inv, ssh, extra, log):
    """Ping every web, app and db VM (and `extra` targets) from the WAN, sourced from the WAN's client range — the path
    a user outside takes: WAN -> border -> outside VRF -> firewall (Standard Firewall Rules) -> tier VRF -> VM."""
    import re as _re
    wan = next(x["name"] for x in inv["nodes"] if x["role"] == "wan")
    src = str(ipaddress.ip_network(inv["wan"]["clients"]).network_address + 1)
    targets = [(f"{x['name']} ({x['net']['vrf']})", x["net"]["address"].split("/")[0]) for x in inv["nodes"]
               if x["role"] in ("web", "app", "db") and x.get("net")] + list(extra)
    script = "; ".join(f"echo '@@{i}'; ping -n -c 3 -i 0.3 -W 2 -I {src} {ip} | tail -2" for i, (_, ip) in enumerate(targets))
    _, out = ssh(wan, script, inv=inv, timeout=120)
    parts = _re.split(r"^@@(\d+)\s*$", out, flags=_re.M)
    res = {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}
    rows, failed = [], []
    for i, (label, ip) in enumerate(targets):
        o = res.get(i, "")
        m = _re.search(r"(\d+) received", o); rtt = _re.search(r"= [\d.]+/([\d.]+)/", o)
        got = int(m.group(1)) if m else 0
        rows.append({"target": label, "ip": ip, "received": got, "sent": 3, "rtt_ms": float(rtt.group(1)) if rtt else None})
        if got < 2: failed.append(f"{label} {ip} ({got}/3)")
    log("ping from the WAN (" + src + "): " + "; ".join(f"{r['target']} {r['ip']} {r['received']}/3" + (f" {r['rtt_ms']:.1f} ms" if r["rtt_ms"] else "") for r in rows))
    _check(not failed, f"not reachable by ping from the WAN: {', '.join(failed)}")
    log(f"ping test passed: all {len(rows)} targets answer from the WAN")
    return rows


def _vlan_args(r):
    p = r.parameters
    return p, p["vlan_id"], p["vrf"], p["vni"], p["gateway"], p["span"], ipaddress.ip_interface(p["gateway"]).network


def _vlan_phpipam(r, log):
    """Add a VLAN, step 1 (also Add a VRF's first VLAN): reserve in phpIPAM; returns the gateway phpIPAM holds."""
    p, vid, vrf, vni, gw, span, net = _vlan_args(r)
    # ---- 1 phpIPAM — the source of truth for addressing: reserve first --------------------------------------------------------
    log.stage("phpipam")
    ip = Ipam(); tag = f"request #{r.pk}"
    dom = next(d for d in ip.req("GET", "l2domains") if d["name"] == LOCATION)
    have = {int(x["number"]): x for x in ip.req("GET", f"l2domains/{dom['id']}/vlans") or []}
    sec = next(s for s in ip.req("GET", "sections") if s["name"] == LOCATION)
    vrf_id = next(x["vrfId"] for x in ip.req("GET", "vrf") if x["name"] == vrf)
    master = next(x for x in ip.req("GET", "subnets/cidr/172.16.0.0/16") if x["sectionId"] == sec["id"])
    sub = next((x for x in ip.req("GET", f"subnets/cidr/{net.network_address}/{net.prefixlen}") or [] if x["sectionId"] == sec["id"]), None)
    # every check before the first write: refused means nothing changed in phpIPAM
    if vid in have:     # already there: ours (a retry) or taken since the request was submitted
        _check(tag in (have[vid].get("description") or ""), f"phpIPAM: VLAN {vid} was taken since the request was submitted ({have[vid].get('name')}) — nothing changed; submit a new request")
    if sub is not None:
        _check(tag in (sub.get("description") or ""), f"phpIPAM: {net} was taken since the request was submitted — nothing changed; submit a new request")
    else:
        _check(str(net) in {str(n) for n in allocate.free_subnets(net.prefixlen)},
               f"phpIPAM: {net} is no longer free (it overlaps a subnet reserved since the request was submitted) — nothing changed; submit a new request")
    # reserve
    if vid not in have:
        ip.req("POST", "vlan", json={"domainId": dom["id"], "number": vid, "name": p["name"], "description": f"tier VLAN, L2VNI {vni}, VRF {vrf} ({tag})"})
    vlan_id = {int(x["number"]): x for x in ip.req("GET", f"l2domains/{dom['id']}/vlans")}[vid]["vlanId"]
    if sub is None:
        ip.req("POST", "subnets", json={"subnet": str(net.network_address), "mask": net.prefixlen, "sectionId": sec["id"], "masterSubnetId": master["id"],
                                        "vrfId": vrf_id, "vlanId": vlan_id, "description": f"VLAN {vid} — {vrf} tier (L2VNI {vni}), gateway {ipaddress.ip_interface(gw).ip} (anycast); {tag}"})
        sub = next(x for x in ip.req("GET", f"subnets/cidr/{net.network_address}/{net.prefixlen}") if x["sectionId"] == sec["id"])
    gw_ip = str(ipaddress.ip_interface(gw).ip)
    if not any(a["ip"] == gw_ip for a in ip.req("GET", f"subnets/{sub['id']}/addresses") or []):
        ip.req("POST", "addresses", json={"subnetId": sub["id"], "ip": gw_ip, "hostname": f"gw-{p['name']}", "is_gateway": 1,
                                          "description": f"anycast gateway, every leaf{' + border' if 'borders' in span else ''}"[:64]})
    # what Nautobot gets is what phpIPAM holds: read it back
    net = ipaddress.ip_network(f"{sub['subnet']}/{sub['mask']}")
    gw_ip = next(a["ip"] for a in ip.req("GET", f"subnets/{sub['id']}/addresses") if a["ip"] == str(net.network_address + 1))
    gw = f"{gw_ip}/{net.prefixlen}"
    log(f"phpIPAM: reserved VLAN {vid} ({p['name']}), subnet {net} in VRF {vrf} and the gateway {gw_ip} (the subnet's first host)")
    return gw


def _vlan_nautobot(r, gw, log):
    """Step 2: the VLAN in Nautobot's plan, its native VLAN and prefix."""
    p, vid, vrf, vni, _, span, net = _vlan_args(r)
    nb = Nautobot()
    # ---- 2 Nautobot — from phpIPAM's reservation ----------------------------------------------------------------------------
    log.stage("nautobot")
    ctx = nb.context(); data = ctx["data"]; vlans = data["evpn"]["vlans"]
    entry = {"vlan": vid, "vrf": vrf, "vni": vni, "gateway": gw, "on": span.split("+")}
    mine = next((v for v in vlans if v["vlan"] == vid), None)
    if mine is None:
        clash = [v["vlan"] for v in vlans if v["vni"] == vni or ipaddress.ip_interface(v["gateway"]).network.overlaps(net)]
        _check(not clash, f"VLAN {vid}: its VNI or subnet clashes with VLAN(s) {clash} in Nautobot now — rejected at execution")
        data["evpn"]["vlans"] = vlans + [entry]; nb.patch(f"extras/config-contexts/{ctx['id']}", {"data": data})
        log(f"Nautobot: VLAN {vid} added to the evpn-3tier plan (VRF {vrf}, VNI {vni}, gateway {gw}, on {span})")
    elif mine != entry:
        raise RuntimeError(f"VLAN {vid} is already in Nautobot's plan with other settings: {mine}")
    else:
        log(f"Nautobot: VLAN {vid} already in the plan (a retry)")
    vg = nb.one("ipam/vlan-groups", name=LOCATION); tenant = nb.one("tenancy/tenants", name=f"e3t-{vrf}")
    active = nb.one("extras/statuses", name="Active")
    v = nb.one("ipam/vlans", vid=vid, vlan_group=vg["id"])
    if v is None:
        v = nb.post("ipam/vlans", {"vid": vid, "name": p["name"], "vlan_group": vg["id"], "status": active["id"], "tenant": tenant["id"],
                                   "description": f"tier VLAN, L2VNI {vni}, anycast gateway {gw} on {span} (self-service request #{r.pk})"})
        log(f"Nautobot: native VLAN {vid} ({p['name']}) in group {LOCATION}")
    ns = nb.one("ipam/namespaces", name=LOCATION); vrf_obj = nb.one("ipam/vrfs", name=vrf, namespace=ns["id"])
    pf = nb.one("ipam/prefixes", prefix=str(net), namespace=ns["id"])
    if pf is None:
        pf = nb.post("ipam/prefixes", {"prefix": str(net), "namespace": ns["id"], "status": active["id"], "type": "network", "tenant": tenant["id"],
                                       "vlan": v["id"], "description": f"VLAN {vid} ({vrf} tier) — self-service request #{r.pk}"})
        nb.patch(f"ipam/prefixes/{pf['id']}", {"location": nb.one("dcim/locations", name=LOCATION)["id"]})
        nb.post("ipam/vrf-prefix-assignments", {"vrf": vrf_obj["id"], "prefix": pf["id"]})
        log(f"Nautobot: prefix {net} in namespace {LOCATION}, VRF {vrf}")


def _vlan_rules(r, gw, log):
    """Step 2b: the VLAN's Standard Firewall Rules."""
    p, vid, vrf, vni, _, span, net = _vlan_args(r); nb = Nautobot()
    # ---- 2b Standard Firewall Rules: ICMP to the VLAN from every other zone, in Nautobot's fw_policy ----------------------
    log.stage("rules")
    ctx = nb.context(); data = ctx["data"]; before = data.get("fw_policy", [])
    zs = fwrules.zones(data)
    after, _ = fwrules.merge(before, vid, vrf, net, zs)
    mine = fwrules.for_vlan(vid, vrf, net, zs)
    if all(m in before for m in mine):
        log(f"Standard Firewall Rules: VLAN {vid}'s are already in Nautobot (a retry)")
    else:
        data["fw_policy"] = after; nb.patch(f"extras/config-contexts/{ctx['id']}", {"data": data})
        log(f"Standard Firewall Rules in Nautobot: ICMP to VLAN {vid} ({net}) from {', '.join(m['from'] for m in mine)} — "
            f"firewall rule {fwrules.rule_number(vid)} in each zone pair")
    labmodel.invalidate()


def _vlan_checks(r, gw, log):
    """Step 5: the VLAN and gateway on the switches, the WAN export, the firewalls; returns (inv, ssh, switches)."""
    p, vid, vrf, vni, _, span, net = _vlan_args(r)
    # ---- 5 on the devices ----------------------------------------------------------------------------------------------------
    log.stage("checks")
    inv, ssh = _lab()
    switches = [n["name"] for n in inv["nodes"] if (n["role"] == "leaf" and "leaves" in span) or (n["role"] == "border" and "borders" in span)]
    bad = []
    for s in switches:
        _, out = ssh(s, f"/opt/vyatta/bin/vyatta-op-cmd-wrapper show configuration commands | grep -E 'vlan-to-vni {vid} vni|pseudo-ethernet peth{vid} address'", inv=inv, timeout=120)
        out = out.replace("'", "")                                   # VyOS quotes values: vni '10040'
        if f"vlan-to-vni {vid} vni {vni}" not in out or f"peth{vid} address {ipaddress.ip_interface(gw).ip}/32" not in out: bad.append(s)
    _check(not bad, f"VLAN {vid} is missing on {', '.join(bad)} after the pipeline")
    log(f"on the devices: VLAN {vid} -> VNI {vni} and the anycast gateway {gw} on {', '.join(switches)}")
    # the WAN export policy: every border permits the new subnet to the WAN — prefix-list FABRIC-SUBNETS has its rule,
    # route-map TO-WAN calls the prefix-list, and the eBGP sessions to the WAN export through the route-map
    borders = [n["name"] for n in inv["nodes"] if n["role"] == "border"]
    wan_peers = {w["border"]: w["wan_ip"].split("/")[0] for w in inv["wan"]["links"]}
    bad = []
    for b in borders:
        _, out = ssh(b, "/opt/vyatta/bin/vyatta-op-cmd-wrapper show configuration commands | grep -E 'FABRIC-SUBNETS|TO-WAN'", inv=inv, timeout=120)   # VyOS quotes values (export 'TO-WAN'): match loosely
        out = out.replace("'", "")
        want = [f"prefix-list FABRIC-SUBNETS rule {vid} prefix {net}", f"prefix-list FABRIC-SUBNETS rule {vid} action permit",
                "route-map TO-WAN rule 10 match ip address prefix-list FABRIC-SUBNETS",
                f"neighbor {wan_peers[b]} address-family ipv4-unicast route-map export TO-WAN"]
        if missing := [w for w in want if w not in out]: bad.append(f"{b} ({'; '.join(missing)})")
    _check(not bad, f"the WAN export policy lacks {net} on: {', '.join(bad)}")
    log(f"WAN export policy: {', '.join(borders)} permit {net} to the WAN (prefix-list FABRIC-SUBNETS rule {vid}, route-map TO-WAN on the eBGP sessions to the WAN)")
    _fw_and_wan(inv, ssh, vid, net, present=True, log=log)
    _fw_standard(inv, ssh, vid, vrf, net, present=True, log=log)
    return inv, ssh, switches


def add_vlan(r, log):
    p, vid, vrf, vni, gw, span, net = _vlan_args(r)
    gw = _vlan_phpipam(r, log); net = ipaddress.ip_interface(gw).network
    _vlan_nautobot(r, gw, log)
    _vlan_rules(r, gw, log)
    # ---- 3 the switches pipeline (renders from Nautobot) ---------------------------------------------------------------------------------------------
    log.stage("switches")
    ok, num, url = Gitea().dispatch_and_wait("evpn-3tier-switches", log)
    _check(ok, f"evpn-3tier-switches run #{num} failed — {url}")
    log(f"evpn-3tier-switches run #{num} succeeded (inventory committed, checked, applied, verified): {url}")
    # ---- 4 the firewalls pipeline: the subnet in PUBLIC (what outside, and so the WAN, learns) -------------------------------
    log.stage("firewalls")
    fok, fnum, furl = Gitea().dispatch_and_wait("evpn-3tier-firewalls", log)
    _check(fok, f"evpn-3tier-firewalls run #{fnum} failed — {furl}")
    log(f"evpn-3tier-firewalls run #{fnum} succeeded (PUBLIC rule {vid}, the standard ICMP rules {fwrules.rule_number(vid)}): {furl}")
    inv, ssh, switches = _vlan_checks(r, gw, log)
    # ---- 6 the ping test: from the WAN, every web, app and db VM, and the new VLAN's gateway --------------------------------
    log.stage("ping")
    pings = _ping_from_wan(inv, ssh, [(f"VLAN {vid} gateway (leaves)", str(ipaddress.ip_interface(gw).ip))], log)
    return {"ping": pings, "switches_run": num, "switches_run_url": url, "firewalls_run": fnum, "firewalls_run_url": furl, "switches": switches}


def remove_vlan(r, log):
    """Remove a VLAN and every trace of it — the mirror of add-vlan: pre-flight -> Nautobot -> the switches pipeline
    (its managed deletions take off what is rendered no more) -> checks -> phpIPAM last (addresses are released only once
    nothing uses them). Every step idempotent: a retry skips what is already gone."""
    import re as _re
    p = r.parameters; vid, vni, vrf = p["vlan_id"], p["vni"], p["vrf"]; net = ipaddress.ip_network(p["subnet"])
    nb, ip = Nautobot(), Ipam()
    # ---- 1 pre-flight: refuse before changing anything ---------------------------------------------------------------------
    log.stage("preflight")
    ctx = nb.context(); data = ctx["data"]
    entry = next((v for v in data["evpn"]["vlans"] if v["vlan"] == vid), None)
    if entry is not None:
        labmodel.invalidate(); m = labmodel.model()
        v = next(x for x in m["vlans"] if x["vlan"] == vid)
        _check(not v["blocked"], f"VLAN {vid} cannot be removed: {v['blocked']} — nothing changed")
        _check(ipaddress.ip_interface(entry["gateway"]).network == net, f"VLAN {vid}'s subnet in Nautobot is {entry['gateway']}, not {net} as requested — nothing changed")
    sec = next(x for x in ip.req("GET", "sections") if x["name"] == LOCATION)
    sub = next((x for x in ip.req("GET", f"subnets/cidr/{net.network_address}/{net.prefixlen}") or [] if x["sectionId"] == sec["id"]), None)
    if sub is not None:
        others = [a["ip"] for a in ip.req("GET", f"subnets/{sub['id']}/addresses") or [] if a["ip"] != str(net.network_address + 1)]
        _check(not others, f"phpIPAM still has addresses in {net} besides the gateway: {', '.join(others[:8])} — release them first; nothing changed")
    log(f"pre-flight: VLAN {vid} is removable (not the lab's design, no hosts, not VRF {vrf}'s last VLAN); phpIPAM holds only the gateway in {net}")
    # ---- 2 Nautobot -----------------------------------------------------------------------------------------------------------
    log.stage("nautobot")
    if entry is not None:
        data["evpn"]["vlans"] = [v for v in data["evpn"]["vlans"] if v["vlan"] != vid]
        nb.patch(f"extras/config-contexts/{ctx['id']}", {"data": data})
        log(f"Nautobot: VLAN {vid} out of the evpn-3tier plan")
    else:
        log(f"Nautobot: VLAN {vid} already out of the plan (a retry)")
    ctx = nb.context(); data = ctx["data"]
    pol, n_removed = fwrules.drop(data.get("fw_policy", []), vid)
    if n_removed:
        data["fw_policy"] = pol; nb.patch(f"extras/config-contexts/{ctx['id']}", {"data": data})
        log(f"Nautobot: VLAN {vid}'s {n_removed} Standard Firewall Rules (ICMP, rule {fwrules.rule_number(vid)}) removed")
    ns = nb.one("ipam/namespaces", name=LOCATION)
    pf = nb.one("ipam/prefixes", prefix=str(net), namespace=ns["id"])
    if pf: nb.delete(f"ipam/prefixes/{pf['id']}"); log(f"Nautobot: prefix {net} deleted")
    vg = nb.one("ipam/vlan-groups", name=LOCATION); vo = nb.one("ipam/vlans", vid=vid, vlan_group=vg["id"])
    if vo: nb.delete(f"ipam/vlans/{vo['id']}"); log(f"Nautobot: VLAN object {vid} ({vo['name']}) deleted")
    labmodel.invalidate()
    # ---- 3 the switches pipeline: renders from Nautobot; deletes what is rendered no more -----------------------------------------
    log.stage("switches")
    ok, num, url = Gitea().dispatch_and_wait("evpn-3tier-switches", log)
    _check(ok, f"evpn-3tier-switches run #{num} failed — {url}")
    log(f"evpn-3tier-switches run #{num} succeeded (the VLAN's lines deleted by its managed deletions, verified): {url}")
    log.stage("firewalls")
    fok, fnum, furl = Gitea().dispatch_and_wait("evpn-3tier-firewalls", log)
    _check(fok, f"evpn-3tier-firewalls run #{fnum} failed — {furl}")
    log(f"evpn-3tier-firewalls run #{fnum} succeeded (PUBLIC rule {vid} deleted): {furl}")
    # ---- 4 checks: no trace on any switch ------------------------------------------------------------------------------------
    log.stage("checks")
    inv, ssh = _lab()
    switches = [n["name"] for n in inv["nodes"] if n["role"] in ("leaf", "border", "spine")]
    pats = [rf"vlan-to-vni {vid} ", rf"\bpeth{vid}\b", rf"allowed-vlan {vid}$", rf"\bvni {vni}\b", rf"FABRIC-SUBNETS rule {vid} ", rf"\bvif {vid}\b",
            rf"\b{_re.escape(str(net))}\b"]
    left = {}
    for sw in switches:
        _, out = ssh(sw, f"/opt/vyatta/bin/vyatta-op-cmd-wrapper show configuration commands | grep -E '{vid}|{vni}|{net.network_address}'", inv=inv, timeout=120)
        hits = [l for l in out.replace("'", "").splitlines() if any(_re.search(pt, l) for pt in pats)]
        if hits: left[sw] = hits
    _check(not left, "still on the switches: " + "; ".join(f"{k}: {v[0]}" + (f" (+{len(v) - 1})" if len(v) > 1 else "") for k, v in left.items()))
    log(f"on the devices: no trace of VLAN {vid} / VNI {vni} / {net} on {', '.join(switches)}")
    _fw_and_wan(inv, ssh, vid, net, present=False, log=log)
    _fw_standard(inv, ssh, vid, vrf, net, present=False, log=log)
    # ---- 5 phpIPAM last: release the addresses --------------------------------------------------------------------------------
    log.stage("phpipam")
    if sub is not None:
        ip.req("DELETE", f"subnets/{sub['id']}"); log(f"phpIPAM: subnet {net} and its gateway address released")
    dom = next(d for d in ip.req("GET", "l2domains") if d["name"] == LOCATION)
    pv = next((x for x in ip.req("GET", f"l2domains/{dom['id']}/vlans") or [] if int(x["number"]) == vid), None)
    if pv is not None:
        ip.req("DELETE", f"vlan/{pv['vlanId']}"); log(f"phpIPAM: VLAN {vid} ({pv.get('name')}) released")
    left_ipam = [x for x in ip.req("GET", f"subnets/cidr/{net.network_address}/{net.prefixlen}") or [] if x["sectionId"] == sec["id"]]
    _check(not left_ipam, f"phpIPAM still has {net}")
    log(f"done: VLAN {vid} is gone from the switches, Nautobot and phpIPAM — its ID and subnet are free again")
    return {"switches_run": num, "switches_run_url": url, "firewalls_run": fnum, "firewalls_run_url": furl,
            "removed": {"vlan": vid, "vni": vni, "subnet": str(net)}}


EXECUTORS = {"remove-vlan": remove_vlan, "add-vlan": add_vlan}
from . import changes as _changes                                           # noqa: E402  (changes.py imports this module's helpers)
EXECUTORS.update({"vlan-span": _changes.vlan_span, "fw-rule": _changes.fw_rule, "fw-rule-remove": _changes.fw_rule_remove,
                  "move-server": _changes.move_server, "port": _changes.port_config, "port-profile": _changes.port_profile})
from . import leafpair as _leafpair                                         # noqa: E402
EXECUTORS.update({"add-leaf-pair": _leafpair.add_pair, "remove-leaf-pair": _leafpair.remove_pair})
from . import vrfs as _vrfs                                                 # noqa: E402
EXECUTORS.update({"add-vrf": _vrfs.add_vrf, "delete-vrf": _vrfs.delete_vrf})
from . import lbs as _lbs                                                   # noqa: E402
EXECUTORS.update({"lb-peering": _lbs.run})
