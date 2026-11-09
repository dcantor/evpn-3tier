"""Reconciliation: do phpIPAM (addressing) and Nautobot (the plan) still agree? Read-only — it reports, it repairs nothing.

The workflows write both systems in a fixed order, but nothing else keeps them in step: an edit by hand in either one, or a
request that failed between its phpIPAM and its Nautobot stage, leaves them apart. This compares what each should hold:

  vrfs       the same VRF names on both sides
  vlans      the same VLAN IDs in phpIPAM's L2 domain and Nautobot's VLAN group; the same names (a name difference is a warning)
  prefixes   every Nautobot prefix is a phpIPAM subnet and vice versa, with the same VRF and the same VLAN; phpIPAM's
             containers (subnets that hold others: allocation pools) are not expected in Nautobot
  probes     phpIPAM's per-VRF probe subnets match the plan's VRF probe bases (Nautobot keeps them in the config context)
  gateways   every VLAN of the plan has its gateway reserved in phpIPAM, flagged as the gateway; every phpIPAM gateway in
             a tier subnet is a plan gateway
  addresses  every address Nautobot assigns is reserved in phpIPAM (phpIPAM holds more — gateways, SVIs — by design)
  plan       Nautobot against itself: every VLAN, transit network and peering of the config context has its VLAN and
             prefix objects, and every VLAN object belongs to the plan

A difference that belongs to a request still in progress (its number is in the object's description) is reported as
"in flight", not as drift: phpIPAM is written before Nautobot when adding and released after it when removing.
Cached for 30 seconds."""
import ipaddress
import re
import time

from .clients import LOCATION, Ipam, Nautobot

QUERY = """{ config_contexts(name: ["%(l)s"]) { data }
  prefixes(namespace: ["%(l)s"]) { prefix description vrfs { name } vlan { vid } }
  vlans(vlan_group: ["%(l)s"]) { vid name description }
  vrfs(namespace: ["%(l)s"]) { name }
  ip_addresses(namespace: ["%(l)s"]) { address description } }""" % {"l": LOCATION}

CHECKS = [("vrfs", "VRFs"), ("vlans", "VLANs"), ("prefixes", "Subnets / prefixes"), ("probes", "Probe subnets"),
          ("gateways", "Gateways"), ("addresses", "Assigned addresses"), ("plan", "Nautobot plan ↔ objects")]
REQ = re.compile(r"request #(\d+)")
_cache = {"t": 0, "v": None}


def _open_requests():
    from .models import WorkflowRequest as R
    return set(R.objects.filter(status__in=(R.Status.SUBMITTED, R.Status.APPROVED, R.Status.RUNNING)).values_list("pk", flat=True))


def _phpipam():
    ip = Ipam()
    sec = next(s for s in ip.req("GET", "sections") if s["name"] == LOCATION)
    vrfs = {v["vrfId"]: v["name"] for v in ip.req("GET", "vrf") or []}
    dom = next(d for d in ip.req("GET", "l2domains") if d["name"] == LOCATION)
    vlans = ip.req("GET", f"l2domains/{dom['id']}/vlans") or []
    vid = {v["vlanId"]: int(v["number"]) for v in vlans}
    subs = ip.req("GET", f"sections/{sec['id']}/subnets") or []
    masters = {s.get("masterSubnetId") for s in subs}
    subnets = {}
    for s in subs:
        net = ipaddress.ip_network(f"{s['subnet']}/{s['mask']}")
        subnets[net] = {"id": s["id"], "vrf": vrfs.get(s.get("vrfId")), "vlan": vid.get(s.get("vlanId")), "description": s.get("description") or "",
                        "container": s["id"] in masters}
    addrs = {}
    for net, s in subnets.items():
        if s["container"]: continue
        for a in ip.req("GET", f"subnets/{s['id']}/addresses") or []:
            addrs[ipaddress.ip_address(a["ip"])] = {"subnet": net, "gateway": str(a.get("is_gateway")) == "1", "description": a.get("description") or "",
                                                    "hostname": a.get("hostname") or ""}
    return {"vrfs": set(vrfs.values()), "vlans": {int(v["number"]): {"name": v["name"], "description": v.get("description") or ""} for v in vlans},
            "subnets": subnets, "addresses": addrs}


def _nautobot():
    d = Nautobot().graphql(QUERY)
    return {"ctx": d["config_contexts"][0]["data"],
            "vrfs": {v["name"] for v in d["vrfs"]},
            "vlans": {v["vid"]: {"name": v["name"], "description": v.get("description") or ""} for v in d["vlans"]},
            "prefixes": {ipaddress.ip_network(p["prefix"]): {"vrf": (p["vrfs"] or [{}])[0].get("name"), "vlan": (p.get("vlan") or {}).get("vid"),
                                                             "description": p.get("description") or ""} for p in d["prefixes"]},
            "addresses": {ipaddress.ip_interface(a["address"]): a.get("description") or "" for a in d["ip_addresses"]}}


class Report:
    def __init__(self, open_reqs):
        self.open = open_reqs
        self.checks = {k: {"key": k, "label": l, "matched": 0, "issues": []} for k, l in CHECKS}
        self.ignored = []

    def ok(self, check, n=1):
        self.checks[check]["matched"] += n

    def issue(self, check, obj, kind, detail, phpipam=None, nautobot=None, severity="error", texts=()):
        reqs = sorted({int(m) for t in texts for m in REQ.findall(t or "")})
        flight = [r for r in reqs if r in self.open]
        if flight: severity = "in_flight"
        self.checks[check]["issues"].append({"object": obj, "kind": kind, "detail": detail, "phpipam": phpipam, "nautobot": nautobot,
                                              "severity": severity, "requests": reqs, "in_flight": flight})


def _s(v):
    return "—" if v is None else str(v)


def _build():
    t0 = time.monotonic()
    P, N = _phpipam(), _nautobot()
    R = Report(_open_requests())
    ev = N["ctx"]["evpn"]

    # VRFs
    for v in sorted(P["vrfs"] | N["vrfs"]):
        if v in P["vrfs"] and v in N["vrfs"]: R.ok("vrfs")
        else: R.issue("vrfs", f"VRF {v}", "only_phpipam" if v in P["vrfs"] else "only_nautobot", "missing on the other side")

    # VLANs
    for vid in sorted(set(P["vlans"]) | set(N["vlans"])):
        p, n = P["vlans"].get(vid), N["vlans"].get(vid)
        texts = [(p or {}).get("description"), (n or {}).get("description")]
        if p is None: R.issue("vlans", f"VLAN {vid}", "only_nautobot", f"in Nautobot ({n['name']}), not in phpIPAM", nautobot=n["name"], texts=texts)
        elif n is None: R.issue("vlans", f"VLAN {vid}", "only_phpipam", f"in phpIPAM ({p['name']}), not in Nautobot", phpipam=p["name"], texts=texts)
        elif p["name"] != n["name"]: R.issue("vlans", f"VLAN {vid}", "mismatch", "names differ", phpipam=p["name"], nautobot=n["name"], severity="warning", texts=texts)
        else: R.ok("vlans")

    # prefixes (phpIPAM containers and probe subnets aside)
    probe_nets = {ipaddress.ip_network(f"{v['probe']}.0/24"): v["name"] for v in ev["vrfs"] if v.get("probe")}
    for net in sorted(set(P["subnets"]) | set(N["prefixes"]), key=lambda n: (int(n.network_address), n.prefixlen)):
        p, n = P["subnets"].get(net), N["prefixes"].get(net)
        if p and p["container"] and n is None:
            R.ignored.append({"object": str(net), "why": "a phpIPAM container (allocation pool); Nautobot holds its children"}); continue
        if net in probe_nets and n is None: continue                                    # checked under probes
        texts = [(p or {}).get("description"), (n or {}).get("description")]
        if p is None: R.issue("prefixes", str(net), "only_nautobot", "a Nautobot prefix with no phpIPAM subnet", nautobot=f"VRF {_s(n['vrf'])}, VLAN {_s(n['vlan'])}", texts=texts); continue
        if n is None: R.issue("prefixes", str(net), "only_phpipam", "a phpIPAM subnet with no Nautobot prefix", phpipam=f"VRF {_s(p['vrf'])}, VLAN {_s(p['vlan'])}", texts=texts); continue
        bad = False
        if p["vrf"] != n["vrf"]: R.issue("prefixes", str(net), "mismatch", "VRF differs", phpipam=_s(p["vrf"]), nautobot=_s(n["vrf"]), texts=texts); bad = True
        if p["vlan"] != n["vlan"]: R.issue("prefixes", str(net), "mismatch", "VLAN differs", phpipam=_s(p["vlan"]), nautobot=_s(n["vlan"]), texts=texts); bad = True
        if not bad: R.ok("prefixes")

    # probe subnets: phpIPAM's against the plan's VRF probe bases
    for net, vrf in sorted(probe_nets.items()):
        p = P["subnets"].get(net)
        if p is None: R.issue("probes", str(net), "only_nautobot", f"the plan's probe base for VRF {vrf} has no phpIPAM subnet", nautobot=f"VRF {vrf}")
        elif p["vrf"] != vrf: R.issue("probes", str(net), "mismatch", "VRF differs", phpipam=_s(p["vrf"]), nautobot=vrf)
        else: R.ok("probes")
    for net, p in P["subnets"].items():
        if net.subnet_of(ipaddress.ip_network("10.255.0.0/16")) and not p["container"] and net not in probe_nets:
            R.issue("probes", str(net), "only_phpipam", "a probe subnet the plan does not have", phpipam=f"VRF {_s(p['vrf'])}")

    # gateways: the plan's VLAN gateways reserved in phpIPAM and flagged as such
    plan_gw = {ipaddress.ip_interface(v["gateway"]).ip: v for v in ev["vlans"]}
    for ip_, v in sorted(plan_gw.items()):
        a = P["addresses"].get(ip_)
        obj = f"VLAN {v['vlan']} gateway {ip_}"
        texts = [P["subnets"].get(ipaddress.ip_interface(v["gateway"]).network, {}).get("description")]
        if a is None: R.issue("gateways", obj, "only_nautobot", "the plan's gateway is not reserved in phpIPAM", nautobot=v["gateway"], texts=texts)
        elif not a["gateway"]: R.issue("gateways", obj, "mismatch", "reserved in phpIPAM but not flagged as the gateway", phpipam="address", nautobot="gateway", severity="warning", texts=texts)
        else: R.ok("gateways")
    tier_nets = {ipaddress.ip_interface(v["gateway"]).network for v in ev["vlans"]}
    for ip_, a in sorted(P["addresses"].items()):
        if a["gateway"] and ip_ not in plan_gw and a["subnet"] in tier_nets:
            R.issue("gateways", f"{ip_} ({a['hostname'] or a['description']})", "only_phpipam", "a phpIPAM gateway the plan does not have", phpipam=str(ip_), texts=[a["description"]])

    # addresses Nautobot assigns must be reserved in phpIPAM
    for itf, desc in sorted(N["addresses"].items(), key=lambda t: int(t[0].ip)):
        if itf.ip in P["addresses"]: R.ok("addresses")
        else: R.issue("addresses", str(itf), "only_nautobot", "assigned in Nautobot, not reserved in phpIPAM", nautobot=desc or "assigned", texts=[desc])

    # the plan against Nautobot's own objects
    want = {}
    for v in ev["vlans"]: want[v["vlan"]] = (ipaddress.ip_interface(v["gateway"]).network, f"tier VLAN (VRF {v['vrf']})")
    for t in ev["transit"]: want[int(t["vlan"])] = (ipaddress.ip_network(t["subnet"]), f"firewall transit (VRF {t['vrf']})")
    for p in N["ctx"].get("lb_peerings", []): want[p["vlan"]] = (ipaddress.ip_network(p["subnet"]), f"peering {p['name']}")
    l3 = {v["l3vlan"] for v in ev["vrfs"]}
    for vid, (net, what) in sorted(want.items()):
        miss = [x for x, ok in (("VLAN object", vid in N["vlans"]), (f"prefix {net}", net in N["prefixes"])) if not ok]
        if miss: R.issue("plan", f"VLAN {vid}", "mismatch", f"{what} in the plan, but Nautobot lacks its {' and '.join(miss)}", texts=[(N["vlans"].get(vid) or {}).get("description")])
        else: R.ok("plan")
    for vid in sorted(set(N["vlans"]) - set(want) - l3):
        R.issue("plan", f"VLAN {vid}", "only_nautobot", f"a VLAN object ({N['vlans'][vid]['name']}) that is not in the plan", severity="warning", texts=[N["vlans"][vid]["description"]])

    checks = list(R.checks.values())
    for c in checks:
        c["errors"] = sum(1 for i in c["issues"] if i["severity"] == "error")
        c["warnings"] = sum(1 for i in c["issues"] if i["severity"] == "warning")
        c["in_flight"] = sum(1 for i in c["issues"] if i["severity"] == "in_flight")
        c["total"] = c["matched"] + len(c["issues"])
    tot = lambda k: sum(c[k] for c in checks)                                      # noqa: E731
    return {"checks": checks, "ignored": R.ignored,
            "summary": {"matched": tot("matched"), "total": tot("total"), "errors": tot("errors"), "warnings": tot("warnings"), "in_flight": tot("in_flight"),
                        "in_sync": tot("errors") == 0},
            "counts": {"phpipam_subnets": len(P["subnets"]), "phpipam_vlans": len(P["vlans"]), "phpipam_addresses": len(P["addresses"]),
                       "nautobot_prefixes": len(N["prefixes"]), "nautobot_vlans": len(N["vlans"]), "nautobot_addresses": len(N["addresses"])},
            "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "ms": int(1000 * (time.monotonic() - t0))}


def report(force=False):
    if _cache["v"] is None or force or time.time() - _cache["t"] > 30:
        _cache.update(t=time.time(), v=_build())
    return {**_cache["v"], "age_s": int(time.time() - _cache["t"])}
