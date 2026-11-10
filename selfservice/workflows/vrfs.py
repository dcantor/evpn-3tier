"""Add or delete a VRF — a tier zone behind the firewall pair, like web, app and db.

A VRF here is a security zone: an L3VNI on the borders (and on the leaves once a leaf VLAN uses it), a firewall transit
VLAN (/28) with eBGP from both borders to fw1 / fw2, and a zone in the firewalls' policy — deny by default, with the
Standard Firewall Rules (ICMP to every VLAN from every other zone). The templates loop over Nautobot's plan
(evpn.vrfs, evpn.transit), so a VRF is Nautobot + phpIPAM data and two pipeline runs.

Added VRF number z (1-20) — ranges clear of lab.conf's VRFs, tier VLANs and LB peerings:
  table 2000+z   L3VNI 51000+z (route target <rt_as>:<L3VNI>)   L3VNI VLAN 3900+z   transit VLAN 3800+z, L2VNI 13800+z
  transit subnet: the z-th /28 of 172.16.240.0/23 (a phpIPAM pool, so Add a VLAN never allocates from it):
                  borders .2 / .3, fw1 .11, fw2 .12        probe base 10.255.(100+z)

add-vrf     pre-flight -> phpIPAM (VRF, VLANs, transit subnet + addresses, probe subnet; the first VLAN) -> Nautobot (tenant,
            route target, VRF, VLANs, transit prefix, the plan; the first VLAN) -> Standard Firewall Rules (ICMP from the
            new zone to every VLAN; to the first VLAN from every zone) -> switches pipeline (no verify: the firewalls do not
            know the zone yet) -> firewalls pipeline (whole lab verified) -> checks -> ping from the WAN
delete-vrf  pre-flight (an added VRF with nothing in it: no VLAN, server, peering, custom rule) -> Nautobot (the plan, its
            standard rules, the objects) -> firewalls pipeline (no verify) -> switches pipeline (whole lab verified) ->
            checks (gone from every switch and firewall) -> phpIPAM released -> ping from the WAN"""
import ipaddress
import re

from . import fwrules, labmodel
from .clients import LOCATION, Ipam, Nautobot

BASE = ("web", "app", "db", "outside")               # lab.conf's VRFs: never deleted
MAX_ADDED = 20
POOL = ipaddress.ip_network("172.16.240.0/23")
NAME = re.compile(r"^[a-z][a-z0-9]{1,7}$")
RESERVED = set(BASE) | {"default", "mgmt", "sync", "local", "fabric", "transit", "lb", "fw", "wan", "ipam", "spine", "leaf", "border"}
OP = "/opt/vyatta/bin/vyatta-op-cmd-wrapper"


def _check(cond, msg):
    if not cond: raise RuntimeError(msg)


def _helpers():
    from .changes import _pipeline
    from .executors import _lab, _ping_from_wan, _vlan_checks, _vlan_nautobot, _vlan_phpipam, _vlan_rules
    return _pipeline, _lab, _ping_from_wan, _vlan_phpipam, _vlan_nautobot, _vlan_rules, _vlan_checks


# ---- allocation -----------------------------------------------------------------------------------------------------------------
def allocation(z, name):
    net = list(POOL.subnets(new_prefix=28))[z - 1]
    return {"name": name, "z": z, "table": 2000 + z, "l3vni": 51000 + z, "l3vlan": 3900 + z, "probe": f"10.255.{100 + z}",
            "transit_vlan": 3800 + z, "transit_vni": 13800 + z, "subnet": str(net),
            "borders": [str(net.network_address + 2), str(net.network_address + 3)], "fws": [str(net.network_address + 11), str(net.network_address + 12)]}


def added(data):
    """The VRFs added by Fabric Factory (evpn.vrfs entries with a z)."""
    return [v for v in data["evpn"]["vrfs"] if v.get("z")]


def next_z(data):
    used = {v["z"] for v in added(data)}
    return next((z for z in range(1, MAX_ADDED + 1) if z not in used), None)


def label(a):
    return (f"VRF {a['name']}: L3VNI {a['l3vni']}, table {a['table']}, L3VNI VLAN {a['l3vlan']}, transit VLAN {a['transit_vlan']} "
            f"({a['subnet']}), probes {a['probe']}.x")


def clashes(a, m, data):
    """Why this allocation cannot be used (the live plan, Nautobot's model)."""
    why = []
    if a["name"] in {v["name"] for v in data["evpn"]["vrfs"]}: why.append(f"VRF {a['name']} exists")
    for v in (a["l3vlan"], a["transit_vlan"]):
        if v in m["vlans_used"]: why.append(f"VLAN {v} is in use")
    for v in (a["l3vni"], a["transit_vni"]):
        if v in m["vnis_used"]: why.append(f"VNI {v} is in use")
    if a["table"] in {v["table"] for v in data["evpn"]["vrfs"]}: why.append(f"table {a['table']} is in use")
    if a["probe"] in {v.get("probe") for v in data["evpn"]["vrfs"]}: why.append(f"probe base {a['probe']} is in use")
    net = ipaddress.ip_network(a["subnet"])
    if any(net.overlaps(ipaddress.ip_network(s)) for s in m.get("subnets_used", [])): why.append(f"{net} overlaps a subnet in use")
    return why


def in_use(name, m, data):
    """What keeps a VRF from being deleted."""
    why = [f"VLAN {v['vlan']}" for v in data["evpn"]["vlans"] if v["vrf"] == name]
    why += [f"server {h['name']}" for h in m.get("hosts", []) if h.get("vrf") == name]
    why += [f"LB peering {p['name']}" for p in data.get("lb_peerings", []) if p.get("vrf") == name]
    why += [f"firewall rule {p['from']} -> {p['to']} {p['dest']} {p['proto']} {p.get('port') or ''}".rstrip()
            for p in data.get("fw_policy", []) if not p.get("standard") and name in (p["from"], p["to"])]
    return why


# ---- phpIPAM / Nautobot ---------------------------------------------------------------------------------------------------------
def _ipam_ctx(ip):
    sec = next(s for s in ip.req("GET", "sections") if s["name"] == LOCATION)
    dom = next(d for d in ip.req("GET", "l2domains") if d["name"] == LOCATION)
    def subnet(cidr):
        n = ipaddress.ip_network(cidr)
        return next((x for x in ip.req("GET", f"subnets/cidr/{n.network_address}/{n.prefixlen}") or [] if x["sectionId"] == sec["id"]), None)
    return sec, dom, subnet


def _phpipam_add(a, rt_as, tag, log):
    ip = Ipam(); sec, dom, subnet = _ipam_ctx(ip); n = 0
    vrf = next((x for x in ip.req("GET", "vrf") or [] if x["name"] == a["name"]), None)
    if vrf is None:
        ip.req("POST", "vrf", json={"name": a["name"], "rd": f"{rt_as}:{a['l3vni']}", "description": f"evpn-3tier VRF {a['name']}: L3VNI {a['l3vni']}, kernel table {a['table']} ({tag})"}); n += 1
        vrf = next(x for x in ip.req("GET", "vrf") if x["name"] == a["name"])
    have = {int(x["number"]): x for x in ip.req("GET", f"l2domains/{dom['id']}/vlans") or []}
    for num, nm, desc in ((a["transit_vlan"], f"transit-{a['name']}", f"firewall transit {a['name']}, L2VNI {a['transit_vni']} (borders + firewalls)"),
                          (a["l3vlan"], f"{a['name']}-l3vni", f"L3VNI {a['l3vni']} of VRF {a['name']} (symmetric IRB)")):
        if num in have: _check(tag in (have[num].get("description") or "") or have[num]["name"] == nm, f"phpIPAM: VLAN {num} is taken ({have[num]['name']})"); continue
        ip.req("POST", "vlan", json={"domainId": dom["id"], "number": num, "name": nm, "description": f"{desc} ({tag})"}); n += 1
    have = {int(x["number"]): x for x in ip.req("GET", f"l2domains/{dom['id']}/vlans")}
    pool = subnet(str(POOL))
    if pool is None:
        master = subnet(str(labmodel.FABRIC_SUPERNET))
        ip.req("POST", "subnets", json={"subnet": str(POOL.network_address), "mask": POOL.prefixlen, "sectionId": sec["id"], "masterSubnetId": master["id"],
                                        "description": "pool: transit /28s of added VRFs (Add a VRF)"}); n += 1
        pool = subnet(str(POOL))
    sub = subnet(a["subnet"])
    if sub is None:
        net = ipaddress.ip_network(a["subnet"])
        ip.req("POST", "subnets", json={"subnet": str(net.network_address), "mask": 28, "sectionId": sec["id"], "masterSubnetId": pool["id"], "vrfId": vrf["vrfId"],
                                        "vlanId": have[a["transit_vlan"]]["vlanId"],
                                        "description": f"firewall transit {a['name']} (VLAN {a['transit_vlan']}, L2VNI {a['transit_vni']}): eBGP borders to firewalls"}); n += 1
        sub = subnet(a["subnet"])
    addrs = {x["ip"] for x in ip.req("GET", f"subnets/{sub['id']}/addresses") or []}
    for host, addr, desc in (("border1", a["borders"][0], "br0"), ("border2", a["borders"][1], "br0"), ("fw1", a["fws"][0], "bond0 (preferred)"), ("fw2", a["fws"][1], "bond0 (standby)")):
        if addr not in addrs:
            ip.req("POST", "addresses", json={"subnetId": sub["id"], "ip": addr, "hostname": host, "description": f"{host} {desc}.{a['transit_vlan']} (VRF {a['name']})"[:64]}); n += 1
    probe = f"{a['probe']}.0/24"
    if subnet(probe) is None:
        master = subnet("10.255.0.0/16")
        ip.req("POST", "subnets", json={"subnet": f"{a['probe']}.0", "mask": 24, "sectionId": sec["id"], "masterSubnetId": master["id"], "vrfId": vrf["vrfId"],
                                        "description": f"probes in VRF {a['name']}"}); n += 1
    log(f"phpIPAM: VRF {a['name']} (RD {rt_as}:{a['l3vni']}), VLANs {a['transit_vlan']} / {a['l3vlan']}, transit {a['subnet']} with the borders' and "
        f"firewalls' addresses, probe subnet {probe} — {n} object(s) created")


def _phpipam_release(a, log):
    ip = Ipam(); sec, dom, subnet = _ipam_ctx(ip); n = 0
    for cidr in (a["subnet"], f"{a['probe']}.0/24"):
        s = subnet(cidr)
        if s:
            for x in ip.req("GET", f"subnets/{s['id']}/addresses") or []: ip.req("DELETE", f"addresses/{x['id']}"); n += 1
            ip.req("DELETE", f"subnets/{s['id']}"); n += 1
    pool = subnet(str(POOL))
    if pool and not (ip.req("GET", f"subnets/{pool['id']}/slaves") or []): ip.req("DELETE", f"subnets/{pool['id']}"); n += 1
    for x in ip.req("GET", f"l2domains/{dom['id']}/vlans") or []:
        if int(x["number"]) in (a["transit_vlan"], a["l3vlan"]): ip.req("DELETE", f"vlan/{x['vlanId']}"); n += 1
    vrf = next((x for x in ip.req("GET", "vrf") or [] if x["name"] == a["name"]), None)
    if vrf: ip.req("DELETE", f"vrf/{vrf['vrfId']}"); n += 1
    log(f"phpIPAM: VRF {a['name']}, its VLANs, transit and probe subnets released — {n} object(s) removed")


def _nautobot_add(a, tag, log):
    nb = Nautobot(); ctx = nb.context(); data = ctx["data"]; ev = data["evpn"]
    active = nb.one("extras/statuses", name="Active"); site = nb.one("dcim/locations", name=LOCATION)
    ns = nb.one("ipam/namespaces", name=LOCATION); vg = nb.one("ipam/vlan-groups", name=LOCATION); tg = nb.one("tenancy/tenant-groups", name=LOCATION)
    tenant = nb.one("tenancy/tenants", name=f"e3t-{a['name']}") or nb.post("tenancy/tenants", {
        "name": f"e3t-{a['name']}", "tenant_group": tg["id"], "description": f"evpn-3tier VRF {a['name']} (L3VNI {a['l3vni']}) — {tag}"})
    rt_name = f"{ev['rt_as']}:{a['l3vni']}"
    rt = nb.one("ipam/route-targets", name=rt_name) or nb.post("ipam/route-targets", {"name": rt_name, "tenant": tenant["id"]})
    vrf = nb.one("ipam/vrfs", name=a["name"], namespace=ns["id"]) or nb.post("ipam/vrfs", {
        "name": a["name"], "namespace": ns["id"], "status": active["id"], "tenant": tenant["id"], "import_targets": [rt["id"]], "export_targets": [rt["id"]],
        "description": f"VRF {a['name']}: L3VNI {a['l3vni']}, table {a['table']}, L3VNI VLAN {a['l3vlan']} — {tag}"})
    vl = {}
    for num, nm, desc in ((a["transit_vlan"], f"transit-{a['name']}", f"firewall transit {a['name']}, L2VNI {a['transit_vni']}, {a['subnet']} (borders + firewalls)"),
                          (a["l3vlan"], f"{a['name']}-l3vni", f"L3VNI {a['l3vni']} of VRF {a['name']}")):
        vl[num] = nb.one("ipam/vlans", vid=num, vlan_group=vg["id"]) or nb.post("ipam/vlans", {
            "vid": num, "name": nm, "vlan_group": vg["id"], "status": active["id"], "tenant": tenant["id"], "description": desc})
    pf = nb.one("ipam/prefixes", prefix=a["subnet"], namespace=ns["id"])
    if pf is None:
        pf = nb.post("ipam/prefixes", {"prefix": a["subnet"], "namespace": ns["id"], "status": active["id"], "type": "network", "tenant": tenant["id"],
                                       "vlan": vl[a["transit_vlan"]]["id"], "description": f"transit {a['name']}: borders .2/.3, fw1 .11, fw2 .12"})
        nb.patch(f"ipam/prefixes/{pf['id']}", {"location": site["id"]})
        nb.post("ipam/vrf-prefix-assignments", {"vrf": vrf["id"], "prefix": pf["id"]})
    ctx = nb.context(); data = ctx["data"]; ev = data["evpn"]
    if not any(v["name"] == a["name"] for v in ev["vrfs"]):
        ev["vrfs"].append({"name": a["name"], "l3vni": a["l3vni"], "probe": a["probe"], "table": a["table"], "l3vlan": a["l3vlan"], "z": a["z"]})
    if not any(t["vrf"] == a["name"] for t in ev["transit"]):
        ev["transit"].append({"vni": a["transit_vni"], "vrf": a["name"], "vlan": a["transit_vlan"], "subnet": a["subnet"]})
    nb.patch(f"extras/config-contexts/{ctx['id']}", {"data": data})
    labmodel.invalidate()
    log(f"Nautobot: tenant e3t-{a['name']}, route target {rt_name}, VRF {a['name']}, VLANs {a['transit_vlan']} (transit-{a['name']}) and "
        f"{a['l3vlan']} ({a['name']}-l3vni), prefix {a['subnet']}; the VRF and its transit in the evpn-3tier plan")


def _zone_rules(name, add, log):
    nb = Nautobot(); ctx = nb.context(); data = ctx["data"]; pol = data.get("fw_policy", [])
    if add: pol, n = fwrules.add_zone(pol, name, data["evpn"]["vlans"])
    else: pol, n = fwrules.drop_zone(pol, name)
    if n:
        data["fw_policy"] = pol; nb.patch(f"extras/config-contexts/{ctx['id']}", {"data": data}); labmodel.invalidate()
    log(f"Standard Firewall Rules: {n} {'added — ICMP from zone ' + name + ' to every other VLAN' if add else 'removed — every one from or to zone ' + name}")


def _nautobot_remove(a, log):
    nb = Nautobot(); ctx = nb.context(); data = ctx["data"]; ev = data["evpn"]
    ev["vrfs"] = [v for v in ev["vrfs"] if v["name"] != a["name"]]
    ev["transit"] = [t for t in ev["transit"] if t["vrf"] != a["name"]]
    nb.patch(f"extras/config-contexts/{ctx['id']}", {"data": data})
    ns = nb.one("ipam/namespaces", name=LOCATION); vg = nb.one("ipam/vlan-groups", name=LOCATION); gone = []
    pf = nb.one("ipam/prefixes", prefix=a["subnet"], namespace=ns["id"])
    if pf: nb.delete(f"ipam/prefixes/{pf['id']}"); gone.append(a["subnet"])
    for num in (a["transit_vlan"], a["l3vlan"]):
        v = nb.one("ipam/vlans", vid=num, vlan_group=vg["id"])
        if v: nb.delete(f"ipam/vlans/{v['id']}"); gone.append(f"VLAN {num}")
    vrf = nb.one("ipam/vrfs", name=a["name"], namespace=ns["id"])
    if vrf: nb.delete(f"ipam/vrfs/{vrf['id']}"); gone.append(f"VRF {a['name']}")
    rt = nb.one("ipam/route-targets", name=f"{ev['rt_as']}:{a['l3vni']}")
    if rt: nb.delete(f"ipam/route-targets/{rt['id']}"); gone.append(rt["name"])
    t = nb.one("tenancy/tenants", name=f"e3t-{a['name']}")
    if t: nb.delete(f"tenancy/tenants/{t['id']}"); gone.append(t["name"])
    labmodel.invalidate()
    log(f"Nautobot: VRF {a['name']} out of the plan; removed {', '.join(gone) or 'nothing more (already gone)'}")


# ---- devices ------------------------------------------------------------------------------------------------------------------
def _has_vrf(ssh, inv, node, name):
    _, o = ssh(node, f"{OP} show configuration commands | grep -E '^set vrf name {name} '", inv=inv, timeout=60)
    return bool(o.strip())


def _zone_on_fw(ssh, inv, fw, name):
    _, o = ssh(fw, f"{OP} show configuration commands | grep -E 'firewall zone {name.upper()} '", inv=inv, timeout=60)
    return bool(o.strip())


# ---- executors ----------------------------------------------------------------------------------------------------------------
def add_vrf(r, log):
    _pipeline, _lab, _ping_from_wan, _vlan_phpipam, _vlan_nautobot, _vlan_rules, _vlan_checks = _helpers()
    p = r.parameters; a = allocation(p["z"], p["vrf"]); tag = f"request #{r.pk}"; first = bool(p.get("vlan_id"))
    # ---- 1 pre-flight ------------------------------------------------------------------------------------------------------
    log.stage("preflight")
    nb = Nautobot(); data = nb.context()["data"]; labmodel.invalidate(); m = labmodel.model()
    mine = next((v for v in data["evpn"]["vrfs"] if v["name"] == a["name"]), None)
    retry = mine is not None and mine.get("z") == a["z"]
    if not retry:
        why = clashes(a, m, data); _check(not why, "; ".join(why) + " — nothing changed")
        _check(len(added(data)) < MAX_ADDED, f"{MAX_ADDED} VRFs have been added already — nothing changed")
    log(f"pre-flight: {label(a)}" + (f"; first VLAN {p['vlan_id']} ({p['gateway']})" if first else "; no VLAN yet") + (" (a retry)" if retry else ""))
    # ---- 2 phpIPAM ---------------------------------------------------------------------------------------------------------
    log.stage("phpipam")
    _phpipam_add(a, data["evpn"]["rt_as"], tag, log)
    gw = _vlan_phpipam(r, log) if first else None
    # ---- 3 Nautobot --------------------------------------------------------------------------------------------------------
    log.stage("nautobot")
    _nautobot_add(a, tag, log)
    if first: _vlan_nautobot(r, gw, log)
    # ---- 4 Standard Firewall Rules -------------------------------------------------------------------------------------------
    log.stage("rules")
    _zone_rules(a["name"], True, log)
    if first: _vlan_rules(r, gw, log)
    # ---- 5 pipelines: switches first (the firewalls do not know the zone yet: no verify), then firewalls (whole lab verified)
    log.stage("switches")
    sw = _pipeline("evpn-3tier-switches", log, verify=False)
    log.stage("firewalls")
    fw = _pipeline("evpn-3tier-firewalls", log)
    # ---- 6 checks ----------------------------------------------------------------------------------------------------------
    log.stage("checks")
    inv, ssh = _lab()
    borders = [n["name"] for n in inv["nodes"] if n["role"] == "border"]
    leaves = [n["name"] for n in inv["nodes"] if n["role"] == "leaf"] if first else []
    missing = [s for s in borders + leaves if not _has_vrf(ssh, inv, s, a["name"])]
    _check(not missing, f"VRF {a['name']} is missing on {', '.join(missing)}")
    for b in borders:
        _, o = ssh(b, f"vtysh -c 'show bgp vrf {a['name']} summary'", inv=inv, timeout=60)
        up = [f for f in a["fws"] if any(l.split()[0] == f and l.split()[-3].isdigit() for l in o.splitlines() if l.split())]
        _check(len(up) == 2, f"{b} VRF {a['name']}: firewall sessions up {up}, want {a['fws']}")
    no_zone = [f for f in ("fw1", "fw2") if not _zone_on_fw(ssh, inv, f, a["name"])]
    _check(not no_zone, f"zone {a['name'].upper()} missing on {', '.join(no_zone)}")
    probe = leaves[0] if leaves else borders[0]
    _, o = ssh(probe, f"vtysh -c 'show ip route vrf {a['name']} 0.0.0.0/0'", inv=inv, timeout=60)
    _check("0.0.0.0/0" in o, f"{probe} has no default route in VRF {a['name']} (from the firewalls)")
    log(f"on the devices: VRF {a['name']} on {', '.join(borders + leaves)}; both borders' sessions to fw1 / fw2 in it Established; "
        f"zone {a['name'].upper()} on both firewalls; {probe} has the default route from the firewalls")
    extra = []
    if first:
        _vlan_checks(r, gw, log)
        extra = [(f"VLAN {p['vlan_id']} gateway ({a['name']})", str(ipaddress.ip_interface(gw).ip))]
    # ---- 7 ping from the WAN -------------------------------------------------------------------------------------------------
    log.stage("ping")
    pings = _ping_from_wan(inv, ssh, extra, log)
    return {"vrf": a["name"], "z": a["z"], "switches_run": sw, "firewalls_run": fw, "ping": pings}


def delete_vrf(r, log):
    _pipeline, _lab, _ping_from_wan, *_ = _helpers()
    p = r.parameters; a = allocation(p["z"], p["vrf"])
    # ---- 1 pre-flight ------------------------------------------------------------------------------------------------------
    log.stage("preflight")
    nb = Nautobot(); data = nb.context()["data"]; labmodel.invalidate(); m = labmodel.model()
    _check(a["name"] not in BASE, f"VRF {a['name']} is part of the lab's design — nothing changed")
    mine = next((v for v in data["evpn"]["vrfs"] if v["name"] == a["name"]), None)
    _check(mine is None or mine.get("z") == a["z"], f"VRF {a['name']} was not added by Fabric Factory — nothing changed")
    why = in_use(a["name"], m, data); _check(not why, f"VRF {a['name']} is in use: {', '.join(why)} — nothing changed")
    log(f"pre-flight: VRF {a['name']} holds nothing (no VLAN, server, peering or custom rule)" + (" — already out of the plan (a retry)" if mine is None else ""))
    # ---- 2 Nautobot --------------------------------------------------------------------------------------------------------
    log.stage("nautobot")
    _zone_rules(a["name"], False, log)
    _nautobot_remove(a, log)
    # ---- 3 pipelines: firewalls first (the zone goes; the borders still have the VRF: no verify), then switches (verified) ----
    log.stage("firewalls")
    fw = _pipeline("evpn-3tier-firewalls", log, verify=False)
    log.stage("switches")
    sw = _pipeline("evpn-3tier-switches", log)
    # ---- 4 checks ----------------------------------------------------------------------------------------------------------
    log.stage("checks")
    inv, ssh = _lab()
    left = [n["name"] for n in inv["nodes"] if n["role"] in ("leaf", "border") and _has_vrf(ssh, inv, n["name"], a["name"])]
    left += [f for f in ("fw1", "fw2") if _zone_on_fw(ssh, inv, f, a["name"])]
    _check(not left, f"VRF / zone {a['name']} still on {', '.join(left)}")
    log(f"on the devices: VRF {a['name']} gone from every leaf and border, zone {a['name'].upper()} from both firewalls")
    # ---- 5 phpIPAM: released last ---------------------------------------------------------------------------------------------
    log.stage("phpipam")
    _phpipam_release(a, log)
    log.stage("ping")
    pings = _ping_from_wan(inv, ssh, [], log)
    return {"vrf": a["name"], "switches_run": sw, "firewalls_run": fw, "ping": pings}
