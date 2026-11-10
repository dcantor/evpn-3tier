"""Allocation for *Add a VLAN*: the app picks the VLAN ID and the subnet — the requester picks only the VRF.

  VLAN ID   the next unused one: the lowest ID above the highest tier VLAN that nothing uses — not Nautobot's plan
            (tier, transit, L3 VLANs, peerings), not phpIPAM's VLANs, not an open request (submitted / approved /
            running) — and whose L2VNI (10000 + ID) is free too
  subnet    phpIPAM's first free /24 under 172.16.0.0/16 (the evpn-3tier section) that overlaps nothing in Nautobot's
            plan nor an open request; the gateway is its first host address
  name      vlan_<VLAN ID>_<VRF>, e.g. vlan_41_web (underscores only)

Nothing is reserved at this point: a request holds its VLAN and subnet while it is open (they count as used for the
next allocation and in validation), and the worker writes them to Nautobot and phpIPAM when it carries the request out."""
import ipaddress

from . import labmodel
from .models import WorkflowRequest as R

SUBNET_LEN = 24
OPEN = (R.Status.SUBMITTED, R.Status.APPROVED, R.Status.RUNNING)


def claims(exclude_pk=None):
    """VLAN IDs, VNIs and subnets that open requests hold (not yet in Nautobot)."""
    vlans, vnis, nets = set(), set(), []
    for r in R.objects.filter(status__in=OPEN).exclude(pk=exclude_pk):
        p = r.parameters
        v = p.get("vlan_id") or p.get("peering_vlan")
        if v: vlans.add(int(v)); vnis.add(10000 + int(v))
        for key in ("subnet", "peering_subnet"):
            if p.get(key): nets.append((ipaddress.ip_network(p[key]), f"request #{r.pk}"))
        for x in p.get("vip_prefixes") or []: nets.append((ipaddress.ip_network(x), f"request #{r.pk}"))
        for x in p.get("members") or []:                     # a load balancer pair: each member's peering VLAN and subnet
            vlans.add(int(x["peering_vlan"])); vnis.add(int(x["vni"])); nets.append((ipaddress.ip_network(x["peering_subnet"]), f"request #{r.pk}"))
    return vlans, vnis, nets


def ipam_vlans():
    from .clients import LOCATION, Ipam
    ip = Ipam()
    dom = next(d for d in ip.req("GET", "l2domains") if d["name"] == LOCATION)
    return {int(v["number"]) for v in ip.req("GET", f"l2domains/{dom['id']}/vlans") or []}


def free_subnets(length=SUBNET_LEN):
    """phpIPAM's free subnets of this length under 172.16.0.0/16, in order."""
    from .clients import LOCATION, Ipam
    ip = Ipam()
    sec = next(s for s in ip.req("GET", "sections") if s["name"] == LOCATION)
    sup = labmodel.FABRIC_SUPERNET
    master = next(x for x in ip.req("GET", f"subnets/cidr/{sup.network_address}/{sup.prefixlen}") if x["sectionId"] == sec["id"])
    return [ipaddress.ip_network(s) for s in ip.req("GET", f"subnets/{master['id']}/all_subnets/{length}") or []]


def next_vlan(m=None):
    m = m or labmodel.model()
    cl_vlans, cl_vnis, _ = claims()
    used = set(m["vlans_used"]) | cl_vlans | ipam_vlans()
    vnis = set(m["vnis_used"]) | cl_vnis
    tier = [v["vlan"] for v in m.get("tier_vlans", [])] or [1]
    v = max(tier) + 1
    while v in used or 10000 + v in vnis:
        v += 1
        if v > 4094: raise RuntimeError("no free VLAN ID above the tier VLANs")
    return v


def next_subnet(m=None):
    m = m or labmodel.model()
    _, _, held = claims()
    taken = list(m["subnets_used"]) + [n for n, _ in held]
    for net in free_subnets():
        if not any(net.overlaps(t) for t in taken):
            return net
    raise RuntimeError(f"phpIPAM has no free /{SUBNET_LEN} under {labmodel.FABRIC_SUPERNET}")


def vlan_name(vlan_id, vrf):
    return f"vlan_{vlan_id}_{vrf}"


NAME_TEMPLATE = "vlan_{vlan_id}_{vrf}"          # the same, for the front end's live preview


def add_vlan(vrf=None):
    """What the button returns: the VLAN, its VNI, the subnet and gateway — and the name once the VRF is known."""
    m = labmodel.model()
    vid, net = next_vlan(m), next_subnet(m)
    gw = f"{net.network_address + 1}/{net.prefixlen}"
    return {"vlan_id": vid, "vni": 10000 + vid, "subnet": str(net), "gateway": gw,
            "name": vlan_name(vid, vrf) if vrf else None, "source": {"vlan": "Nautobot, phpIPAM and open requests", "subnet": "phpIPAM (first free /24)"}}
