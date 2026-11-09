"""Carrying out approved requests. The worker (manage.py e3t_worker) calls run(request, log); each step logs to the
request's history and is idempotent, so a failed request can be retried from the top.

add-vlan
  1 Nautobot   the VLAN in the evpn-3tier plan (config context: VLAN, VRF, VNI, gateway, span) — what the pipelines
               render from — plus the native VLAN (group evpn-3tier) and its prefix (namespace evpn-3tier, in the VRF)
  2 Gitea      the evpn-3tier-switches pipeline: inventory from Nautobot, check, apply to the leaves / borders, verify
  3 phpIPAM    the VLAN (domain evpn-3tier), the subnet (under 172.16.0.0/16, its VRF and VLAN), the gateway reservation
  4 devices    every switch in the span carries the VLAN (vlan-to-vni) and its anycast gateway
lb-peering
  not automated yet: the switch templates have no BGP peering towards hosts, and there is no load balancer device to
  put on the fabric — approval records the decision; an operator completes it by hand (or it waits for the automation)"""
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
    ok, num, url = Gitea().dispatch_and_wait("evpn-3tier-switches", log)
    _check(ok, f"evpn-3tier-switches run #{num} failed — {url}")
    log(f"evpn-3tier-switches run #{num} succeeded (inventory committed, checked, applied, verified): {url}")
    # ---- 3 phpIPAM -------------------------------------------------------------------------------------------------------------
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
    raise NotAutomated("load balancer peering is not automated yet: the switch templates have no BGP peering towards hosts, and the lab has "
                       "no load balancer device for it. Approved = recorded; an operator completes it by hand, or it waits for the automation.")


EXECUTORS = {"add-vlan": add_vlan, "lb-peering": lb_peering}
