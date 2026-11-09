"""Carrying out approved requests. The worker (manage.py e3t_worker) calls run(request, log); each step logs to the
request's history and is idempotent, so a failed request can be retried from the top.

add-vlan
  1 Nautobot   the VLAN in the evpn-3tier plan (config context: VLAN, VRF, VNI, gateway, span) — what the pipelines
               render from — plus the native VLAN (group evpn-3tier) and its prefix (namespace evpn-3tier, in the VRF)
  2 Gitea      the evpn-3tier-switches pipeline: inventory from Nautobot, check, apply to the leaves / borders, verify
  3 phpIPAM    the VLAN (domain evpn-3tier), the subnet (under 172.16.0.0/16, its VRF and VLAN), the gateway reservation
  4 devices    every switch in the span carries the VLAN (vlan-to-vni) and its anycast gateway
lb-peering
  1 Nautobot   the peering in the plan (config context lb_peerings), its VLAN and prefixes (peering subnet, VIPs)
  2 Gitea      evpn-3tier-switches: the peering VLAN on the pair and the load balancer's legs, eBGP importing only the VIPs
  3 Gitea      evpn-3tier-loadbalancers: the peering VLAN and the VIPs on the load balancer, BIRD to both switches
  4 phpIPAM    the VLAN, the peering subnet (switches, load balancer), the VIP prefixes
  5 checks     both sessions Established with prefixes, the first VIP prefix in the VRF on a leaf, the first VIP answering
               HTTP (/health) from a server in the VRF"""
import ipaddress
import json
import sys

from django.conf import settings

from . import labmodel
from .clients import LOCATION, Gitea, Ipam, Nautobot


class NotAutomated(Exception):
    pass


def _check(cond, msg):
    if not cond: raise RuntimeError(msg)


def add_vlan(r, log):
    p = r.parameters; vid, vrf, vni, gw, span = p["vlan_id"], p["vrf"], p["vni"], p["gateway"], p["span"]
    net = ipaddress.ip_interface(gw).network
    nb = Nautobot()
    # ---- 1 Nautobot -------------------------------------------------------------------------------------------------------
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
    labmodel.invalidate()
    # ---- 2 the switches pipeline ---------------------------------------------------------------------------------------------
    log.stage("switches")
    ok, num, url = Gitea().dispatch_and_wait("evpn-3tier-switches", log)
    _check(ok, f"evpn-3tier-switches run #{num} failed — {url}")
    log(f"evpn-3tier-switches run #{num} succeeded (inventory committed, checked, applied, verified): {url}")
    # ---- 3 phpIPAM -------------------------------------------------------------------------------------------------------------
    log.stage("phpipam")
    ip = Ipam()
    dom = next(d for d in ip.req("GET", "l2domains") if d["name"] == LOCATION)
    have = {int(x["number"]): x for x in ip.req("GET", f"l2domains/{dom['id']}/vlans") or []}
    if vid not in have: ip.req("POST", "vlan", json={"domainId": dom["id"], "number": vid, "name": p["name"], "description": f"tier VLAN, L2VNI {vni}, VRF {vrf} (request #{r.pk})"})
    vlan_id = {int(x["number"]): x for x in ip.req("GET", f"l2domains/{dom['id']}/vlans")}[vid]["vlanId"]
    sec = next(s for s in ip.req("GET", "sections") if s["name"] == LOCATION)
    vrf_id = next(x["vrfId"] for x in ip.req("GET", "vrf") if x["name"] == vrf)
    master = next(x for x in ip.req("GET", "subnets/cidr/172.16.0.0/16") if x["sectionId"] == sec["id"])
    found = ip.req("GET", f"subnets/cidr/{net.network_address}/{net.prefixlen}")
    sub = next((x for x in found or [] if x["sectionId"] == sec["id"]), None)
    if sub is None:
        ip.req("POST", "subnets", json={"subnet": str(net.network_address), "mask": net.prefixlen, "sectionId": sec["id"], "masterSubnetId": master["id"],
                                        "vrfId": vrf_id, "vlanId": vlan_id, "description": f"VLAN {vid} — {vrf} tier (L2VNI {vni}), gateway {ipaddress.ip_interface(gw).ip} (anycast); request #{r.pk}"})
        sub = next(x for x in ip.req("GET", f"subnets/cidr/{net.network_address}/{net.prefixlen}") if x["sectionId"] == sec["id"])
    gw_ip = str(ipaddress.ip_interface(gw).ip)
    if not any(a["ip"] == gw_ip for a in ip.req("GET", f"subnets/{sub['id']}/addresses") or []):
        ip.req("POST", "addresses", json={"subnetId": sub["id"], "ip": gw_ip, "hostname": f"gw-{p['name']}", "is_gateway": 1,
                                          "description": f"anycast gateway, every leaf{' + border' if 'borders' in span else ''}"[:64]})
    log(f"phpIPAM: VLAN {vid}, subnet {net} (VRF {vrf}) and the gateway {gw_ip} reserved")
    # ---- 4 on the devices ----------------------------------------------------------------------------------------------------
    log.stage("checks")
    sys.path.insert(0, str(settings.LAB_DIR / "tools"))
    from labssh import inventory, run as ssh                                  # noqa: E402
    inv = inventory()
    switches = [n["name"] for n in inv["nodes"] if (n["role"] == "leaf" and "leaves" in span) or (n["role"] == "border" and "borders" in span)]
    bad = []
    for s in switches:
        _, out = ssh(s, f"/opt/vyatta/bin/vyatta-op-cmd-wrapper show configuration commands | grep -E 'vlan-to-vni {vid} vni|pseudo-ethernet peth{vid} address'", inv=inv, timeout=120)
        out = out.replace("'", "")                                   # VyOS quotes values: vni '10040'
        if f"vlan-to-vni {vid} vni {vni}" not in out or f"peth{vid} address {ipaddress.ip_interface(gw).ip}/32" not in out: bad.append(s)
    _check(not bad, f"VLAN {vid} is missing on {', '.join(bad)} after the pipeline")
    log(f"on the devices: VLAN {vid} -> VNI {vni} and the anycast gateway {gw} on {', '.join(switches)}")
    return {"switches_run": num, "switches_run_url": url, "switches": switches}


def lb_peering(r, log):
    """A load balancer peers with its switch pair: Nautobot -> switches pipeline -> loadbalancers pipeline -> phpIPAM ->
    checks (both sessions up, the VIPs routed in the fabric, the first VIP answering HTTP from a server in the VRF)."""
    p = r.parameters; lb, vrf, asn, vlan, vni = p["device"], p["vrf"], p["asn"], p["peering_vlan"], p["vni"]
    net = ipaddress.ip_network(p["peering_subnet"]); vips = [ipaddress.ip_network(v) for v in p["vip_prefixes"]]
    nb = Nautobot()
    # ---- 1 Nautobot ------------------------------------------------------------------------------------------------------
    log.stage("nautobot")
    ctx = nb.context(); data = ctx["data"]; peerings = data.get("lb_peerings", [])
    entry = {"name": lb, "switches": p["switches"], "vrf": vrf, "asn": asn, "vlan": vlan, "vni": vni, "subnet": str(net),
             "vips": [str(v) for v in vips], "bfd": bool(p["bfd"])}
    mine = next((x for x in peerings if x["name"] == lb), None)
    if mine is None:
        data["lb_peerings"] = peerings + [entry]; nb.patch(f"extras/config-contexts/{ctx['id']}", {"data": data})
        log(f"Nautobot: peering {lb} (AS {asn}) with {', '.join(p['switches'])} in VRF {vrf} — VLAN {vlan} / VNI {vni}, {net}, VIPs {', '.join(map(str, vips))}")
    elif mine != entry:
        raise RuntimeError(f"{lb} already has another peering in Nautobot: {mine}")
    else:
        log(f"Nautobot: the peering of {lb} is already in the plan (a retry)")
    vg = nb.one("ipam/vlan-groups", name=LOCATION); tenant = nb.one("tenancy/tenants", name=f"e3t-{vrf}")
    active = nb.one("extras/statuses", name="Active"); ns = nb.one("ipam/namespaces", name=LOCATION)
    vrf_obj = nb.one("ipam/vrfs", name=vrf, namespace=ns["id"]); loc = nb.one("dcim/locations", name=LOCATION)
    v = nb.one("ipam/vlans", vid=vlan, vlan_group=vg["id"]) or nb.post("ipam/vlans", {
        "vid": vlan, "name": f"peering-{lb}", "vlan_group": vg["id"], "status": active["id"], "tenant": tenant["id"],
        "description": f"BGP peering {lb} (AS {asn}) with {', '.join(p['switches'])}, L2VNI {vni} (self-service request #{r.pk})"})
    for pfx, desc, vl in [(net, f"peering {lb}: switches .1/.2, {lb} the last address", v["id"])] + [(x, f"VIPs of {lb} (announced over BGP)", None) for x in vips]:
        if nb.one("ipam/prefixes", prefix=str(pfx), namespace=ns["id"]) is None:
            o = nb.post("ipam/prefixes", {"prefix": str(pfx), "namespace": ns["id"], "status": active["id"], "type": "network", "tenant": tenant["id"],
                                          **({"vlan": vl} if vl else {}), "description": f"{desc} — request #{r.pk}"})
            nb.patch(f"ipam/prefixes/{o['id']}", {"location": loc["id"]}); nb.post("ipam/vrf-prefix-assignments", {"vrf": vrf_obj["id"], "prefix": o["id"]})
    log(f"Nautobot: VLAN {vlan} (peering-{lb}) and the prefixes {net}, {', '.join(map(str, vips))} in VRF {vrf}")
    labmodel.invalidate()
    # ---- 2 + 3 the pipelines: the switches first (the peering VLAN must reach the load balancer), then the load balancer ---
    g = Gitea(); runs = {}
    for repo in ("evpn-3tier-switches", "evpn-3tier-loadbalancers"):
        log.stage(repo.removeprefix("evpn-3tier-"))
        ok, num, url = g.dispatch_and_wait(repo, log)
        _check(ok, f"{repo} run #{num} failed — {url}")
        log(f"{repo} run #{num} succeeded: {url}"); runs[repo] = {"run": num, "url": url}
    # ---- 4 phpIPAM ---------------------------------------------------------------------------------------------------------
    log.stage("phpipam")
    ip = Ipam()
    dom = next(d for d in ip.req("GET", "l2domains") if d["name"] == LOCATION)
    have = {int(x["number"]): x for x in ip.req("GET", f"l2domains/{dom['id']}/vlans") or []}
    if vlan not in have: ip.req("POST", "vlan", json={"domainId": dom["id"], "number": vlan, "name": f"peering-{lb}", "description": f"BGP peering {lb} (AS {asn}), L2VNI {vni} (request #{r.pk})"})
    vlan_id = {int(x["number"]): x for x in ip.req("GET", f"l2domains/{dom['id']}/vlans")}[vlan]["vlanId"]
    sec = next(s for s in ip.req("GET", "sections") if s["name"] == LOCATION)
    vrf_id = next(x["vrfId"] for x in ip.req("GET", "vrf") if x["name"] == vrf)
    master = next(x for x in ip.req("GET", "subnets/cidr/172.16.0.0/16") if x["sectionId"] == sec["id"])
    def subnet(pfx, desc, vlan_id=None):
        found = next((x for x in ip.req("GET", f"subnets/cidr/{pfx.network_address}/{pfx.prefixlen}") or [] if x["sectionId"] == sec["id"]), None)
        if found is None:
            ip.req("POST", "subnets", json={"subnet": str(pfx.network_address), "mask": pfx.prefixlen, "sectionId": sec["id"], "masterSubnetId": master["id"],
                                            "vrfId": vrf_id, **({"vlanId": vlan_id} if vlan_id else {}), "description": desc})
            found = next(x for x in ip.req("GET", f"subnets/cidr/{pfx.network_address}/{pfx.prefixlen}") if x["sectionId"] == sec["id"])
        return found
    def address(sub, a, host, desc):
        if not any(x["ip"] == str(a) for x in ip.req("GET", f"subnets/{sub['id']}/addresses") or []):
            ip.req("POST", "addresses", json={"subnetId": sub["id"], "ip": str(a), "hostname": host, "description": desc[:64]})
    ps = subnet(net, f"BGP peering {lb} (AS {asn}) with {', '.join(p['switches'])}, VLAN {vlan} / L2VNI {vni}; request #{r.pk}", vlan_id)
    sw = sorted(p["switches"])
    for i, s_ in enumerate(sw): address(ps, net.network_address + 1 + i, s_, f"{s_} br0.{vlan}, eBGP to {lb}")
    address(ps, net.broadcast_address - 1, lb, f"{lb} bond0.{vlan}, eBGP AS {asn} (BIRD)")
    for x in vips:
        vs = subnet(x, f"VIPs of {lb}, announced over BGP (AS {asn}); request #{r.pk}")
        address(vs, x.network_address + 1, f"vip-{lb}", f"VIP on {lb} (lo), HAProxy")
    log(f"phpIPAM: VLAN {vlan}, the peering subnet {net} with {', '.join(sw)} and {lb}, the VIP prefixes")
    # ---- 5 checks ----------------------------------------------------------------------------------------------------------
    log.stage("checks")
    sys.path.insert(0, str(settings.LAB_DIR / "tools"))
    from labssh import inventory, run as ssh                                  # noqa: E402
    import time as _t
    inv = inventory(); lb_ip = str(net.broadcast_address - 1)
    deadline, down = _t.time() + 120, sw
    while down and _t.time() < deadline:
        down = []
        for s_ in sw:
            _, out = ssh(s_, f"vtysh -c 'show bgp vrf {vrf} summary json'", inv=inv, timeout=60)
            try: st = json.loads(out)["ipv4Unicast"]["peers"].get(lb_ip, {})
            except (ValueError, KeyError): st = {}
            if st.get("state") != "Established" or not st.get("pfxRcd"): down.append(s_)
        if down: _t.sleep(10)
    _check(not down, f"BGP to {lb} ({lb_ip}) not Established with prefixes on {', '.join(down)}")
    log(f"BGP: {', '.join(sw)} have {lb} ({lb_ip}, AS {asn}) Established, its VIP prefixes received")
    leaf = sorted(n["name"] for n in inv["nodes"] if n["role"] == "leaf")[0]
    _, out = ssh(leaf, f"vtysh -c 'show ip route vrf {vrf} {vips[0]} json'", inv=inv, timeout=60)
    _check(str(vips[0]) in out, f"{vips[0]} is not in VRF {vrf} on {leaf}")
    log(f"routing: {leaf} has {vips[0]} in VRF {vrf} (EVPN type-5 from the borders)")
    vip = str(vips[0].network_address + 1)
    client = next(n["name"] for n in inv["nodes"] if n.get("net") and n["net"]["vrf"] == vrf and n["role"] != "lb")
    rc, out = ssh(client, f"curl -s -m5 http://{vip}/health", inv=inv, timeout=30)
    _check(f"{lb} ok" in out, f"http://{vip}/health from {client}: {out.strip()[:100] or 'no answer'}")
    log(f"data plane: http://{vip}/health from {client} answered '{out.strip()}' (HAProxy on {lb}, reached through the fabric)")
    return {"runs": runs, "vip": vip, "switches_run_url": runs["evpn-3tier-switches"]["url"], "switches_run": runs["evpn-3tier-switches"]["run"],
            "lb_run_url": runs["evpn-3tier-loadbalancers"]["url"], "lb_run": runs["evpn-3tier-loadbalancers"]["run"]}


EXECUTORS = {"add-vlan": add_vlan, "lb-peering": lb_peering}
