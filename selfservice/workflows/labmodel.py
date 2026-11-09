"""The live lab, as the request forms need it: what is already used (VLAN IDs, VNIs, subnets, ASNs) and what can be
chosen (VRFs, switch pairs). Read from **Nautobot** — the source of truth the pipelines deploy from — so a VLAN added
through this app counts as used for the next request. Falls back to the lab's own model (`lab.sh inventory`) when
Nautobot does not answer. Cached for 30 seconds."""
import ipaddress
import json
import logging
import re
import subprocess
import time

from django.conf import settings

_cache = {"t": 0, "v": None}
FABRIC_SUPERNET = ipaddress.ip_network("172.16.0.0/16")     # the data centre's tier / transit space (the lab's convention)
PRIVATE_ASN = range(64512, 65535)
log = logging.getLogger(__name__)

QUERY = """{ config_contexts(name: ["evpn-3tier"]) { data }
  devices(location: ["evpn-3tier"]) { name local_config_context_data role { name } rack { name }
    interfaces { name vrf { name } cable_peer_interface { device { name } } } }
  vlans(vlan_group: ["evpn-3tier"]) { vid name } }"""


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
                      "vrf": next((i["vrf"]["name"] for i in ifs if i["name"].startswith("bond0.") and i.get("vrf")), None),
                      "vlans": sorted(int(i["name"].split(".")[1]) for i in ifs if re.match(r"^bond0\.\d+$", i["name"]))})
    ctx = {**ctx, "_vlan_names": {v["vid"]: v["name"] for v in d.get("vlans") or []}}
    return ctx, ev, nodes, "Nautobot"


def _from_lab():
    inv = json.loads(subprocess.run([str(settings.LAB_DIR / "lab.sh"), "inventory"], capture_output=True, text=True, timeout=60, check=True).stdout)
    for n in inv["nodes"]:
        n["peers"] = sorted({p["peer"] for p in n["ports"] if p.get("kind") == "access"}) if n.get("net") else []
        n["vrf"] = (n.get("net") or {}).get("vrf")
        n["vlans"] = [n["net"]["vlan"]] if n.get("net") else []
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
    # removable VLANs: the plan's tier VLANs, unless the lab's design (lab.conf) has them, hosts sit in them, or they are
    # the last VLAN of their VRF (its L3 config on the switches would go with it)
    base = _base_vlans()
    names = ctx.get("_vlan_names", {})
    per_vrf = {}
    for v in ev["vlans"]: per_vrf[v["vrf"]] = per_vrf.get(v["vrf"], 0) + 1
    vlans = []
    for v in sorted(ev["vlans"], key=lambda v: v["vlan"]):
        hosts = sorted(n["name"] for n in nodes if v["vlan"] in (n.get("vlans") or []))
        why = ("part of the lab's design (lab.conf)" if v["vlan"] in base else f"hosts attached: {', '.join(hosts)}" if hosts
               else f"the last VLAN of VRF {v['vrf']}" if per_vrf[v["vrf"]] == 1 else None)
        vlans.append({"vlan": v["vlan"], "name": names.get(v["vlan"]), "vrf": v["vrf"], "vni": v["vni"], "gateway": v["gateway"],
                      "subnet": str(ipaddress.ip_interface(v["gateway"]).network), "on": v["on"], "blocked": why})
    m = {
        "lab": ctx.get("lab", "evpn-3tier"), "source": source, "vlans": vlans,
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


def _base_vlans():
    """The VLANs of the lab's own design (lab.conf) — never removed by a request."""
    try:
        inv = json.loads(subprocess.run([str(settings.LAB_DIR / "lab.sh"), "inventory"], capture_output=True, text=True, timeout=60, check=True).stdout)
        return {v["vlan"] for v in inv["evpn"]["vlans"]}
    except Exception as e:                                                    # noqa: BLE001 — unknown: protect everything
        log.warning("lab.conf VLANs unknown (%s): no VLAN is removable", e); return {0} | set(range(1, 4095))


def invalidate():
    _cache["v"] = None


def overlaps(net, m=None):
    m = m or model()
    return [str(s) for s in m["subnets_used"] if s.overlaps(net)]
