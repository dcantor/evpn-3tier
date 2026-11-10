"""Set up a load balancer to peer with the fabric: add a new load balancer PAIR (two VMs dual-homed to the border leaves,
in the tier VRF the requester picks, highly available active / standby with keepalived VRRP, each peering with both
borders over eBGP), or delete the last pair added. The requester picks only the action and the tier; everything else is
allocated here.

The hardware inventory stays lab.conf's: added load balancers are names in added-lbs.conf (ADDED_LBS=(lb3 lb4 ...),
written here, not in git), and lab.conf derives the rest from the number k (3-6): index 22+k, mgmt 10.18.0.(50+k),
rack edge, Ethernet Segment 12+k, bond legs border1 / border2 eth(5+k) — the borders' spare ports eth8-eth11, so two
pairs (lb3 + lb4, lb5 + lb6). Nautobot holds the configuration: the devices, their tier VLAN and addresses, the pair's
VRRP address, and each member's peering (config context lb_peerings, "added", with "ha": partner, VRID, VRRP address,
priority — the loadbalancers pipeline renders keepalived from it; no HAProxy: no VIPs yet, the fabric accepts nothing).

add     pre-flight (host memory, the borders' spare ports up and uncabled, names / addresses / ASNs / VLANs free) ->
        phpIPAM (per member: mgmt, tier address, peering VLAN, its /29 and three addresses; the VRRP address) ->
        Nautobot (devices, interfaces, addresses, cables, the tier VLAN onto the borders if needed, the peerings, the VRRP
        address) -> rack and cable (added-lbs.conf, both VMs) -> switches pipeline -> loadbalancers pipeline (bond, tier
        VLAN, keepalived, BIRD; the whole lab verified) -> checks (per member: both sessions and BFD, the segment, BIRD,
        the gateway; the pair: exactly one member holds the VRRP address — the first — a server reaches it, and a live
        failover: keepalived stopped on the active one, the standby takes over, the server still reaches it, then back)
        -> ping
delete  pre-flight (the last pair added) -> Nautobot (all of the above removed, the borders' ports spare again, the tier
        VLAN back to the leaves if it was extended for the pair) -> switches pipeline -> both VMs powered off and deleted,
        added-lbs.conf -> phpIPAM released -> ping
Every stage checks before it writes and is idempotent: a failed request is retried from the top."""
import ipaddress
import json
import os
import re
import shutil
import subprocess
import time

from django.conf import settings

from . import labmodel
from .clients import LOCATION, Ipam, Nautobot

FIRST, LAST = 3, 6                 # load balancer numbers added pairs may use (the borders' spare ports eth8-eth11)
RAM_MIB = 256                      # per VM (lab.conf LINUX_RAM_MIB[lb])
SWITCHES = ["border1", "border2"]
DESIGN_PAIR = ["lb1", "lb2"]       # the web VIP pair (lab.conf): cannot be deleted
PEERING_POOL = ipaddress.ip_network("172.16.150.0/24")   # where a peering /29 goes when 172.16.<VLAN>.0/29 is taken
ADD_STAGES = (("preflight", "Pre-flight checks"), ("phpipam", "phpIPAM reservation"), ("nautobot", "Nautobot"), ("rack", "Rack and cable"),
              ("switches", "Switches pipeline"), ("loadbalancers", "Load balancers pipeline"), ("checks", "BGP, VRRP and failover checks"),
              ("ping", "Ping test from the WAN"))
DELETE_STAGES = (("preflight", "Pre-flight checks"), ("nautobot", "Nautobot"), ("switches", "Switches pipeline"), ("rack", "Un-rack"),
                 ("phpipam", "phpIPAM release"), ("ping", "Ping test from the WAN"))


def _check(cond, msg):
    if not cond: raise RuntimeError(msg)


def _helpers():
    """executors / changes import this module (EXECUTORS): their helpers are imported when a request runs."""
    from .changes import _pipeline
    from .executors import _lab, _ping_from_wan
    return _pipeline, _lab, _ping_from_wan


# ---- the hardware inventory ---------------------------------------------------------------------------------------------------
def _conf():
    return settings.LAB_DIR / "added-lbs.conf"


def added():
    """The load balancers added so far, in order (added-lbs.conf)."""
    f = _conf()
    m = re.search(r"ADDED_LBS=\(([^)]*)\)", f.read_text()) if f.exists() else None
    return m.group(1).split() if m else []


def _write_added(names):
    f = _conf()
    if not names: f.unlink(missing_ok=True); return
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text("# load balancers added by Fabric Factory (Set up a load balancer to peer with the fabric): the hardware inventory lab.conf reads\n"
                   f"ADDED_LBS=({' '.join(names)})\n")
    os.replace(tmp, f)


def _lab_sh(*args, timeout=900):
    r = subprocess.run([str(settings.LAB_DIR / "lab.sh"), *args], capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def hardware(k):
    """Load balancer number k: what lab.conf derives for it."""
    idx = 22 + k
    return {"name": f"lb{k}", "k": k, "idx": idx, "mgmt_ip": f"10.18.0.{50 + k}", "console": 5620 + idx, "border_port": f"eth{5 + k}",
            "es": 12 + k, "rack": "edge", "macs": [f"52:54:00:d0:{idx:02x}:{p:02x}" for p in range(3)]}


def next_pair():
    """The next pair to add: (k, k+1) after the highest added number, or None when the borders have no spare ports left."""
    have = [int(n[2:]) for n in added()]
    k = max(have) + 1 if have else FIRST
    return (k, k + 1) if k + 1 <= LAST else None


def last_pair():
    """The pair Delete may take: the last one added."""
    a = added()
    return a[-2:] if len(a) >= 2 else None


def pair_name(names):
    return " + ".join(names)


def mem_available_mib():
    for l in open("/proc/meminfo"):
        if l.startswith("MemAvailable:"): return int(l.split()[1]) // 1024
    return 0


# ---- the plan: everything but the tier is allocated ----------------------------------------------------------------------------
def _held(exclude_pk=None):
    """What open requests hold: VLANs, VNIs, subnets (allocate.claims), and the ASNs / addresses of open load balancer requests."""
    from .allocate import OPEN, R, claims
    v, vni, nets = claims(exclude_pk)
    asns, addrs = set(), set()
    for r in R.objects.filter(status__in=OPEN, workflow="lb-peering").exclude(pk=exclude_pk):
        for x in r.parameters.get("members") or []:
            asns.add(int(x["asn"])); addrs.add(x["address"].split("/")[0])
        if r.parameters.get("vrrp_address"): addrs.add(r.parameters["vrrp_address"].split("/")[0])
    return v, vni, nets, asns, addrs


def ipam_used(subnet):
    """The addresses phpIPAM has in this subnet (empty when phpIPAM has no such subnet or does not answer)."""
    try:
        ip = Ipam(); net = ipaddress.ip_network(subnet)
        sec = next(s for s in ip.req("GET", "sections") if s["name"] == LOCATION)
        sub = next((x for x in ip.req("GET", f"subnets/cidr/{net.network_address}/{net.prefixlen}") or [] if x["sectionId"] == sec["id"]), None)
        return {a["ip"] for a in ip.req("GET", f"subnets/{sub['id']}/addresses") or []} if sub else set()
    except Exception:                                                     # noqa: BLE001
        return set()


def tier_vlan(m, vrf):
    """The VLAN an added load balancer of this tier sits in: the VRF's lowest VLAN already on the borders, else its lowest
    VLAN (then extended to the borders). None when the VRF has no VLAN."""
    vl = sorted((v for v in m.get("vlans", []) if v["vrf"] == vrf), key=lambda v: ("borders" not in v["on"], v["vlan"]))
    return vl[0] if vl else None


def plan(ks, vrf, m=None, exclude_pk=None, used_addrs=None):
    """The new pair lb<k> + lb<k+1> in tier VRF vrf: per member its ASN, peering VLAN / VNI / subnet and tier address; the
    pair's VRRP address and VRID. The first member is the active one (priority 150), the second the standby (100)."""
    m = m or labmodel.model()
    hv, hvni, hnets, hasn, haddr = _held(exclude_pk)
    used_asn = set(m["asns_used"]) | hasn
    used_v, used_vni = set(m["vlans_used"]) | hv, set(m["vnis_used"]) | hvni
    taken = list(m["subnets_used"]) + [n for n, _ in hnets]
    names = [f"lb{k}" for k in ks]
    members = []
    for i, k in enumerate(ks):
        hw = hardware(k)
        asn = next(a for a in [65300 + 10 * k] + list(range(65330, 65400, 10)) + list(range(65301, 65400)) if a not in used_asn); used_asn.add(asn)
        vlan = next(v for v in [140 + 10 * k] + list(range(170, 400, 10)) + list(range(141, 400)) if v not in used_v and 10000 + v not in used_vni)
        used_v.add(vlan); used_vni.add(10000 + vlan)
        cands = ([ipaddress.ip_network(f"172.16.{vlan}.0/29")] if vlan <= 255 else []) + list(PEERING_POOL.subnets(new_prefix=29))
        subnet = next(c for c in cands if not any(c.overlaps(t) for t in taken)); taken.append(subnet)
        members.append({"name": hw["name"], "k": k, "asn": asn, "peering_vlan": vlan, "vni": 10000 + vlan, "peering_subnet": str(subnet),
                        "border_port": hw["border_port"], "mgmt_ip": hw["mgmt_ip"], "es": hw["es"], "priority": 150 if i == 0 else 100,
                        "partner": names[1 - i]})
    out = {"op": "add", "names": names, "ks": list(ks), "pair": pair_name(names), "vrf": vrf, "switches": SWITCHES, "bfd": True, "vips": [],
           "vrid": 100 + ks[0], "members": members}
    tv = tier_vlan(m, vrf)
    if tv is None: return {**out, "tier_vlan": None, "vrrp_address": None}
    net = ipaddress.ip_network(tv["subnet"]); gw = ipaddress.ip_interface(tv["gateway"]).ip
    bad = {gw, net.network_address, net.broadcast_address}
    bad |= {net.network_address + b + i for b, n in ((240, 8), (250, 2)) for i in range(1, n + 1) if b + i < net.num_addresses}   # SVIs
    if m.get("web_vip"): bad.add(ipaddress.ip_address(m["web_vip"]))
    bad |= {ipaddress.ip_interface(h["address"]).ip for h in m.get("hosts", []) if h.get("address")}
    bad |= {ipaddress.ip_address(a) for a in (used_addrs if used_addrs is not None else ipam_used(net)) | haddr}
    size = net.num_addresses
    free = [h for h in list(range(20, size - 1)) + list(range(2, min(20, size - 1))) if net.network_address + h not in bad][:3]
    out.update(tier_vlan=tv["vlan"], tier_vni=tv["vni"], tier_subnet=str(net), tier_gateway=str(gw), span_added="borders" not in tv["on"])
    if len(free) < 3: return {**out, "vrrp_address": None}
    out["vrrp_address"] = f"{net.network_address + free[0]}/{net.prefixlen}"          # the pair's shared address, then one per member
    for x, h in zip(members, free[1:]): x.update(hostnum=h, address=f"{net.network_address + h}/{net.prefixlen}")
    return out


def plan_text(p):
    """The plan as the form and the request show it."""
    if not p.get("vrrp_address"): return f"No three free addresses in VRF {p['vrf']}'s VLAN {p.get('tier_vlan')}."
    a, b = p["members"]
    s = (f"{p['pair']}: high availability, active / standby (keepalived VRRP, VRID {p['vrid']}) — {a['name']} active, {b['name']} standby\n"
         f"Shared VRRP address: {p['vrrp_address']} in VLAN {p['tier_vlan']} (VRF {p['vrf']}), gateway {p['tier_gateway']}"
         + (f" — VLAN {p['tier_vlan']} is extended to the border leaves\n" if p["span_added"] else "\n"))
    for x in p["members"]:
        net = ipaddress.ip_network(x["peering_subnet"])
        s += (f"{x['name']}: {x['address']}, AS {x['asn']}, peering VLAN {x['peering_vlan']} (L2VNI {x['vni']}) {net} — borders .1 / .2, {x['name']} .{net.num_addresses - 2}; "
              f"mgmt {x['mgmt_ip']}, border1 / border2 {x['border_port']} (ES {x['es']})\n")
    return s + "BGP + BFD from both borders to each member; VIPs: none yet (the fabric accepts no route from them)"


def delete_text(names, entries):
    s = (f"{pair_name(names)} go: their peerings (AS {', '.join(str(e['asn']) for e in entries)}; VLANs {', '.join(str(e['vlan']) for e in entries)}), "
         f"the VRRP address {(entries[0].get('ha') or {}).get('address', '?')}, both VMs and disks, their cables (border1 / border2 "
         f"{', '.join(e.get('border_port', '?') for e in entries)} spare again), their addresses in Nautobot and phpIPAM")
    return s + (f"; VLAN {entries[0]['tier_vlan']} back to the leaves only" if entries[0].get("span_added") else "")


def pairs(m=None):
    """Every load balancer pair: the design's (lb1 + lb2), then the added ones in order."""
    m = m or labmodel.model(); a = added()
    design = [n for n in m.get("lbs", {}) if n not in a]
    return [design] + [a[i:i + 2] for i in range(0, len(a), 2)]


def choices(m=None):
    """The Load balancer dropdown: add the next pair, or delete a pair (only the last added can go)."""
    m = m or labmodel.model(); nxt = next_pair(); out = []
    out.append(("add", f"Add a new load balancer pair — lb{nxt[0]} + lb{nxt[1]} (active / standby) on border1 / border2 eth{5 + nxt[0]}, eth{5 + nxt[1]}"
                if nxt else "Add a new load balancer pair — none left: the borders' spare ports are all used"))
    last = last_pair()
    for pr in pairs(m):
        if not pr: continue
        key = f"delete:{'+'.join(pr)}"
        if pr == DESIGN_PAIR or not set(pr) <= set(added()): out.append((key, f"Delete {pair_name(pr)} — part of the lab's design: cannot be deleted"))
        elif pr != last: out.append((key, f"Delete {pair_name(pr)} — delete {pair_name(last)} first (the last pair added goes first)"))
        else:
            vrf = (m["lbs"].get(pr[0]) or {}).get("vrf")
            out.append((key, f"Delete {pair_name(pr)} — VRF {vrf}"))
    return out


# ---- phpIPAM ------------------------------------------------------------------------------------------------------------------
def _ipam_ctx(ip):
    sec = next(s for s in ip.req("GET", "sections") if s["name"] == LOCATION)
    def subnet(cidr):
        net = ipaddress.ip_network(cidr)
        return next((x for x in ip.req("GET", f"subnets/cidr/{net.network_address}/{net.prefixlen}") or [] if x["sectionId"] == sec["id"]), None)
    return sec, subnet


def _ipam_address(ip, sub, addr, host, desc, mac=None):
    have = next((x for x in ip.req("GET", f"subnets/{sub['id']}/addresses") or [] if x["ip"] == addr), None)
    if have: _check(have.get("hostname") == host, f"phpIPAM: {addr} is reserved for {have.get('hostname')} — stopped"); return 0
    ip.req("POST", "addresses", json={"subnetId": sub["id"], "ip": addr, "hostname": host, "description": desc[:64], **({"mac": mac} if mac else {})})
    return 1


# ---- add ----------------------------------------------------------------------------------------------------------------------
def _desc_of(nb, dev):
    return str(nb.get(f"dcim/devices/{dev['id']}").get("comments") or "")


def _vrrp_holder(ssh, inv, names, addr):
    """Which members hold the VRRP address now."""
    ip_ = addr.split("/")[0]
    return [n for n in names if f" {ip_}/" in ssh(n, "ip -br addr", inv=inv, timeout=30)[1]]


def _wait(fn, timeout, step=1.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v: return v, time.time() - t0
        time.sleep(step)
    return fn(), time.time() - t0


def add_pair(r, log):
    _pipeline, _lab, _ping_from_wan = _helpers()
    p = r.parameters; names, vrf, members = p["names"], p["vrf"], p["members"]; tag = f"request #{r.pk}"
    hws = {x["name"]: hardware(x["k"]) for x in members}
    nb, ip = Nautobot(), Ipam()
    # ---- 1 pre-flight -------------------------------------------------------------------------------------------------------
    log.stage("preflight")
    racked = added()
    olds = [nb.one("dcim/devices", name=f"e3t-{n}") for n in names]
    # a retry: this request got as far as Nautobot (a device it created) or the rack — every stage is idempotent
    mine_before = any(o is not None and (o.get("local_config_context_data") or {}).get("idx") == hws[n]["idx"] and tag in _desc_of(nb, o) for n, o in zip(names, olds))
    retry = any(n in racked for n in names) or mine_before
    _check(retry or (next_pair() and list(next_pair()) == list(p["ks"])), f"{p['pair']} is no longer the next pair to add — nothing changed")
    free = mem_available_mib()
    _check(all(n in racked for n in names) or free >= 2 * RAM_MIB + 1024,
           f"the host has {free} MiB available: two load balancers need {2 * RAM_MIB} MiB plus 1 GiB headroom — nothing changed")
    inv, ssh = _lab()
    for s in SWITCHES:
        _, o = ssh(s, "ip -br link", inv=inv, timeout=60)
        sd = nb.one("dcim/devices", name=f"e3t-{s}")
        for n in names:
            bp = hws[n]["border_port"]
            _check(f"{bp} " in o or f"{bp}@" in o, f"{s} has no {bp} (its spare ports are added once, with a restart) — nothing changed")
            itf = nb.one("dcim/interfaces", device_id=sd["id"], name=bp)
            _check(itf is not None, f"Nautobot has no {s} {bp} — nothing changed")
            _check(retry or not itf.get("cable"), f"{s} {bp} is already cabled in Nautobot — nothing changed")
    data = nb.context()["data"]; ev = data["evpn"]; ns = nb.one("ipam/namespaces", name=LOCATION)
    if not retry:
        labmodel.invalidate(); m = labmodel.model()
        for x in members:
            n = x["name"]; net = ipaddress.ip_network(x["peering_subnet"])
            _check(nb.one("dcim/devices", name=f"e3t-{n}") is None, f"Nautobot already has e3t-{n} — nothing changed")
            _check(not any(e["name"] == n for e in data.get("lb_peerings", [])), f"Nautobot's plan already has a peering for {n} — nothing changed")
            _check(x["asn"] not in m["asns_used"], f"AS {x['asn']} is in use — nothing changed")
            _check(x["peering_vlan"] not in m["vlans_used"] and x["vni"] not in m["vnis_used"], f"VLAN {x['peering_vlan']} / VNI {x['vni']} is in use — nothing changed")
            _check(not labmodel.overlaps(net), f"{net} overlaps {', '.join(labmodel.overlaps(net))} — nothing changed")
            _check(subprocess.run(["ping", "-c", "1", "-W", "1", x["mgmt_ip"]], capture_output=True).returncode != 0, f"{x['mgmt_ip']} answers ping: in use — nothing changed")
        for a in [f"{x['mgmt_ip']}/24" for x in members] + [x["address"] for x in members] + [p["vrrp_address"]]:
            hit = nb.get("ipam/ip-addresses", address=a, namespace=ns["id"])["results"]
            _check(not hit, f"{a} is already in Nautobot ({hit[0].get('description') if hit else ''}) — nothing changed")
    tv = next((v for v in ev["vlans"] if v["vlan"] == p["tier_vlan"]), None)
    _check(tv is not None and tv["vrf"] == vrf, f"VLAN {p['tier_vlan']} is not a VLAN of VRF {vrf} in Nautobot's plan — nothing changed")
    log(f"pre-flight: {p['pair']} in VRF {vrf} (VLAN {p['tier_vlan']}), VRRP {p['vrrp_address']}; "
        + "; ".join(f"{x['name']} AS {x['asn']}, {x['address']}, peering VLAN {x['peering_vlan']} {x['peering_subnet']}, border port {x['border_port']}" for x in members)
        + f"; the borders' ports spare; host memory {free} MiB available" + (" (a retry)" if retry else ""))
    # ---- 2 phpIPAM ----------------------------------------------------------------------------------------------------------
    log.stage("phpipam")
    sec, subnet = _ipam_ctx(ip); n_new = 0
    oob = subnet("10.18.0.0/24"); _check(oob, "phpIPAM has no subnet 10.18.0.0/24")
    tsub = subnet(p["tier_subnet"]); _check(tsub, f"phpIPAM has no subnet {p['tier_subnet']}")
    vrrp_host = "-".join(names) + "-vrrp"
    n_new += _ipam_address(ip, tsub, p["vrrp_address"].split("/")[0], vrrp_host, f"VRRP {p['pair']} (keepalived, VRID {p['vrid']}), {tag}")
    dom = next(d for d in ip.req("GET", "l2domains") if d["name"] == LOCATION)
    vrf_id = next(x["vrfId"] for x in ip.req("GET", "vrf") if x["name"] == vrf)
    sup = labmodel.FABRIC_SUPERNET
    master = next(x for x in ip.req("GET", f"subnets/cidr/{sup.network_address}/{sup.prefixlen}") if x["sectionId"] == sec["id"])
    for x in members:
        lb, hw, vlan, net = x["name"], hws[x["name"]], x["peering_vlan"], ipaddress.ip_network(x["peering_subnet"])
        n_new += _ipam_address(ip, oob, x["mgmt_ip"], lb, f"load balancer OOB eth0, VM e3t-{lb}, console :{hw['console']}, {tag}", hw["macs"][0])
        n_new += _ipam_address(ip, tsub, x["address"].split("/")[0], lb, f"{lb} bond0.{p['tier_vlan']} ({vrf} tier), {tag}")
        have = {int(v["number"]): v for v in ip.req("GET", f"l2domains/{dom['id']}/vlans") or []}
        if vlan not in have:
            ip.req("POST", "vlan", json={"domainId": dom["id"], "number": vlan, "name": f"peering-{lb}", "description": f"BGP peering {lb} (AS {x['asn']}), L2VNI {x['vni']} ({tag})"}); n_new += 1
        vlan_id = {int(v["number"]): v for v in ip.req("GET", f"l2domains/{dom['id']}/vlans")}[vlan]["vlanId"]
        psub = subnet(net)
        if psub is None:
            ip.req("POST", "subnets", json={"subnet": str(net.network_address), "mask": net.prefixlen, "sectionId": sec["id"], "masterSubnetId": master["id"],
                                            "vrfId": vrf_id, "vlanId": vlan_id, "description": f"BGP peering {lb} (AS {x['asn']}) with border1, border2, VLAN {vlan} / L2VNI {x['vni']}; {tag}"})
            psub = subnet(net); n_new += 1
        for i, s in enumerate(SWITCHES): n_new += _ipam_address(ip, psub, str(net.network_address + 1 + i), s, f"{s} br0.{vlan}, eBGP to {lb}")
        n_new += _ipam_address(ip, psub, str(net.broadcast_address - 1), lb, f"{lb} bond0.{vlan}, eBGP AS {x['asn']} (BIRD)")
    log(f"phpIPAM: {n_new} new object(s) for {p['pair']} — the VRRP address {p['vrrp_address']}; per member mgmt, tier address, peering VLAN, its /29 and three addresses")
    # ---- 3 Nautobot ---------------------------------------------------------------------------------------------------------
    log.stage("nautobot")
    site = nb.one("dcim/locations", name=LOCATION); active = nb.one("extras/statuses", name="Active"); connected = nb.one("extras/statuses", name="Connected")
    ref = nb.one("dcim/devices", name="e3t-lb1")                   # the design's load balancer: role, type, platform, rack
    tenant = nb.one("tenancy/tenants", name=f"e3t-{vrf}"); vrf_obj = nb.one("ipam/vrfs", name=vrf, namespace=ns["id"])
    vg = nb.one("ipam/vlan-groups", name=LOCATION)
    def iface(d, name, **f):
        return nb.one("dcim/interfaces", device_id=d["id"], name=name) or nb.post("dcim/interfaces", {"device": d["id"], "name": name, "status": active["id"], **f})
    def ip_on(a, itf, desc, **f):
        o = (nb.get("ipam/ip-addresses", address=a, namespace=ns["id"])["results"] or [None])[0]
        o = o or nb.post("ipam/ip-addresses", {"address": a, "namespace": ns["id"], "status": active["id"], "description": desc, **f})
        if itf and not nb.get("ipam/ip-address-to-interface", ip_address=o["id"], interface=itf["id"])["results"]:
            nb.post("ipam/ip-address-to-interface", {"ip_address": o["id"], "interface": itf["id"]})
        return o
    ctx = nb.context(); data = ctx["data"]; entries = []
    for x in members:
        lb, hw = x["name"], hws[x["name"]]; net = ipaddress.ip_network(x["peering_subnet"])
        dev = nb.one("dcim/devices", name=f"e3t-{lb}") or nb.post("dcim/devices", {
            "name": f"e3t-{lb}", "role": ref["role"]["id"], "device_type": ref["device_type"]["id"], "platform": ref["platform"]["id"],
            "location": site["id"], "rack": ref["rack"]["id"], "status": active["id"], "tenant": tenant["id"],
            "comments": f"Added by Fabric Factory, {tag}: load balancer pair {p['pair']} ({'active' if x['priority'] > 100 else 'standby'})",
            "local_config_context_data": {"idx": hw["idx"], "console": hw["console"], "hostnum": x["hostnum"], "es": hw["es"]}})
        e0 = iface(dev, "eth0", type="10gbase-t", description="OOB management", mgmt_only=True, mac_address=hw["macs"][0])
        mo = ip_on(f"{x['mgmt_ip']}/24", e0, f"{lb} OOB (ansible_host)")
        nb.patch(f"dcim/devices/{dev['id']}", {"primary_ip4": mo["id"]})
        legs = [iface(dev, f"eth{i + 1}", type="10gbase-t", mac_address=hw["macs"][i + 1], description=f"access: {s} {hw['border_port']}") for i, s in enumerate(SWITCHES)]
        if not nb.get("ipam/vrf-device-assignments", vrf=vrf_obj["id"], device=dev["id"])["results"]:   # before an interface may use the VRF
            nb.post("ipam/vrf-device-assignments", {"vrf": vrf_obj["id"], "device": dev["id"]})
        bond = iface(dev, "bond0", type="lag", description=f"LACP to {', '.join(SWITCHES)} (EVPN ES {hw['es']})")
        sub = iface(dev, f"bond0.{p['tier_vlan']}", type="virtual", description=f"VLAN {p['tier_vlan']} ({vrf} tier)", parent_interface=bond["id"], vrf=vrf_obj["id"])
        ip_on(x["address"], sub, f"{lb} ({vrf} tier)", tenant=tenant["id"])
        for i, s in enumerate(SWITCHES):
            sd = nb.one("dcim/devices", name=f"e3t-{s}"); bp = nb.one("dcim/interfaces", device_id=sd["id"], name=hw["border_port"])
            nb.patch(f"dcim/interfaces/{bp['id']}", {"description": f"access: {lb} eth{i + 1}", "enabled": True})
            if not nb.get(f"dcim/interfaces/{legs[i]['id']}").get("cable"):
                nb.post("dcim/cables", {"termination_a_type": "dcim.interface", "termination_a_id": bp["id"], "termination_b_type": "dcim.interface",
                                        "termination_b_id": legs[i]["id"], "status": connected["id"], "label": "access"})
        v = nb.one("ipam/vlans", vid=x["peering_vlan"], vlan_group=vg["id"]) or nb.post("ipam/vlans", {
            "vid": x["peering_vlan"], "name": f"peering-{lb}", "vlan_group": vg["id"], "status": active["id"], "tenant": tenant["id"],
            "description": f"BGP peering {lb} (AS {x['asn']}) with {', '.join(SWITCHES)}, L2VNI {x['vni']} ({tag})"})
        if nb.one("ipam/prefixes", prefix=str(net), namespace=ns["id"]) is None:
            o = nb.post("ipam/prefixes", {"prefix": str(net), "namespace": ns["id"], "status": active["id"], "type": "network", "tenant": tenant["id"], "vlan": v["id"],
                                          "description": f"peering {lb}: switches .1/.2, {lb} the last address — {tag}"})
            nb.patch(f"ipam/prefixes/{o['id']}", {"location": site["id"]}); nb.post("ipam/vrf-prefix-assignments", {"vrf": vrf_obj["id"], "prefix": o["id"]})
        entries.append({"name": lb, "switches": SWITCHES, "vrf": vrf, "asn": x["asn"], "vlan": x["peering_vlan"], "vni": x["vni"], "subnet": str(net),
                        "vips": [], "bfd": True, "added": True, "pair": p["pair"], "tier_vlan": p["tier_vlan"], "span_added": bool(p["span_added"]),
                        "border_port": hw["border_port"],
                        "ha": {"partner": x["partner"], "vrid": p["vrid"], "address": p["vrrp_address"], "priority": x["priority"]}})
    vrole = nb.one("extras/roles", name="VRRP")
    ip_on(p["vrrp_address"], None, f"VRRP {p['pair']} (keepalived, VRID {p['vrid']}, active / standby) — {tag}", tenant=tenant["id"], **({"role": vrole["id"]} if vrole else {}))
    ctx = nb.context(); data = ctx["data"]; changed = False
    for v in data["evpn"]["vlans"]:
        if v["vlan"] == p["tier_vlan"] and "borders" not in v["on"]: v["on"] = v["on"] + ["borders"]; changed = True
    for e in entries:
        mine = next((y for y in data.get("lb_peerings", []) if y["name"] == e["name"]), None)
        _check(mine in (None, e), f"{e['name']} already has another peering in Nautobot: {mine}")
        if mine is None: data["lb_peerings"] = data.get("lb_peerings", []) + [e]; changed = True
    if changed: nb.patch(f"extras/config-contexts/{ctx['id']}", {"data": data})
    if p["span_added"]:
        vo = nb.one("ipam/vlans", vid=p["tier_vlan"], vlan_group=vg["id"])
        if vo and (d := re.sub(r" on [a-z+]+", " on leaves+borders", vo.get("description") or "")) != vo.get("description"):
            nb.patch(f"ipam/vlans/{vo['id']}", {"description": d})
    labmodel.invalidate()
    log(f"Nautobot: e3t-{names[0]}, e3t-{names[1]} (Alpine load balancers, rack edge, tenant e3t-{vrf}) with eth0-eth2, bond0, bond0.{p['tier_vlan']}; "
        f"cables labelled access to border1 / border2 {', '.join(x['border_port'] for x in members)}; the VRRP address {p['vrrp_address']}; "
        f"both peerings in the plan (AS {', '.join(str(x['asn']) for x in members)}, no VIPs, BFD) with the pair's VRRP settings"
        + (f"; VLAN {p['tier_vlan']} now on leaves + borders" if p["span_added"] else ""))
    # ---- 4 rack and cable: the VMs -----------------------------------------------------------------------------------------------
    log.stage("rack")
    racked = added()
    if any(n not in racked for n in names): _write_added(racked + [n for n in names if n not in racked])
    rc, out = _lab_sh("up", *names, timeout=600)
    _check(rc == 0 and all(f"[{n}]" in out for n in names), f"lab.sh up failed: {out[-300:]}")
    log("rack and cable: " + "; ".join(l for l in out.splitlines() if "started" in l or "already running" in l or "creating" in l))
    rc, out = _lab_sh("wait", *names, timeout=600)
    _check(out.count("SSH ready") == len(names), f"SSH does not answer — {out[-200:]}")
    log(f"{p['pair']}: booted (cloud-init: OOB {', '.join(x['mgmt_ip'] for x in members)}, SSH)")
    # ---- 5 + 6 the pipelines: the borders first (the segments and the peerings), then the load balancers; the lab verified -------
    log.stage("switches")
    runs = {"switches": _pipeline("evpn-3tier-switches", log, verify=False)}
    log.stage("loadbalancers")
    runs["loadbalancers"] = _pipeline("evpn-3tier-loadbalancers", log)
    # ---- 7 checks: per member, then the pair's high availability -------------------------------------------------------------------
    log.stage("checks")
    inv, ssh = _lab()
    for x in members:
        lb, hw = x["name"], hws[x["name"]]; lb_ip = str(ipaddress.ip_network(x["peering_subnet"]).broadcast_address - 1)
        for s in SWITCHES:
            o, _t = _wait(lambda: (lambda out: out if '"bgpState":"Established"' in out.replace(" ", "") else None)(
                ssh(s, f"vtysh -c 'show bgp vrf {vrf} neighbors {lb_ip} json'", inv=inv, timeout=60)[1]), 180, 10)
            _check(o, f"{s}: BGP to {lb} ({lb_ip}, VRF {vrf}) not Established")
            _, bfd = ssh(s, "vtysh -c 'show bfd peers brief'", inv=inv, timeout=60)
            _check(any(lb_ip in l and " up " in l for l in bfd.splitlines()), f"{s}: BFD to {lb} not up")
            _, es = ssh(s, "vtysh -c 'show evpn es json'", inv=inv, timeout=60)
            try: esis = [e.get("esi", "") for e in json.loads(es[es.index("["):])]
            except ValueError: esis = []
            _check(any(e.endswith(f":{hw['es']:02x}") for e in esis), f"{s}: Ethernet Segment {hw['es']} not present ({esis})")
        _, o = ssh(lb, "doas birdc show protocols", inv=inv, timeout=60)
        est = [l.split()[0] for l in o.splitlines() if l.split()[:1] and l.split()[0] in SWITCHES and "Established" in l]
        _check(sorted(est) == SWITCHES, f"{lb}: BIRD sessions {est or o[-200:]}")
        _, o = ssh(lb, f"ping -c 2 -W 2 {p['tier_gateway']}", inv=inv, timeout=30)
        _check(" 0% packet loss" in o, f"{lb} cannot reach its gateway {p['tier_gateway']}")
        log(f"{lb}: eBGP from border1 / border2 ({lb_ip}, AS {x['asn']}) Established in VRF {vrf}, BFD up, Ethernet Segment {hw['es']} on both borders; "
            f"BIRD Established with both; the gateway answers")
    act, stb = names
    held, t = _wait(lambda: (lambda h: h if h == [act] else None)(_vrrp_holder(ssh, inv, names, p["vrrp_address"])), 60)
    _check(held == [act], f"the VRRP address {p['vrrp_address']} is held by {_vrrp_holder(ssh, inv, names, p['vrrp_address']) or 'nobody'}, want {act} only")
    vip = p["vrrp_address"].split("/")[0]
    client = next((n["name"] for n in inv["nodes"] if n.get("net") and n["net"]["vrf"] == vrf and n["role"] != "lb"), None) or stb
    reach = lambda: " 0% packet loss" in ssh(client, f"ping -c 1 -W 1 {vip}", inv=inv, timeout=30)[1]   # noqa: E731
    _check(_wait(reach, 20, 0.5)[0], f"{client} cannot reach the VRRP address {vip}")
    log(f"VRRP: {act} active, holding {p['vrrp_address']}; {stb} standby; {client} reaches it")
    # the failover test: the active one's keepalived stopped -> the standby takes the address; then back (preemption)
    ssh(act, "doas rc-service keepalived stop", inv=inv, timeout=60)
    try:
        held, t_over = _wait(lambda: (lambda h: h if h == [stb] else None)(_vrrp_holder(ssh, inv, names, p["vrrp_address"])), 30, 0.5)
        _check(held == [stb], f"failover: {stb} did not take {vip} over (held by {_vrrp_holder(ssh, inv, names, p['vrrp_address']) or 'nobody'})")
        ok, t_reach = _wait(reach, 20, 0.5)     # the fabric learns that the VRRP MAC moved to the standby's segment
        _check(ok, f"failover: {client} cannot reach {vip} on {stb}")
    finally:
        ssh(act, "doas rc-service keepalived start", inv=inv, timeout=60)
    held, t_back = _wait(lambda: (lambda h: h if h == [act] else None)(_vrrp_holder(ssh, inv, names, p["vrrp_address"])), 60, 0.5)
    _check(held == [act], f"after the failover test {act} did not take {vip} back")
    _check(_wait(reach, 20, 0.5)[0], f"after the failover test {client} cannot reach {vip}")
    log(f"failover test: keepalived stopped on {act} -> {stb} held {vip} after {t_over:.1f} s and {client} reached it {t_reach:.1f} s later; "
        f"keepalived started again -> {act} took it back after {t_back:.1f} s")
    log.stage("ping")
    pings = _ping_from_wan(inv, ssh, [], log)
    return {"pair": names, "vrf": vrf, "vrrp": p["vrrp_address"], "failover_s": round(t_over, 1), "runs": runs, "ping": pings}


# ---- delete -------------------------------------------------------------------------------------------------------------------
def delete_pair(r, log):
    _pipeline, _lab, _ping_from_wan = _helpers()
    p = r.parameters; names = p["names"]; hws = {n: hardware(int(n[2:])) for n in names}
    nb, ip = Nautobot(), Ipam()
    # ---- 1 pre-flight -------------------------------------------------------------------------------------------------------
    log.stage("preflight")
    have = added()
    _check(set(names) <= set(have) and have[-2:] == names, f"{p['pair']} is not the last pair added — nothing changed")
    ctx = nb.context(); data = ctx["data"]
    entries = [next((x for x in data.get("lb_peerings", []) if x["name"] == n), None) or next((e for e in p.get("entries", []) if e["name"] == n), None) for n in names]
    _check(all(e and e.get("added") for e in entries), f"{p['pair']}: not peerings Fabric Factory added — nothing changed")
    vrrp = (entries[0].get("ha") or {}).get("address")
    log(f"pre-flight: {p['pair']} is the last pair added (VRF {entries[0]['vrf']}, VRRP {vrrp})")
    # ---- 2 Nautobot ---------------------------------------------------------------------------------------------------------
    log.stage("nautobot")
    ns = nb.one("ipam/namespaces", name=LOCATION); vg = nb.one("ipam/vlan-groups", name=LOCATION)
    inv, ssh = _lab()
    borders = {n["name"] for n in inv["nodes"] if n["role"] == "border"}
    tv = entries[0].get("tier_vlan")
    others = [n["name"] for n in inv["nodes"] if n["name"] not in names and tv in (n.get("vlans") or [])
              and any(x.get("peer") in borders for x in n["ports"] if x.get("kind") == "access")]
    revert = bool(entries[0].get("span_added")) and not others
    data["lb_peerings"] = [x for x in data.get("lb_peerings", []) if x["name"] not in names]
    if revert:
        for v in data["evpn"]["vlans"]:
            if v["vlan"] == tv: v["on"] = [x for x in v["on"] if x != "borders"]
    nb.patch(f"extras/config-contexts/{ctx['id']}", {"data": data})
    if revert:
        vo = nb.one("ipam/vlans", vid=tv, vlan_group=vg["id"])
        if vo and (d := re.sub(r" on [a-z+]+", " on leaves", vo.get("description") or "")) != vo.get("description"):
            nb.patch(f"ipam/vlans/{vo['id']}", {"description": d})
    gone = []
    for n, e in zip(names, entries):
        dev = nb.one("dcim/devices", name=f"e3t-{n}")
        if dev:
            addrs = [a["address"] for a in nb.get("ipam/ip-addresses", device_id=dev["id"])["results"]]
            for c in nb.get("dcim/cables", device_id=dev["id"], limit=50)["results"]: nb.delete(f"dcim/cables/{c['id']}")
            nb.delete(f"dcim/devices/{dev['id']}"); gone.append(f"e3t-{n}")
            for a in addrs:
                for o in nb.get("ipam/ip-addresses", address=a, namespace=ns["id"])["results"]: nb.delete(f"ipam/ip-addresses/{o['id']}")
        for o in nb.get("ipam/prefixes", prefix=e["subnet"], namespace=ns["id"])["results"]: nb.delete(f"ipam/prefixes/{o['id']}"); gone.append(e["subnet"])
        vo = nb.one("ipam/vlans", vid=e["vlan"], vlan_group=vg["id"])
        if vo: nb.delete(f"ipam/vlans/{vo['id']}"); gone.append(f"VLAN {e['vlan']}")
        for s in SWITCHES:
            sd = nb.one("dcim/devices", name=f"e3t-{s}"); bp = nb.one("dcim/interfaces", device_id=sd["id"], name=hws[n]["border_port"])
            if bp: nb.patch(f"dcim/interfaces/{bp['id']}", {"description": "spare port (for an added load balancer)", "enabled": False})
    if vrrp:
        for o in nb.get("ipam/ip-addresses", address=vrrp, namespace=ns["id"])["results"]: nb.delete(f"ipam/ip-addresses/{o['id']}"); gone.append(f"VRRP {vrrp}")
    labmodel.invalidate()
    log(f"Nautobot: both peerings out of the plan; removed {', '.join(gone) or 'nothing else (already gone)'} and their addresses; "
        f"border1 / border2 {', '.join(hws[n]['border_port'] for n in names)} spare and disabled again"
        + (f"; VLAN {tv} back to the leaves only" if revert else f"; VLAN {tv} stays on the borders ({', '.join(others)} use it)" if entries[0].get("span_added") else ""))
    # ---- 3 the switches pipeline: the borders lose the segments and the peerings (managed deletions); the lab verified -------------
    log.stage("switches")
    runs = {"switches": _pipeline("evpn-3tier-switches", log)}
    # ---- 4 un-rack ------------------------------------------------------------------------------------------------------------
    log.stage("rack")
    rc, out = 0, ""
    present = [n for n in names if n in added()]
    if present:
        _lab_sh("down", *present, timeout=300); rc, out = _lab_sh("clean", *present, timeout=300)
    _check(rc == 0, f"lab.sh clean failed: {out[-300:]}")
    _write_added([n for n in added() if n not in names])
    for n in names: shutil.rmtree(settings.LAB_DIR / "nodes" / n, ignore_errors=True)
    inv, ssh = _lab()
    pat = "|".join([f"LB-{n.upper()}-IN" for n in names] + [f"vif {e['vlan']} " for e in entries] + [f"bond{hws[n]['es']} " for n in names])
    for s in SWITCHES:
        _, o = ssh(s, f"/opt/vyatta/bin/vyatta-op-cmd-wrapper show configuration commands | grep -E '{pat}'", inv=inv, timeout=60)
        _check(not o.strip(), f"{s} still has the pair's peerings or segments: {o.strip()[:200]}")
    log(f"un-racked: {p['pair']} powered off, their VMs and disks deleted; added-lbs.conf updated; border1 / border2 have no trace of their peerings or segments")
    # ---- 5 phpIPAM, last ----------------------------------------------------------------------------------------------------------
    log.stage("phpipam")
    sec, subnet = _ipam_ctx(ip); n_rel = 0
    for host in names + ["-".join(names) + "-vrrp"]:
        for x in ip.req("GET", f"addresses/search_hostname/{host}") or []:
            if str(x.get("hostname")) == host: ip.req("DELETE", f"addresses/{x['id']}"); n_rel += 1
    dom = next(d for d in ip.req("GET", "l2domains") if d["name"] == LOCATION)
    for e in entries:
        ps = subnet(e["subnet"])
        if ps:
            for x in ip.req("GET", f"subnets/{ps['id']}/addresses") or []: ip.req("DELETE", f"addresses/{x['id']}"); n_rel += 1
            ip.req("DELETE", f"subnets/{ps['id']}"); n_rel += 1
        for x in ip.req("GET", f"l2domains/{dom['id']}/vlans") or []:
            if int(x["number"]) == e["vlan"]: ip.req("DELETE", f"vlan/{x['vlanId']}"); n_rel += 1
    log(f"phpIPAM: {n_rel} object(s) of {p['pair']} released — their addresses, the VRRP address, the peering subnets and VLANs")
    log.stage("ping")
    pings = _ping_from_wan(inv, ssh, [], log)
    return {"pair": names, "runs": runs, "ping": pings}


def run(r, log):
    return (add_pair if r.parameters.get("op") == "add" else delete_pair)(r, log)
