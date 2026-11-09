"""Read-only databases for the Tools section: the ASNs and the VLANs of the fabric, from Nautobot (the live plan the
pipelines deploy from), as tables for the front end and as Excel workbooks (openpyxl) to download.

  asn_db()    every ASN: the devices that use it (switches, firewalls, WAN, load balancers), their roles, racks and
              router IDs, which ASNs they peer with; plus every BGP session of the design (underlay + EVPN on the fabric
              links, the firewalls in every VRF, the WAN, the load balancer peerings) and the route-target AS
  vlan_db()   every VLAN in the evpn-3tier VLAN group: tier VLANs (VRF, L2VNI, subnet, gateway, span, hosts, who made
              it), firewall transit VLANs, load balancer peering VLANs and the L3VNI VLANs
Both cached for 30 seconds."""
import io
import ipaddress
import re
import time

from .clients import LOCATION, Nautobot

ROLE = {"spine": "Spine", "leaf": "Leaf", "border": "Border leaf", "fw": "Firewall", "wan": "WAN", "lb": "Load balancer"}
KIND = {"spine": "Fabric", "leaf": "Fabric", "border": "Fabric", "fw": "Firewall", "wan": "WAN", "lb": "Load balancer"}
_cache = {}


def _cached(key, fn):
    t, v = _cache.get(key, (0, None))
    if v is None or time.time() - t > 30:
        v = fn(); _cache[key] = (time.time(), v)
    return {**v, "age_s": int(time.time() - _cache[key][0])}


def _model():
    from .executors import _lab
    return _lab()[0]


def _stamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---- ASNs ---------------------------------------------------------------------------------------------------------------------
def _asns():
    m = _model(); N = {n["name"]: n for n in m["nodes"]}
    sessions = []

    def s(a, b, vrf, kind, a_addr, b_addr, bfd, note=""):
        sessions.append({"a": a, "a_asn": N[a].get("asn") if a in N else None, "a_addr": a_addr, "b": b, "b_asn": None, "b_addr": b_addr,
                         "vrf": vrf, "kind": kind, "bfd": bfd, "note": note})

    # fabric: eBGP unnumbered over every leaf / border <-> spine link (IPv4 + L2VPN EVPN), BFD
    for n in m["nodes"]:
        if n["role"] in ("leaf", "border"):
            for p in (p for p in n["ports"] if p.get("kind") == "fabric"):
                s(p["peer"], n["name"], "default", "Underlay + EVPN", f"{p['peer_port']} (IPv6 link-local)", f"{p['name']} (IPv6 link-local)", True)
    # borders <-> firewalls: one session per tier / outside VRF on the transit VLANs
    for t in m["evpn"]["transit"]:
        for b, bip in t["borders"].items():
            for f, fip in t["fws"].items():
                s(b, f, t["vrf"], "Firewall transit", bip.split("/")[0], fip.split("/")[0], True, f"VLAN {t['vlan']} {t['subnet']}")
    # borders <-> WAN, in VRF outside
    for w in m["wan"]["links"]:
        s(w["border"], w["wan"], "outside", "WAN", w["border_ip"].split("/")[0], w["wan_ip"].split("/")[0], True, f"{w['border_port']} - {w['wan_port']}")
    # load balancer peerings (self-service)
    lb_asn = {}
    for pr in m.get("lb_peerings", []):
        lb_asn[pr["name"]] = pr["asn"]
        for sw, ip in pr["switch_ips"].items():
            s(sw, pr["name"], pr["vrf"], "Load balancer peering", ip.split("/")[0], pr["lb_ip"].split("/")[0], bool(pr.get("bfd")),
              f"VLAN {pr['vlan']}, VIPs {', '.join(pr['vips'])}")
    asn_of = lambda d: N[d].get("asn") or lb_asn.get(d)                     # noqa: E731
    for x in sessions:
        x["a_asn"], x["b_asn"] = asn_of(x["a"]), asn_of(x["b"])

    by = {}
    for n in m["nodes"]:
        a = n.get("asn") or lb_asn.get(n["name"])
        if a: by.setdefault(a, []).append(n)
    rows = []
    for a, devs in sorted(by.items()):
        names = [d["name"] for d in devs]
        mine = [x for x in sessions if x["a"] in names or x["b"] in names]
        peers = sorted({(x["b_asn"] if x["a"] in names else x["a_asn"]) for x in mine} - {a})
        roles = sorted({d["role"] for d in devs}, key=list(ROLE).index)
        rows.append({"asn": a, "kind": KIND[roles[0]], "private": 64512 <= a <= 65534, "devices": names,
                     "roles": [ROLE[r] for r in roles], "racks": sorted({d["rack"] for d in devs}),
                     "router_ids": [d["loopback"] for d in devs if d.get("loopback")],
                     "peers": peers, "peer_devices": sorted({(x["b"] if x["a"] in names else x["a"]) for x in mine}),
                     "sessions": len(mine), "vrfs": sorted({x["vrf"] for x in mine}),
                     "source": "Nautobot device (local config context)" if roles[0] != "lb" else "Nautobot plan: lb_peerings (self-service)"})
    rt = m["evpn"]["rt_as"]
    rows.append({"asn": rt, "kind": "Route target", "private": 64512 <= rt <= 65534, "devices": [], "roles": [], "racks": [], "router_ids": [],
                 "peers": [], "peer_devices": [], "sessions": 0, "vrfs": [v["name"] for v in m["evpn"]["vrfs"]],
                 "source": "Nautobot plan: evpn.rt_as — the AS part of every EVPN route target (<AS>:<VNI>), not a BGP speaker"})
    used = sorted(r["asn"] for r in rows)
    return {"rows": rows, "sessions": sessions, "fetched": _stamp(), "source": "Nautobot",
            "summary": {"asns": len(rows), "speakers": sum(1 for r in rows if r["devices"]), "sessions": len(sessions),
                        "private_used": sum(1 for a in used if 64512 <= a <= 65534), "private_pool": 65534 - 64512 + 1}}


def asn_db():
    return _cached("asn", _asns)


# ---- VLANs --------------------------------------------------------------------------------------------------------------------
QUERY = """{ vlans(vlan_group: ["%s"]) { vid name description status { name } tenant { name } } }""" % LOCATION
REQ = re.compile(r"request #(\d+)")


def _vlans():
    m = _model(); objs = {v["vid"]: v for v in Nautobot().graphql(QUERY)["vlans"]}
    hosts = {}
    for n in m["nodes"]:
        for v in (n.get("vlans") or ([n["net"]["vlan"]] if n.get("net") else [])):
            hosts.setdefault(v, []).append(n["name"])
    rows = []

    def row(vid, typ, vrf, vni, subnet=None, gateway=None, where="", members=(), extra=""):
        o = objs.get(vid) or {}
        req = REQ.search(o.get("description") or "")
        net = ipaddress.ip_network(subnet) if subnet else None
        rows.append({"vlan": vid, "name": o.get("name"), "type": typ, "vrf": vrf, "vni": vni, "subnet": subnet, "gateway": gateway,
                     "size": net.num_addresses - 2 if net and net.prefixlen < 31 else None, "where": where, "hosts": sorted(members),
                     "tenant": (o.get("tenant") or {}).get("name"), "status": (o.get("status") or {}).get("name"),
                     "origin": f"request #{req.group(1)}" if req else ("lab design" if o else "plan only"), "request": int(req.group(1)) if req else None,
                     "description": o.get("description") or extra, "in_nautobot": bool(o)})

    for v in m["evpn"]["vlans"]:
        gw = v["gateway"]
        row(v["vlan"], "Tier", v["vrf"], v["vni"], str(ipaddress.ip_interface(gw).network), gw.split("/")[0],
            " + ".join({"leaves": "every leaf", "borders": "both border leaves"}[x] for x in v["on"]), hosts.get(v["vlan"], []))
    for t in m["evpn"]["transit"]:
        row(t["vlan"], "Firewall transit", t["vrf"], t["vni"], t["subnet"], None, "border leaves + firewalls",
            list(t["borders"]) + list(t["fws"]))
    for pr in m.get("lb_peerings", []):
        row(pr["vlan"], "LB peering", pr["vrf"], pr["vni"], pr["subnet"], None, f"{', '.join(pr['switches'])} + {pr['name']}",
            list(pr["switches"]) + [pr["name"]])
    for v in m["evpn"]["vrfs"]:
        row(v["l3vlan"], "L3VNI", v["name"], v["l3vni"], None, None, "every switch with the VRF (symmetric IRB)", [])
    known = {r["vlan"] for r in rows}
    for vid, o in objs.items():                                              # in the VLAN group but not in the plan
        if vid not in known: row(vid, "Not in the plan", None, None, None, None, "", [], "")
    rows.sort(key=lambda r: r["vlan"])
    from collections import Counter
    c = Counter(r["type"] for r in rows)
    return {"rows": rows, "fetched": _stamp(), "source": "Nautobot",
            "summary": {"vlans": len(rows), "by_type": dict(c), "vrfs": len(m["evpn"]["vrfs"]),
                        "subnets": sum(1 for r in rows if r["subnet"]), "self_service": sum(1 for r in rows if r["request"])}}


def vlan_db():
    return _cached("vlan", _vlans)


# ---- Excel ------------------------------------------------------------------------------------------------------------------
def _sheet(wb, title, head, rows, widths, first=False):
    from openpyxl.styles import Alignment, Font, PatternFill
    ws = wb.active if first else wb.create_sheet()
    ws.title = title
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="4F46E5")
        c.alignment = Alignment(vertical="center")
    for r in rows: ws.append(r)
    for i, w in enumerate(widths, 1): ws.column_dimensions[ws.cell(1, i).column_letter].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row: c.alignment = Alignment(vertical="top", wrap_text=True)
    ws.freeze_panes = "A2"
    if rows: ws.auto_filter.ref = ws.dimensions
    return ws


def _about(wb, what, d, lines):
    ws = wb.create_sheet("About")
    for r in [[what], [], ["Source", f"Nautobot (location / namespace / VLAN group {LOCATION}) — the plan the pipelines deploy from"],
              ["Generated", d["fetched"]], ["By", "Fabric Factory (evpn-3tier)"], []] + [[k, v] for k, v in lines]:
        ws.append(r)
    from openpyxl.styles import Font
    ws["A1"].font = Font(bold=True, size=14); ws.column_dimensions["A"].width = 22; ws.column_dimensions["B"].width = 90


def _save(wb):
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


J = lambda xs: ", ".join(str(x) for x in xs)                                  # noqa: E731


def asn_xlsx():
    from openpyxl import Workbook
    d = asn_db(); wb = Workbook()
    _sheet(wb, "ASNs", ["ASN", "Kind", "Private", "Devices", "Roles", "Racks", "Router IDs", "Peers with (ASN)", "Peer devices", "BGP sessions", "VRFs", "Source"],
           [[r["asn"], r["kind"], "yes" if r["private"] else "no", J(r["devices"]), J(r["roles"]), J(r["racks"]), J(r["router_ids"]), J(r["peers"]),
             J(r["peer_devices"]), r["sessions"], J(r["vrfs"]), r["source"]] for r in d["rows"]],
           [9, 15, 8, 22, 18, 14, 22, 22, 30, 12, 26, 60], first=True)
    _sheet(wb, "BGP sessions", ["Device A", "ASN A", "Address A", "Device B", "ASN B", "Address B", "VRF", "Kind", "BFD", "Note"],
           [[x["a"], x["a_asn"], x["a_addr"], x["b"], x["b_asn"], x["b_addr"], x["vrf"], x["kind"], "yes" if x["bfd"] else "no", x["note"]] for x in d["sessions"]],
           [12, 9, 24, 12, 9, 24, 10, 22, 6, 40])
    s = d["summary"]
    _about(wb, "evpn-3tier — ASN database", d, [("ASNs", s["asns"]), ("BGP speakers' ASNs", s["speakers"]), ("BGP sessions (design)", s["sessions"]),
                                                ("Private ASNs used", f"{s['private_used']} of {s['private_pool']} (64512-65534)")])
    return _save(wb)


def vlan_xlsx():
    from openpyxl import Workbook
    d = vlan_db(); wb = Workbook()
    _sheet(wb, "VLANs", ["VLAN ID", "Name", "Type", "VRF", "VNI", "Subnet", "Gateway", "Usable addresses", "Where", "Hosts / members", "Tenant", "Status", "Origin", "Description"],
           [[r["vlan"], r["name"], r["type"], r["vrf"], r["vni"], r["subnet"], r["gateway"], r["size"], r["where"], J(r["hosts"]), r["tenant"], r["status"],
             r["origin"], r["description"]] for r in d["rows"]],
           [9, 16, 16, 9, 9, 18, 14, 10, 30, 30, 12, 9, 14, 70], first=True)
    s = d["summary"]
    _about(wb, "evpn-3tier — VLAN database", d, [("VLANs", s["vlans"])] + [(f"  {k}", v) for k, v in s["by_type"].items()]
           + [("Subnets", s["subnets"]), ("Made by self-service requests", s["self_service"])])
    return _save(wb)
