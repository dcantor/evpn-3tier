"""Fabric dashboard: the fabric at a glance — what it is made of (from Nautobot, the source of truth the pipelines deploy
from), the KPIs its design implies, how the change process is doing (this app's requests), and, on request, how the live
network compares with the design (read-only SSH, the same fixed commands as the device-state tool).

    design()   devices by role, models and platforms, racks, links, VRFs, VLANs / VNIs, subnets, ASNs, policy;
               design KPIs: ECMP width, oversubscription, fabric capacity, spine-loss headroom, dual-homing,
               single points of failure, VLAN / address-space use                                    cached 30 s
    health()   per network device: BGP sessions, BFD, VNIs, software version, uptime; summed and compared
               with the design (expected underlay sessions = fabric links)                           cached 30 s
"""
import ipaddress
import json
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

from django.db.models import Count

from . import devices as D
from . import labmodel

SWITCH_ROLES = ("spine", "leaf", "border")
FABRIC_ORDER = ("spine", "leaf", "border", "fw", "wan", "lb", "web", "app", "db", "ipam")
ROLE_PLURAL = {"spine": "Spines", "leaf": "Leaves", "border": "Border leaves", "fw": "Firewalls", "wan": "WAN", "lb": "Load balancers",
               "web": "Web servers", "app": "App servers", "db": "Databases", "ipam": "IPAM"}
VLAN_POOL = (2, 4094)               # usable 802.1Q IDs

QUERY = """{ config_contexts(name: ["evpn-3tier"]) { data }
  devices(location: ["evpn-3tier"]) { name status { name } platform { name } role { name } rack { name } primary_ip4 { address }
    device_type { model manufacturer { name } } local_config_context_data
    interfaces { name type mgmt_only cable_peer_interface { name device { name } } } }
  vrfs(namespace: ["evpn-3tier"]) { name }
  prefixes(namespace: ["evpn-3tier"]) { prefix vrfs { name } }
  vlans(vlan_group: ["evpn-3tier"]) { vid name } }"""

_cache = {"design": (0, None), "health": (0, None)}


def _cached(key, ttl, fn, force=False):
    t, v = _cache[key]
    if v is None or force or time.time() - t > ttl:
        v = fn(); _cache[key] = (time.time(), v)
    return {**v, "age_s": int(time.time() - _cache[key][0])}


def _speed(itype):
    m = re.search(r"(\d+)GBASE", itype or "")
    return int(m.group(1)) if m else (1 if "1000BASE" in (itype or "") else 0)


def _link_kind(ra, rb):
    a, b = sorted((ra, rb))
    if ra in SWITCH_ROLES and rb in SWITCH_ROLES: return "fabric"
    if "wan" in (a, b): return "wan"
    if a == b == "fw": return "sync"
    return "access"


def _ratio(down, up):
    return round(down / up, 2) if up else None


# ---- design -----------------------------------------------------------------------------------------------------------------
def _design():
    from .clients import Nautobot
    d = Nautobot().graphql(QUERY)
    ctx = d["config_contexts"][0]["data"]; ev = ctx["evpn"]; pre = ctx.get("domain_prefix", "e3t-")
    strip = lambda n: n[len(pre):] if n.startswith(pre) else n          # noqa: E731
    m = labmodel.model()

    devs = []
    for x in d["devices"]:
        lc = x.get("local_config_context_data") or {}
        devs.append({"name": strip(x["name"]), "role": x["role"]["name"].removeprefix(pre), "rack": (x.get("rack") or {}).get("name"),
                     "model": x["device_type"]["model"], "manufacturer": x["device_type"]["manufacturer"]["name"],
                     "platform": (x.get("platform") or {}).get("name"), "status": (x.get("status") or {}).get("name"),
                     "mgmt_ip": ((x.get("primary_ip4") or {}).get("address") or "").split("/")[0], "asn": lc.get("asn"), "idx": lc.get("idx", 99),
                     "ports": sum(1 for i in x["interfaces"] if not i.get("mgmt_only") and i["type"] != "VIRTUAL" and i["type"] != "LAG")})
    devs.sort(key=lambda n: (FABRIC_ORDER.index(n["role"]) if n["role"] in FABRIC_ORDER else 99, n["idx"]))
    role_of = {n["name"]: n["role"] for n in devs}

    # links: every cabled interface pair once; per device its uplinks (to spines) and downlinks (to anything not a switch)
    links, seen = [], set()
    up, down = defaultdict(list), defaultdict(list)
    for x in d["devices"]:
        a = strip(x["name"])
        for i in x["interfaces"]:
            p = i.get("cable_peer_interface")
            if not p: continue
            b = strip(p["device"]["name"]); key = tuple(sorted([(a, i["name"]), (b, p["name"])]))
            g = _speed(i["type"])
            if role_of.get(a) in ("leaf", "border"):
                (up if role_of.get(b) == "spine" else down if role_of.get(b) not in SWITCH_ROLES else defaultdict(list))[a].append(g)
            if key in seen: continue
            seen.add(key)
            links.append({"a": a, "a_port": i["name"], "b": b, "b_port": p["name"], "gbps": g, "kind": _link_kind(role_of.get(a), role_of.get(b))})

    by_role = Counter(n["role"] for n in devs)
    spines, leaves, borders = by_role["spine"], by_role["leaf"], by_role["border"]
    edge = [n for n in devs if n["role"] in ("leaf", "border")]
    ecmp = min((len(up[n["name"]]) for n in edge), default=0)
    os_rows = [{"device": n["name"], "role": n["role"], "uplinks": len(up[n["name"]]), "uplink_gbps": sum(up[n["name"]]),
                "downlinks": len(down[n["name"]]), "downlink_gbps": sum(down[n["name"]]), "ratio": _ratio(sum(down[n["name"]]), sum(up[n["name"]]))}
               for n in edge]
    leaf_ratio = max((r["ratio"] or 0 for r in os_rows if r["role"] == "leaf"), default=None)
    border_ratio = max((r["ratio"] or 0 for r in os_rows if r["role"] == "border"), default=None)
    fabric_links = [l for l in links if l["kind"] == "fabric"]
    capacity = sum(l["gbps"] for l in fabric_links)

    # dual-homing: every endpoint (server, LB, firewall) cabled to two different switches
    peers = defaultdict(set)
    for l in links:
        for s, o in ((l["a"], l["b"]), (l["b"], l["a"])):
            if role_of.get(o) in ("leaf", "border") and role_of.get(s) not in SWITCH_ROLES: peers[s].add(o)
    endpoints = [n for n in devs if n["role"] in ("fw", "lb", "web", "app", "db")]
    dual = [n["name"] for n in endpoints if len(peers[n["name"]]) >= 2]
    spof = [{"role": r, "devices": [n["name"] for n in devs if n["role"] == r]} for r, c in by_role.items() if c == 1 and r != "ipam"]

    # models: one row per device type + platform, with the roles that run it
    models = defaultdict(lambda: {"count": 0, "roles": Counter()})
    for n in devs:
        k = (n["manufacturer"], n["model"], n["platform"]); models[k]["count"] += 1; models[k]["roles"][n["role"]] += 1
    models = [{"manufacturer": k[0], "model": k[1], "platform": k[2], "count": v["count"],
               "roles": [{"role": r, "label": ROLE_PLURAL.get(r, r), "count": c} for r, c in sorted(v["roles"].items(), key=lambda t: FABRIC_ORDER.index(t[0]) if t[0] in FABRIC_ORDER else 99)]}
              for k, v in sorted(models.items(), key=lambda t: -t[1]["count"])]

    # VRFs, VLANs, VNIs, subnets
    vlans = m["vlans"]
    peerings = ctx.get("lb_peerings", [])
    vrfs = []
    for v in ev["vrfs"]:
        vv = [x for x in vlans if x["vrf"] == v["name"]]
        hosts = [n["name"] for n in devs if n["role"] in ("web", "app", "db") and n["role"] == v["name"]]
        vrfs.append({"name": v["name"], "l3vni": v["l3vni"], "l3vlan": v["l3vlan"], "table": v["table"], "vlans": [x["vlan"] for x in vv],
                     "subnets": [x["subnet"] for x in vv], "transit": [t["subnet"] for t in ev["transit"] if t["vrf"] == v["name"]],
                     "peerings": [p["name"] for p in peerings if p["vrf"] == v["name"]], "hosts": hosts,
                     "kind": "external" if v["name"] == "outside" else "tier"})
    prefixes = [ipaddress.ip_network(p["prefix"]) for p in d["prefixes"]]
    sup = labmodel.FABRIC_SUPERNET
    in_sup = [p for p in prefixes if p.subnet_of(sup)]
    addr_used = sum(p.num_addresses for p in ipaddress.collapse_addresses(in_sup))
    subnet_kinds = [
        {"kind": "Tier VLAN subnets", "count": len(vlans), "detail": "anycast gateway on the leaves, one per VLAN"},
        {"kind": "Firewall transit", "count": len(ev["transit"]), "detail": "/28 per VRF between the borders and the firewalls"},
        {"kind": "LB peering", "count": len(peerings), "detail": "/29 per load balancer's BGP peering"},
        {"kind": "LB VIP ranges", "count": sum(len(p["vips"]) for p in peerings), "detail": "announced by the load balancers"},
        {"kind": "Infrastructure", "count": sum(1 for p in prefixes if not p.subnet_of(sup)), "detail": "loopbacks / VTEPs and out-of-band management"},
    ]
    l2vnis = len(vlans) + len(ev["transit"]) + len(peerings)
    vlan_ids = set(m["vlans_used"])
    fw_rules = ctx.get("fw_policy", [])
    asns = m["asns_used"]

    kpis = [
        {"key": "ecmp", "label": "Leaf-to-spine ECMP", "value": f"{ecmp}-way", "tone": "ok" if ecmp >= 2 else "bad",
         "detail": f"every leaf and border has {ecmp} equal-cost uplinks, one per spine"},
        {"key": "oversub", "label": "Leaf oversubscription", "value": f"{leaf_ratio}:1" if leaf_ratio else "—",
         "tone": "ok" if leaf_ratio and leaf_ratio <= 3 else "warn",
         "detail": f"server-facing vs. spine-facing bandwidth (borders {border_ratio}:1); 3:1 or less is the usual target"},
        {"key": "capacity", "label": "Fabric capacity", "value": f"{capacity} Gbps",
         "detail": f"{len(fabric_links)} spine links × {fabric_links[0]['gbps'] if fabric_links else 0} Gbps, leaf/border to spine"},
        {"key": "spine_loss", "label": "Capacity after a spine fails", "value": f"{round(100 * (spines - 1) / spines) if spines else 0}%",
         "tone": "ok" if spines >= 2 else "bad", "detail": f"{spines} spines: losing one leaves {spines - 1} path{'s' if spines - 1 != 1 else ''} per leaf"},
        {"key": "dual", "label": "Dual-homed endpoints", "value": f"{round(100 * len(dual) / len(endpoints)) if endpoints else 0}%",
         "tone": "ok" if len(dual) == len(endpoints) else "warn",
         "detail": f"{len(dual)} of {len(endpoints)} servers, load balancers and firewalls on two switches (EVPN multihoming)"},
        {"key": "spof", "label": "Single points of failure", "value": str(len(spof)), "tone": "ok" if not spof else "warn",
         "detail": ", ".join(f"{s['devices'][0]} (only {s['role']})" for s in spof) or "every role is at least a pair"},
        {"key": "vlans", "label": "VLAN IDs in use", "value": f"{len(vlan_ids)}",
         "detail": f"{round(100 * len(vlan_ids) / (VLAN_POOL[1] - VLAN_POOL[0] + 1), 1)}% of {VLAN_POOL[1] - VLAN_POOL[0] + 1} usable IDs (tier, transit, L3 and peering)"},
        {"key": "space", "label": f"{sup} allocated", "value": f"{round(100 * addr_used / sup.num_addresses, 1)}%",
         "detail": f"{addr_used:,} of {sup.num_addresses:,} addresses in Nautobot prefixes; next /24 comes from phpIPAM"},
    ]

    return {
        "lab": ctx.get("lab", "evpn-3tier"), "source": "Nautobot",
        "counts": {"devices": len(devs), "switches": spines + leaves + borders, "spines": spines, "leaves": leaves, "borders": borders,
                   "vrfs": len(ev["vrfs"]), "tier_vrfs": len(m["tier_vrfs"]), "vlans": len(vlans), "subnets": len(prefixes),
                   "l2vnis": l2vnis, "l3vnis": len(ev["vrfs"]), "asns": len(asns), "links": len(links), "fabric_links": len(fabric_links),
                   "racks": len({n["rack"] for n in devs if n["rack"]}), "fw_rules": len(fw_rules),
                   "fw_rules_standard": sum(1 for r in fw_rules if r.get("standard")), "lb_peerings": len(peerings),
                   "vips": sum(len(p["vips"]) for p in peerings) + (1 if ctx.get("web_vip") else 0)},
        "roles": [{"role": r, "label": ROLE_PLURAL.get(r, r), "count": by_role[r], "switch": r in SWITCH_ROLES,
                   "devices": [n["name"] for n in devs if n["role"] == r]} for r in FABRIC_ORDER if by_role[r]],
        "models": models, "devices": devs, "links": links, "kpis": kpis, "oversubscription": os_rows,
        "links_by_kind": [{"kind": k, "count": c, "gbps": sum(l["gbps"] for l in links if l["kind"] == k)} for k, c in Counter(l["kind"] for l in links).most_common()],
        "racks": [{"rack": r, "devices": [n["name"] for n in devs if n["rack"] == r]} for r in sorted({n["rack"] for n in devs if n["rack"]})],
        "vrfs": vrfs, "vlans": vlans, "subnet_kinds": subnet_kinds,
        "protocols": {"underlay": "eBGP, unnumbered IPv6 link-local, BFD", "overlay": "BGP EVPN, VXLAN, symmetric IRB",
                      "mtu": ctx.get("fabric_mtu"), "max_paths": ctx.get("max_paths"), "vrf_max_paths": ctx.get("vrf_max_paths"),
                      "rt_as": ev.get("rt_as"), "anycast_mac": ev.get("anycast_mac"), "asns": asns},
        "changes": _changes(),
        "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _changes():
    from .models import WorkflowRequest as R
    qs = R.objects.all()
    st = dict(qs.values_list("status").annotate(n=Count("id")))
    finished = st.get("done", 0) + st.get("failed", 0)
    durs = []
    for r in qs.filter(status="done").exclude(progress={}):
        p = r.progress or {}
        try:
            from datetime import datetime
            a, b = datetime.fromisoformat(p["started"]), datetime.fromisoformat(p["finished"])
            durs.append((b - a).total_seconds())
        except (KeyError, TypeError, ValueError): pass
    durs.sort()
    last = qs.filter(status="done").order_by("-updated").first()
    return {"total": qs.count(), "by_status": st, "done": st.get("done", 0), "failed": st.get("failed", 0),
            "open": sum(st.get(s, 0) for s in ("submitted", "approved", "running")),
            "success_rate": round(100 * st.get("done", 0) / finished) if finished else None,
            "median_s": durs[len(durs) // 2] if durs else None,
            "by_workflow": dict(qs.filter(status="done").values_list("workflow").annotate(n=Count("id"))),
            "last_change": {"id": last.pk, "title": last.title, "at": last.updated.isoformat()} if last else None}


def design(force=False):
    return _cached("design", 30, _design, force)


# ---- live health ------------------------------------------------------------------------------------------------------------
def _bgp(out):
    try: s = json.loads(out)
    except ValueError: return None
    sess = {}
    for vrf, afs in s.items():
        for af, v in (afs or {}).items():
            if not isinstance(v, dict) or "peers" not in v: continue
            for peer, x in v["peers"].items():
                k = (vrf, peer); ok = x.get("state") == "Established"
                prev = sess.get(k)
                sess[k] = {"vrf": vrf, "peer": x.get("hostname") or peer, "up": ok and (prev is None or prev["up"]), "afs": (prev or {"afs": []})["afs"] + [af]}
    return list(sess.values())


def _one(dev):
    name, role = dev["name"], dev["role"]
    t0 = time.monotonic()
    if D.kind(role) == "vyos":
        cmds = ["vtysh -c 'show bgp vrf all summary json'", "vtysh -c 'show bfd peers json'", f"{D.OP} show version", "cat /proc/uptime"]
        if role in ("leaf", "border"): cmds.append("vtysh -c 'show evpn vni json'")
        outs = D._run_many(name, cmds, timeout=60)
        sess = _bgp(outs[0]) or []
        try: bfd = json.loads(outs[1])
        except ValueError: bfd = []
        ver = re.search(r"^Version:\s*(.+)$", outs[2], re.M)
        vnis = None
        if len(outs) > 4:
            try: vnis = sorted(int(k) for k in json.loads(outs[4]))
            except ValueError: vnis = None
        return {"device": name, "role": role, "ok": True, "software": ver.group(1).strip() if ver else None,
                "uptime_s": int(float(outs[3].split()[0])) if outs[3].strip() else None,
                "bgp": {"up": sum(1 for s in sess if s["up"]), "total": len(sess), "down": [f"{s['vrf']}:{s['peer']}" for s in sess if not s["up"]],
                        "underlay_up": sum(1 for s in sess if s["up"] and s["vrf"] == "default" and "l2VpnEvpn" in s["afs"])},
                "bfd": {"up": sum(1 for b in bfd if b.get("status") == "up"), "total": len(bfd)}, "vnis": vnis,
                "ms": int(1000 * (time.monotonic() - t0))}
    outs = D._run_many(name, ["cat /etc/alpine-release", "uname -r", "cat /proc/uptime"], timeout=30)
    return {"device": name, "role": role, "ok": True, "software": f"Alpine {outs[0].strip()} (kernel {outs[1].strip()})",
            "uptime_s": int(float(outs[2].split()[0])) if outs[2].strip() else None, "ms": int(1000 * (time.monotonic() - t0))}


def _safe_one(dev):
    try: return _one(dev)
    except Exception as e:                                                    # noqa: BLE001
        return {"device": dev["name"], "role": dev["role"], "ok": False, "error": f"{type(e).__name__}: {e}"}


def _health():
    des = design()
    devs = [n for n in des["devices"] if n["role"] != "ipam"]
    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(_safe_one, devs))
    net = [r for r in rows if r.get("bgp")]
    bgp_up, bgp_total = sum(r["bgp"]["up"] for r in net), sum(r["bgp"]["total"] for r in net)
    bfd_up, bfd_total = sum(r["bfd"]["up"] for r in net), sum(r["bfd"]["total"] for r in net)
    # the spines see every fabric link's EVPN session: expected = fabric links (one session per cable)
    underlay_up = sum(r["bgp"]["underlay_up"] for r in net if r["role"] == "spine")
    expected = des["counts"]["fabric_links"]
    vni_sets = {r["device"]: set(r["vnis"]) for r in net if r.get("vnis") is not None and r["role"] == "leaf"}
    common = set.intersection(*vni_sets.values()) if vni_sets else set()
    union = set.union(*vni_sets.values()) if vni_sets else set()
    reach = sum(1 for r in rows if r["ok"])
    sw = Counter(r.get("software") for r in rows if r["ok"] and r["role"] in SWITCH_ROLES + ("fw", "wan"))
    kpis = [
        {"key": "reach", "label": "Devices reachable", "value": f"{reach}/{len(rows)}", "tone": "ok" if reach == len(rows) else "bad",
         "detail": "answered over SSH on the out-of-band network"},
        {"key": "underlay", "label": "Fabric sessions vs. design", "value": f"{underlay_up}/{expected}", "tone": "ok" if underlay_up == expected else "bad",
         "detail": "spine BGP EVPN sessions established, one expected per spine link"},
        {"key": "bgp", "label": "BGP sessions up", "value": f"{bgp_up}/{bgp_total}", "tone": "ok" if bgp_up == bgp_total else "bad",
         "detail": "every VRF on every router, firewall and the WAN (each session counted from both ends)"},
        {"key": "bfd", "label": "BFD sessions up", "value": f"{bfd_up}/{bfd_total}", "tone": "ok" if bfd_up == bfd_total else "bad",
         "detail": "sub-second failure detection on fabric and peering links"},
        {"key": "vni", "label": "VNI consistency (leaves)", "value": f"{len(common)}/{len(union)}", "tone": "ok" if common == union else "warn",
         "detail": "VNIs present on every leaf, of all VNIs seen on any leaf"},
        {"key": "sw", "label": "Network software versions", "value": str(len(sw)), "tone": "ok" if len(sw) <= 1 else "warn",
         "detail": "; ".join(f"{v} ×{c}" for v, c in sw.most_common()) or "—"},
    ]
    return {"kpis": kpis, "devices": rows, "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def health(force=False):
    return _cached("health", 30, _health, force)
