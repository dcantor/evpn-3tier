"""Standard Firewall Rules: every tier VLAN is open to ICMP from every other zone — outside (the WAN) and the other tiers.

They live in Nautobot's plan, in fw_policy, as entries tagged {"standard": true, "vlan": <ID>} — one per source zone:
  {"from": "outside", "to": "app", "dest": "172.16.40.0/24", "proto": "icmp", "port": null, "standard": true, "vlan": 40}
The firewalls render them as rule 1000 + VLAN ID in each zone pair (stable: a VLAN added or removed never renumbers
another rule). Add a VLAN writes its set; Remove a VLAN deletes it."""
import ipaddress

ZONES = ("outside", "web", "app", "db")
RULE_BASE = 1000


def for_vlan(vlan, vrf, subnet):
    """The standard rules of one VLAN: ICMP to its subnet from every zone but its own."""
    net = str(ipaddress.ip_network(subnet))
    return [{"from": z, "to": vrf, "dest": net, "proto": "icmp", "port": None, "standard": True, "vlan": int(vlan)} for z in ZONES if z != vrf]


def merge(policy, vlan, vrf, subnet):
    """fw_policy with this VLAN's standard rules (exactly once); returns (new policy, added count)."""
    keep = [p for p in policy if not (p.get("standard") and p.get("vlan") == int(vlan))]
    return keep + for_vlan(vlan, vrf, subnet), len(keep) + len(for_vlan(vlan, vrf, subnet)) - len(policy)


def drop(policy, vlan):
    """fw_policy without this VLAN's standard rules; returns (new policy, removed count)."""
    keep = [p for p in policy if not (p.get("standard") and p.get("vlan") == int(vlan))]
    return keep, len(policy) - len(keep)


def rule_number(vlan):
    return RULE_BASE + int(vlan)
