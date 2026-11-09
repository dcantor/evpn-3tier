"""The live lab, as the request forms need it: what is already used (VLAN IDs, VNIs, subnets, ASNs) and what can be
chosen (VRFs, switch pairs). Read from **Nautobot** — the source of truth the pipelines deploy from — so a VLAN added
through this app counts as used for the next request. Falls back to the lab's own model (`lab.sh inventory`) when
Nautobot does not answer. Cached for 30 seconds."""
import ipaddress
import json
import logging
import subprocess
import time

from django.conf import settings

_cache = {"t": 0, "v": None}
FABRIC_SUPERNET = ipaddress.ip_network("172.16.0.0/16")     # the data centre's tier / transit space (the lab's convention)
PRIVATE_ASN = range(64512, 65535)
log = logging.getLogger(__name__)

QUERY = """{ config_contexts(name: ["evpn-3tier"]) { data }
  devices(location: ["evpn-3tier"]) { name local_config_context_data role { name } rack { name }
    interfaces { name vrf { name } cable_peer_interface { device { name } } } } }"""


def _from_nautobot():
    from .clients import Nautobot
    d = Nautobot().graphql(QUERY)
    ctx = d["config_contexts"][0]["data"]; ev = ctx["evpn"]
    pre = ctx["domain_prefix"]
    strip = lambda n: n[len(pre):] if n.startswith(pre) else n          # noqa: E731
    nodes = []
    for x in d["devices"]:
        ifs = x.get("interfaces") or []
        nodes.append({"name": strip(x["name"]), "role": x["role"]["name"].removeprefix("e3t-"), "rack": x["rack"]["name"],
                      "idx": x["local_config_context_data"]["idx"], "asn": x["local_config_context_data"].get("asn"),
                      # a Linux VM: the switches its bond legs are cabled to, and its tier VRF (bond0.<VLAN>)
                      "peers": sorted({strip(i["cable_peer_interface"]["device"]["name"]) for i in ifs if i["name"] in ("eth1", "eth2") and i.get("cable_peer_interface")}),
                      "vrf": next((i["vrf"]["name"] for i in ifs if i["name"].startswith("bond0.") and i.get("vrf")), None)})
    return ctx, ev, nodes, "Nautobot"


def _from_lab():
    inv = json.loads(subprocess.run([str(settings.LAB_DIR / "lab.sh"), "inventory"], capture_output=True, text=True, timeout=60, check=True).stdout)
    for n in inv["nodes"]:
        n["peers"] = sorted({p["peer"] for p in n["ports"] if p.get("kind") == "access"}) if n.get("net") else []
        n["vrf"] = (n.get("net") or {}).get("vrf")
    return {**inv, "evpn": inv["evpn"]}, inv["evpn"], inv["nodes"], "lab.conf (Nautobot did not answer)"


def model():
    if _cache["v"] is not None and time.time() - _cache["t"] < 30: return _cache["v"]
    try: ctx, ev, nodes, source = _from_nautobot()
    except Exception as e:                                                    # noqa: BLE001
        log.warning("lab model from Nautobot failed (%s): using lab.conf", e); ctx, ev, nodes, source = _from_lab()
    leaves = sorted((n for n in nodes if n["role"] == "leaf"), key=lambda n: n["idx"])
    borders = sorted((n for n in nodes if n["role"] == "border"), key=lambda n: n["idx"])
    pairs = {}
    for n in leaves: pairs.setdefault(n["rack"], []).append(n["name"])
    pairs = {f"{rack} leaves": names for rack, names in sorted(pairs.items())}
    pairs["border leaves"] = [n["name"] for n in borders]
    peerings = ctx.get("lb_peerings", [])
    subnets = ([ipaddress.ip_interface(v["gateway"]).network for v in ev["vlans"]] + [ipaddress.ip_network(t["subnet"]) for t in ev["transit"]]
               + [ipaddress.ip_network(p["subnet"]) for p in peerings] + [ipaddress.ip_network(v) for p in peerings for v in p["vips"]])
    pair_name = {tuple(sorted(v)): k for k, v in pairs.items()}
    lbs = {n["name"]: {"pair": pair_name.get(tuple(sorted(n["peers"]))), "switches": n["peers"], "vrf": n["vrf"],
                       "peering": next((p for p in peerings if p["name"] == n["name"]), None)}
           for n in sorted(nodes, key=lambda n: n["idx"]) if n["role"] == "lb"}
    m = {
        "lab": ctx.get("lab", "evpn-3tier"), "source": source,
        "vrfs": [v["name"] for v in ev["vrfs"]],
        "tier_vrfs": [v["name"] for v in ev["vrfs"] if v["name"] != "outside"],
        "vlans_used": sorted({v["vlan"] for v in ev["vlans"]} | {int(t["vlan"]) for t in ev["transit"]} | {v["l3vlan"] for v in ev["vrfs"]} | {p["vlan"] for p in peerings}),
        "vnis_used": sorted({v["vni"] for v in ev["vlans"]} | {int(t["vni"]) for t in ev["transit"]} | {v["l3vni"] for v in ev["vrfs"]} | {p["vni"] for p in peerings}),
        "subnets_used": subnets,
        "asns_used": sorted({n["asn"] for n in nodes if n.get("asn")} | {ev["rt_as"]} | {p["asn"] for p in peerings}),
        "lbs": lbs, "lb_peerings": peerings,
        "switch_pairs": pairs,
        "tier_vlans": [{"vlan": v["vlan"], "vrf": v["vrf"], "gateway": v["gateway"], "on": "+".join(v["on"])} for v in ev["vlans"]],
    }
    _cache.update(t=time.time(), v=m)
    return m


def invalidate():
    _cache["v"] = None


def overlaps(net, m=None):
    m = m or model()
    return [str(s) for s in m["subnets_used"] if s.overlaps(net)]
