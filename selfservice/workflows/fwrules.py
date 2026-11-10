"""Standard Firewall Rules: every tier VLAN is open to ICMP from every other zone — outside (the WAN) and the other tiers.

They live in Nautobot's plan, in fw_policy, as entries tagged {"standard": true, "vlan": <ID>} — one per source zone:
  {"from": "outside", "to": "app", "dest": "172.16.40.0/24", "proto": "icmp", "port": null, "standard": true, "vlan": 40}
The firewalls render them as rule 1000 + VLAN ID in each zone pair (stable: a VLAN added or removed never renumbers
another rule). Add a VLAN writes its set; Remove a VLAN deletes it."""
import ipaddress

ZONES = ("outside", "web", "app", "db")            # lab.conf's zones; the live list is the plan's transit VRFs (zones())


def zones(data=None):
    """The zones: every VRF with a firewall transit — lab.conf's four plus any VRF added (Add a VRF)."""
    if data is None:
        from .clients import Nautobot
        data = Nautobot().context()["data"]
    return [t["vrf"] for t in data["evpn"]["transit"]] or list(ZONES)
RULE_BASE = 1000


def for_vlan(vlan, vrf, subnet, zs=ZONES):
    """The standard rules of one VLAN: ICMP to its subnet from every zone but its own."""
    net = str(ipaddress.ip_network(subnet))
    return [{"from": z, "to": vrf, "dest": net, "proto": "icmp", "port": None, "standard": True, "vlan": int(vlan)} for z in zs if z != vrf]


def merge(policy, vlan, vrf, subnet, zs=ZONES):
    """fw_policy with this VLAN's standard rules (exactly once); returns (new policy, added count)."""
    keep = [p for p in policy if not (p.get("standard") and p.get("vlan") == int(vlan))]
    return keep + for_vlan(vlan, vrf, subnet, zs), len(keep) + len(for_vlan(vlan, vrf, subnet, zs)) - len(policy)


def add_zone(policy, zone, vlans):
    """A zone added: ICMP from it to every other VRF's VLAN (vlans: the plan's evpn.vlans). Returns (policy, added)."""
    new = [{"from": zone, "to": v["vrf"], "dest": str(ipaddress.ip_interface(v["gateway"]).network), "proto": "icmp", "port": None,
            "standard": True, "vlan": int(v["vlan"])} for v in vlans if v["vrf"] != zone]
    new = [r for r in new if r not in policy]
    return policy + new, len(new)


def drop_zone(policy, zone):
    """A zone removed: every standard rule from or to it. Returns (policy, removed)."""
    keep = [p for p in policy if not (p.get("standard") and zone in (p.get("from"), p.get("to")))]
    return keep, len(policy) - len(keep)


def drop(policy, vlan):
    """fw_policy without this VLAN's standard rules; returns (new policy, removed count)."""
    keep = [p for p in policy if not (p.get("standard") and p.get("vlan") == int(vlan))]
    return keep, len(policy) - len(keep)


def rule_number(vlan):
    return RULE_BASE + int(vlan)
