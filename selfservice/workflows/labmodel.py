"""The live lab, as the request forms need it: what is already used (VLAN IDs, VNIs, subnets, ASNs) and what can be
chosen (VRFs, switch pairs). Read from the lab itself (`lab.sh inventory`, i.e. lab.conf), cached for a minute.
When the workflows start executing, this is the place to read Nautobot instead."""
import ipaddress
import json
import subprocess
import time

from django.conf import settings

_cache = {"t": 0, "v": None}
FABRIC_SUPERNET = ipaddress.ip_network("172.16.0.0/16")     # the data centre's tier / transit space (the lab's convention)
PRIVATE_ASN = range(64512, 65535)                           # 2-byte private ASNs


def inventory():
    if _cache["v"] is None or time.time() - _cache["t"] > 60:
        out = subprocess.run([str(settings.LAB_DIR / "lab.sh"), "inventory"], capture_output=True, text=True, timeout=60, check=True).stdout
        _cache.update(t=time.time(), v=json.loads(out))
    return _cache["v"]


def model():
    """A compact view of the lab for forms and pages."""
    inv = inventory(); ev = inv["evpn"]
    nodes = inv["nodes"]
    leaves = sorted((n for n in nodes if n["role"] == "leaf"), key=lambda n: n["idx"])
    borders = sorted((n for n in nodes if n["role"] == "border"), key=lambda n: n["idx"])
    pairs = {}
    for n in leaves: pairs.setdefault(n["rack"], []).append(n["name"])
    pairs = {f"{rack} leaves": names for rack, names in sorted(pairs.items())}
    pairs["border leaves"] = [n["name"] for n in borders]
    subnets = [ipaddress.ip_interface(v["gateway"]).network for v in ev["vlans"]] + [ipaddress.ip_network(t["subnet"]) for t in ev["transit"]]
    return {
        "lab": inv["lab"],
        "vrfs": [v["name"] for v in ev["vrfs"]],
        "tier_vrfs": [v["name"] for v in ev["vrfs"] if v["name"] != "outside"],
        "vlans_used": sorted({v["vlan"] for v in ev["vlans"]} | {t["vlan"] for t in ev["transit"]} | {v["l3vlan"] for v in ev["vrfs"]}),
        "vnis_used": sorted({v["vni"] for v in ev["vlans"]} | {t["vni"] for t in ev["transit"]} | {v["l3vni"] for v in ev["vrfs"]}),
        "subnets_used": subnets,
        "asns_used": sorted({n["asn"] for n in nodes if n.get("asn")} | {ev["rt_as"]}),
        "switch_pairs": pairs,
        "tier_vlans": [{"vlan": v["vlan"], "vrf": v["vrf"], "gateway": v["gateway"], "on": "+".join(v["on"])} for v in ev["vlans"]],
    }


def overlaps(net, m=None):
    """The existing subnets `net` overlaps."""
    m = m or model()
    return [str(s) for s in m["subnets_used"] if s.overlaps(net)]
