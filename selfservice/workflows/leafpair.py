"""Add a leaf pair (a new rack of two leaves) to the running fabric, or remove the last one added.

The hardware inventory stays lab.conf's: an added pair is one line in added-leaves.conf (ADDED_LEAVES=(leaf5 leaf6 ...),
written here, not in git), and lab.conf derives everything from the leaf number k (5-8): index 16+k, mgmt 10.18.0.(20+k),
loopback 10.61.0.(10+k), AS 65100+k, rack ceil(k/2), uplinks spine1/spine2 eth(2+k) — the spines' spare ports eth7-10 —
to eth1/eth2; eth3-eth7 are spare ports (Port Provisioning), disabled. Nautobot stays the source of truth for the
configuration: the switches pipeline renders the new leaves and the spines' new neighbours from it.

add-leaf-pair     pre-flight (host memory, the spines' spare ports up and unused, the names / addresses / ASNs free in
                  Nautobot, phpIPAM and the lab) -> phpIPAM (mgmt, loopbacks, SVIs, probes) -> Nautobot (rack, devices,
                  interfaces, addresses, cables to the spines) -> rack and cable (added-leaves.conf, the VMs, day-0 over
                  the serial console) -> switches pipeline (the whole lab verified, the new leaves included) -> checks
                  (BGP + BFD to both spines, the same VNIs and anycast gateways as leaf1, the loopbacks reachable) -> ping
remove-leaf-pair  pre-flight (the last pair added; no server cabled to it, no spare port provisioned) -> Nautobot (the
                  devices, their addresses, the cables; the spines' ports back to spare) -> switches pipeline (managed
                  deletions take the neighbours off the spines) -> the VMs powered off and deleted, added-leaves.conf ->
                  phpIPAM released -> ping
Every stage checks before it writes and is idempotent: a failed request is retried from the top."""
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import time

from django.conf import settings

from . import labmodel
from .clients import LOCATION, Ipam, Nautobot

OP = "/opt/vyatta/bin/vyatta-op-cmd-wrapper"


def _check(cond, msg):
    if not cond: raise RuntimeError(msg)


def _helpers():
    """executors / changes import this module (EXECUTORS): their helpers are imported when a request runs."""
    from .changes import _pipeline
    from .executors import _lab, _ping_from_wan
    return _pipeline, _lab, _ping_from_wan

FIRST, LAST = 5, 8                  # leaf numbers an added pair may use (the spines' spare ports eth7-eth10)
RAM_MIB = 1024                      # per leaf (lab.conf VYOS_RAM_MIB)
SPARE = (3, 4, 5, 6, 7)             # a new leaf's spare ports


# ---- the hardware inventory ---------------------------------------------------------------------------------------------------
def _conf():
    return settings.LAB_DIR / "added-leaves.conf"


def added():
    """The leaves added so far, in order (added-leaves.conf)."""
    f = _conf()
    m = re.search(r"ADDED_LEAVES=\(([^)]*)\)", f.read_text()) if f.exists() else None
    return m.group(1).split() if m else []


def _write_added(leaves):
    f = _conf()
    if not leaves: f.unlink(missing_ok=True); return
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text("# leaf pairs added by Fabric Factory (Add / Remove a leaf pair): the hardware inventory lab.conf reads\n"
                   f"ADDED_LEAVES=({' '.join(leaves)})\n")
    os.replace(tmp, f)


def _lab_sh(*args, timeout=900, env=None):
    r = subprocess.run([str(settings.LAB_DIR / "lab.sh"), *args], capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, **(env or {})})
    return r.returncode, (r.stdout + r.stderr).strip()


def plan_inventory(leaves=()):
    """lab.conf's inventory with these leaves as if racked (lab.conf: E3T_PLAN_LEAVES) — nothing is written."""
    rc, out = _lab_sh("inventory", timeout=60, env={"E3T_PLAN_LEAVES": " ".join(leaves)})
    _check(rc == 0, f"lab.sh inventory failed: {out[-300:]}")
    return json.loads(out[out.index("{"):])


def allocation(k):
    """Leaf number k: what lab.conf derives for it."""
    return {"name": f"leaf{k}", "k": k, "idx": 16 + k, "mgmt_ip": f"10.18.0.{20 + k}", "loopback": f"10.61.0.{10 + k}", "asn": 65100 + k,
            "rack": f"rack{(k + 1) // 2}", "spine_port": f"eth{2 + k}", "console": 5620 + 16 + k}


def next_pair():
    """The next pair to add: (k, k+1) after the highest leaf, or None when the spines have no spare ports left."""
    have = [int(n[4:]) for n in added()]
    k = max(have) + 1 if have else FIRST
    return (allocation(k), allocation(k + 1)) if k + 1 <= LAST else None


def last_pair():
    """The pair Remove a leaf pair may take: the last one added (leaf numbers stay contiguous, so no SVI address moves)."""
    a = added()
    return tuple(allocation(int(n[4:])) for n in a[-2:]) if len(a) >= 2 else None


def mem_available_mib():
    for l in open("/proc/meminfo"):
        if l.startswith("MemAvailable:"): return int(l.split()[1]) // 1024
    return 0


def pair_label(pair):
    a, b = pair
    return (f"{a['name']} + {b['name']} in {a['rack']}: AS {a['asn']} / {b['asn']}, mgmt {a['mgmt_ip']} / {b['mgmt_ip']}, "
            f"loopbacks {a['loopback']} / {b['loopback']}, spine ports {a['spine_port']} / {b['spine_port']}")


# ---- helpers ------------------------------------------------------------------------------------------------------------------
def _ipam_targets(nb, inv_nodes, pair):
    """phpIPAM reservations for the pair: [(ip, cidr, host, description, mac)]."""
    ctx = nb.context()["data"]; ev = ctx["evpn"]; N = {n["name"]: n for n in inv_nodes}
    out = []
    for a in pair:
        n = N[a["name"]]; mac = n["ports"][0]["mac"]
        out.append((a["mgmt_ip"], "10.18.0.0/24", a["name"], f"leaf OOB eth0, VM e3t-{a['name']}, console :{a['console']}", mac))
        out.append((a["loopback"], "10.61.0.0/24", a["name"], f"loopback: router-id, VTEP, AS {a['asn']}", None))
        for v in ev["vlans"]:
            if "leaves" not in v["on"]: continue
            net = ipaddress.ip_interface(v["gateway"]).network
            out.append((str(net.network_address + ev["svi_host_base"] + a["k"]), str(net), a["name"], f"{a['name']} own address (br0.{v['vlan']})", None))
        used = {x["vrf"] for x in ev["vlans"] if "leaves" in x["on"]}
        for v in ev["vrfs"]:
            if v["name"] in used and v.get("probe"):
                out.append((f"{v['probe']}.{a['loopback'].split('.')[-1]}", f"{v['probe']}.0/24", a["name"], f"probe address in VRF {v['name']}", None))
    return out


def _ipam_subnet(ip, sec, cidr):
    net = ipaddress.ip_network(cidr)
    return next((x for x in ip.req("GET", f"subnets/cidr/{net.network_address}/{net.prefixlen}") or [] if x["sectionId"] == sec["id"]), None)


def _bgp_up(ssh, inv, node, ports):
    _, o = ssh(node, "vtysh -c 'show bgp summary'", inv=inv, timeout=60)
    rows = {l.split()[0]: l.split() for l in o.splitlines() if l.split() and l.split()[0] in ports}
    return {p: (p in rows and rows[p][-3].isdigit()) for p in ports}                # PfxRcd a number = Established


def _vnis(ssh, inv, node):
    _, o = ssh(node, "vtysh -c 'show evpn vni'", inv=inv, timeout=60)
    return {int(l.split()[0]) for l in o.splitlines() if l.split() and l.split()[0].isdigit()}


def _gateways(ssh, inv, node):
    _, o = ssh(node, f"{OP} show configuration commands | grep -E 'pseudo-ethernet peth[0-9]+ address'", inv=inv, timeout=60)
    return {l.replace("'", "").split()[-1] for l in o.splitlines() if "address" in l}


# ---- add a leaf pair ----------------------------------------------------------------------------------------------------------
def add_pair(r, log):
    _pipeline, _lab, _ping_from_wan = _helpers()
    p = r.parameters; pair = tuple(allocation(k) for k in p["ks"]); names = [a["name"] for a in pair]
    nb, ip = Nautobot(), Ipam(); tag = f"request #{r.pk}"
    sys.path.insert(0, str(settings.LAB_DIR / "tools")); from render import _desc   # noqa: E402  (the seed's port descriptions)
    # ---- 1 pre-flight -------------------------------------------------------------------------------------------------------
    log.stage("preflight")
    racked = added()
    retry = all(n in racked for n in names)                          # a retry after the rack stage: the VMs exist
    _check(retry or not any(n in racked for n in names), f"{' / '.join(n for n in names if n in racked)} already racked: inconsistent — nothing changed")
    _check(retry or (next_pair() and [a["k"] for a in next_pair()] == list(p["ks"])), f"{' + '.join(names)} is no longer the next pair to add — nothing changed")
    free = mem_available_mib()
    _check(retry or free >= 2 * RAM_MIB + 2048, f"the host has {free} MiB available: two leaves need {2 * RAM_MIB} MiB plus 2 GiB headroom — nothing changed")
    inv, ssh = _lab()
    for s in ("spine1", "spine2"):
        _, o = ssh(s, "ip -br link", inv=inv, timeout=60)
        for a in pair:
            _check(f"{a['spine_port']} " in o or f"{a['spine_port']}@" in o, f"{s} has no {a['spine_port']} (its spare ports are added once, with a restart) — nothing changed")
        sn = next(n for n in inv["nodes"] if n["name"] == s)
        for a in pair:
            peer = next((x.get("peer") for x in sn["ports"] if x["name"] == a["spine_port"]), None)
            _check(peer in (None, a["name"]), f"{s} {a['spine_port']} is cabled to {peer} in Nautobot — nothing changed")
    devs = {d["name"] for d in nb.get("dcim/devices", location=LOCATION, limit=500)["results"]}
    ns = nb.one("ipam/namespaces", name=LOCATION)
    for a in pair:
        if not retry: _check(f"e3t-{a['name']}" not in devs, f"Nautobot already has e3t-{a['name']} — nothing changed")
        for addr in (a["mgmt_ip"], a["loopback"]):
            hit = nb.get("ipam/ip-addresses", address=addr, namespace=ns["id"])["results"]
            _check(retry or not hit, f"{addr} is already in Nautobot ({hit[0]['description'] if hit else ''}) — nothing changed")
            if not retry: _check(subprocess.run(["ping", "-c", "1", "-W", "1", addr], capture_output=True).returncode != 0, f"{addr} answers ping: in use — nothing changed")
        used_as = {n.get("asn") for n in inv["nodes"]} | {x.get("asn") for x in nb.context()["data"].get("lb_peerings", [])}
        _check(retry or a["asn"] not in used_as, f"AS {a['asn']} is in use — nothing changed")
    log(f"pre-flight: {pair_label(pair)}; host memory {free} MiB available" + (" (a retry: the pair is already racked)" if retry else ""))
    # ---- 2 phpIPAM ----------------------------------------------------------------------------------------------------------
    log.stage("phpipam")
    planned = plan_inventory([] if retry else names)
    sec = next(s for s in ip.req("GET", "sections") if s["name"] == LOCATION); n_new = 0
    for addr, cidr, host, desc, mac in _ipam_targets(nb, planned["nodes"], pair):
        sub = _ipam_subnet(ip, sec, cidr)
        if sub is None: log(f"phpIPAM: no subnet {cidr} — {addr} skipped"); continue
        have = next((x for x in ip.req("GET", f"subnets/{sub['id']}/addresses") or [] if x["ip"] == addr), None)
        if have: _check(have.get("hostname") == host, f"phpIPAM: {addr} is reserved for {have.get('hostname')} — stopped"); continue
        ip.req("POST", "addresses", json={"subnetId": sub["id"], "ip": addr, "hostname": host, "description": f"{desc}, {tag}"[:64], **({"mac": mac} if mac else {})}); n_new += 1
    log(f"phpIPAM: {n_new} address(es) reserved for {' + '.join(names)} — mgmt, loopbacks, VLAN addresses, probes")
    # ---- 3 Nautobot ---------------------------------------------------------------------------------------------------------
    log.stage("nautobot")
    N = {n["name"]: n for n in planned["nodes"]}
    site = nb.one("dcim/locations", name=LOCATION); active = nb.one("extras/statuses", name="Active"); connected = nb.one("extras/statuses", name="Connected")
    role = nb.one("extras/roles", name="e3t-leaf"); dtype = nb.one("dcim/device-types", model="VyOS"); plat = nb.one("dcim/platforms", name="vyos")
    loop_role = nb.one("extras/roles", name="Loopback")
    rack_name = pair[0]["rack"]
    rack = nb.one("dcim/racks", name=rack_name, location=site["id"]) or nb.post("dcim/racks", {"name": rack_name, "location": site["id"], "status": active["id"]})
    def ip_on(addr, itf, desc, role_=None):
        o = (nb.get("ipam/ip-addresses", address=addr, namespace=ns["id"])["results"] or [None])[0]
        o = o or nb.post("ipam/ip-addresses", {"address": addr, "namespace": ns["id"], "status": active["id"], "description": desc, **({"role": role_["id"]} if role_ else {})})
        if not nb.get("ipam/ip-address-to-interface", ip_address=o["id"], interface=itf["id"])["results"]:
            nb.post("ipam/ip-address-to-interface", {"ip_address": o["id"], "interface": itf["id"]})
        return o
    def iface(dev, name, **f):
        return nb.one("dcim/interfaces", device_id=dev["id"], name=name) or nb.post("dcim/interfaces", {"device": dev["id"], "name": name, "status": active["id"], **f})
    for a in pair:
        n = N[a["name"]]
        dev = nb.one("dcim/devices", name=f"e3t-{a['name']}") or nb.post("dcim/devices", {
            "name": f"e3t-{a['name']}", "role": role["id"], "device_type": dtype["id"], "platform": plat["id"], "location": site["id"], "rack": rack["id"],
            "status": active["id"], "local_config_context_data": {"idx": a["idx"], "console": a["console"], "asn": a["asn"]}})
        e0 = iface(dev, "eth0", type="10gbase-t", description="OOB management", mgmt_only=True, mac_address=n["ports"][0]["mac"])
        m = ip_on(f"{a['mgmt_ip']}/24", e0, f"{a['name']} OOB (ansible_host)")
        nb.patch(f"dcim/devices/{dev['id']}", {"primary_ip4": m["id"]})
        for port in n["ports"][1:]:
            spare = port["kind"] is None
            iface(dev, port["name"], type="10gbase-t", mac_address=port["mac"], description="spare port (Port Provisioning)" if spare else _desc(port),
                  **({"mtu": planned["fabric_mtu"]} if port["kind"] == "fabric" else {}), **({"enabled": False} if spare else {}))
        ip_on(f"{a['loopback']}/32", iface(dev, "dum0", type="virtual", description="loopback / router-id"), f"{a['name']} loopback", loop_role)
    for l in planned["links"]:
        if not {l["a"], l["b"]} & set(names): continue
        ends = []
        for node, port in ((l["a"], l["a_port"]), (l["b"], l["b_port"])):
            dev = nb.one("dcim/devices", name=f"e3t-{node}")
            pn = next(x for x in N[node]["ports"] if x["name"] == port)
            itf = iface(dev, port, type="10gbase-t", mac_address=pn["mac"], description=_desc(pn), mtu=planned["fabric_mtu"])
            if node.startswith("spine"): nb.patch(f"dcim/interfaces/{itf['id']}", {"description": _desc(pn), "mtu": planned["fabric_mtu"], "enabled": True})
            ends.append(itf)
        x, y = ends
        if not (nb.get(f"dcim/interfaces/{x['id']}").get("cable")):
            nb.post("dcim/cables", {"termination_a_type": "dcim.interface", "termination_a_id": x["id"], "termination_b_type": "dcim.interface",
                                    "termination_b_id": y["id"], "status": connected["id"], "label": "fabric"})
    labmodel.invalidate()
    log(f"Nautobot: {rack_name}; e3t-{names[0]}, e3t-{names[1]} (role e3t-leaf, VyOS) with eth0-eth7 and dum0 — eth3-eth7 spare and disabled; "
        f"mgmt and loopback addresses; 4 cables labelled fabric to spine1 / spine2 {pair[0]['spine_port']}, {pair[1]['spine_port']}")
    # ---- 4 rack and cable: the VMs, day-0 --------------------------------------------------------------------------------------
    log.stage("rack")
    if not retry: _write_added(racked + names)
    rc, out = _lab_sh("gen", timeout=300); _check(rc == 0, f"lab.sh gen failed: {out[-300:]}")
    rc, out = _lab_sh("up", *names, timeout=600); _check(rc == 0, f"lab.sh up failed: {out[-300:]}")
    log("rack and cable: " + "; ".join(l for l in out.splitlines() if "started" in l or "already running" in l))
    rc, out = _lab_sh("bootstrap", *names, timeout=1500)
    _check(rc == 0, f"day-0 failed: {out[-400:]}")
    log("day-0 over the serial console: " + "; ".join(l for l in out.splitlines() if l.startswith("[") and ("OK" in l or "FAIL" in l)))
    # ---- 5 the switches pipeline: the full config from Nautobot, then the whole lab verified --------------------------------------
    log.stage("switches")
    run = _pipeline("evpn-3tier-switches", log)
    # ---- 6 checks -------------------------------------------------------------------------------------------------------------
    log.stage("checks")
    inv, ssh = _lab()
    want_vni, want_gw = _vnis(ssh, inv, "leaf1"), _gateways(ssh, inv, "leaf1")
    for a in pair:
        t0 = time.time()
        while not all((up := _bgp_up(ssh, inv, a["name"], ("eth1", "eth2"))).values()) and time.time() - t0 < 180: time.sleep(10)
        _check(all(up.values()), f"{a['name']}: BGP to the spines {up}")
        for s in ("spine1", "spine2"):
            _check(_bgp_up(ssh, inv, s, (a["spine_port"],))[a["spine_port"]], f"{s} {a['spine_port']}: BGP to {a['name']} not Established")
        _, bfd = ssh(a["name"], "vtysh -c 'show bfd peers brief'", inv=inv, timeout=60)
        _check(bfd.count(" up ") >= 2, f"{a['name']}: BFD not up to both spines")
        vni, gw = _vnis(ssh, inv, a["name"]), _gateways(ssh, inv, a["name"])
        _check(vni == want_vni, f"{a['name']} has VNIs {sorted(vni)}, leaf1 {sorted(want_vni)}")
        _check(gw == want_gw, f"{a['name']} has anycast gateways {sorted(gw)}, leaf1 {sorted(want_gw)}")
        rc_, o = ssh("leaf1", f"ping -c 2 -W 2 -I 10.61.0.11 {a['loopback']}", inv=inv, timeout=30)
        _check(" 0% packet loss" in o, f"leaf1 cannot reach {a['name']}'s loopback {a['loopback']}")
        log(f"on {a['name']}: BGP + BFD up to both spines (spine1 / spine2 {a['spine_port']}); {len(vni)} VNIs and {len(gw)} anycast gateways, "
            f"the same as leaf1; loopback {a['loopback']} reachable from leaf1")
    # ---- 7 nothing else broke -------------------------------------------------------------------------------------------------
    log.stage("ping")
    pings = _ping_from_wan(inv, ssh, [], log)
    return {"leaves": names, "rack": rack_name, "switches_run": run, "ping": pings}


# ---- remove a leaf pair -------------------------------------------------------------------------------------------------------
def in_use(names):
    """Why the pair cannot go: servers cabled to it, spare ports provisioned (Nautobot)."""
    m = labmodel.model(); why = []
    for x in m.get("ports", []):
        if x["switch"] not in names: continue
        if x.get("kind") == "access": why.append(f"{x['switch']} {x['name']}: {x.get('peer')} is cabled to it")
        elif x.get("kind") is None and (x.get("enabled") or x.get("mode") or x.get("application") or x.get("profile")):
            why.append(f"{x['switch']} {x['name']} is provisioned" + (f" ({x['application']['app_id']})" if x.get("application") else ""))
    return why


def remove_pair(r, log):
    _pipeline, _lab, _ping_from_wan = _helpers()
    p = r.parameters; pair = tuple(allocation(k) for k in p["ks"]); names = [a["name"] for a in pair]
    nb, ip = Nautobot(), Ipam()
    # ---- 1 pre-flight -------------------------------------------------------------------------------------------------------
    log.stage("preflight")
    racked = added()
    _check(all(n in racked for n in names) and racked[-2:] == names, f"{' + '.join(names)} is not the last pair added — nothing changed")
    labmodel.invalidate()
    why = in_use(names)
    _check(not why, "the pair is in use: " + "; ".join(why) + " — nothing changed")
    log(f"pre-flight: {' + '.join(names)} ({pair[0]['rack']}): no server cabled, every spare port unprovisioned")
    # ---- 2 Nautobot ---------------------------------------------------------------------------------------------------------
    log.stage("nautobot")
    ns = nb.one("ipam/namespaces", name=LOCATION); gone = []
    for a in pair:
        dev = nb.one("dcim/devices", name=f"e3t-{a['name']}")
        if dev:
            for c in nb.get("dcim/cables", device_id=dev["id"], limit=50)["results"]: nb.delete(f"dcim/cables/{c['id']}")
            nb.delete(f"dcim/devices/{dev['id']}"); gone.append(f"e3t-{a['name']}")
        for addr in (f"{a['mgmt_ip']}/24", f"{a['loopback']}/32"):
            for o in nb.get("ipam/ip-addresses", address=addr, namespace=ns["id"])["results"]: nb.delete(f"ipam/ip-addresses/{o['id']}")
        for s in ("spine1", "spine2"):
            sd = nb.one("dcim/devices", name=f"e3t-{s}"); itf = nb.one("dcim/interfaces", device_id=sd["id"], name=a["spine_port"])
            if itf: nb.patch(f"dcim/interfaces/{itf['id']}", {"description": "spare fabric port (for an added leaf pair)"})
    site = nb.one("dcim/locations", name=LOCATION); rack = nb.one("dcim/racks", name=pair[0]["rack"], location=site["id"])
    if rack and not nb.get("dcim/devices", rack=rack["id"])["results"]: nb.delete(f"dcim/racks/{rack['id']}"); gone.append(rack["name"])
    labmodel.invalidate()
    log(f"Nautobot: removed {', '.join(gone) or 'nothing (already gone)'}, their addresses and cables; spine ports {pair[0]['spine_port']}, {pair[1]['spine_port']} spare again")
    # ---- 3 the switches pipeline: the spines lose the neighbours (managed deletions) --------------------------------------------
    log.stage("switches")
    run = _pipeline("evpn-3tier-switches", log)
    # ---- 4 un-rack: the VMs off and deleted, the hardware inventory -------------------------------------------------------------
    log.stage("rack")
    rc, out = 0, ""
    if all(n in added() for n in names):
        _lab_sh("down", *names, timeout=300); rc, out = _lab_sh("clean", *names, timeout=300)
    _check(rc == 0, f"lab.sh clean failed: {out[-300:]}")
    _write_added([n for n in added() if n not in names])
    for n in names: shutil.rmtree(settings.LAB_DIR / "nodes" / n, ignore_errors=True)    # the day-0 file lab.sh gen left
    log(f"un-racked: {' + '.join(names)} powered off, their VMs and disks deleted; added-leaves.conf updated")
    inv, ssh = _lab()
    for s in ("spine1", "spine2"):
        _, o = ssh(s, f"{OP} show configuration commands | grep -E 'neighbor ({pair[0]['spine_port']}|{pair[1]['spine_port']}) '", inv=inv, timeout=60)
        _check(not o.strip(), f"{s} still has BGP neighbours on {pair[0]['spine_port']} / {pair[1]['spine_port']}")
    log(f"on the spines: no BGP neighbour left on {pair[0]['spine_port']} / {pair[1]['spine_port']}")
    # ---- 5 phpIPAM: released last ---------------------------------------------------------------------------------------------
    log.stage("phpipam")
    sec = next(s for s in ip.req("GET", "sections") if s["name"] == LOCATION); n_rel = 0
    for host in names:
        for x in ip.req("GET", f"addresses/search_hostname/{host}") or []:
            if str(x.get("hostname")) == host: ip.req("DELETE", f"addresses/{x['id']}"); n_rel += 1
    log(f"phpIPAM: {n_rel} address(es) of {' + '.join(names)} released")
    log.stage("ping")
    pings = _ping_from_wan(inv, ssh, [], log)
    return {"leaves": names, "switches_run": run, "ping": pings}
