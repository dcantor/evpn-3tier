#!/usr/bin/env python3
"""Render the Ansible inventory of evpn-3tier from Nautobot (the source of truth).

One GraphQL query (devices of location evpn-3tier with their roles, racks, platforms, primary IPs, interfaces, MACs,
cables and addresses, plus the config context evpn-3tier) is turned into the lab model — the same shape the lab's own
tools/inventory.py builds from lab.conf — and written as a static Ansible inventory:

  inventory/hosts.yml              groups: vyos (role_spine, role_leaf, role_border, role_fw, role_wan), linux (role_lb,
                                   role_web, role_app, role_db), rack_<rack>;
                                   every host with ansible_host = its Nautobot primary IPv4
  inventory/group_vars/all/e3t.yml e3t: the whole lab model (what the templates cross-reference: peers, segments, plan)
  inventory/host_vars/<host>.yml   e3t_node: the host's own node (ports, peers, link kinds, loopback, ASN, tier address)
  inventory/RENDERED.md            when, from which Nautobot, which pipeline run; the commit message says the same

The pipeline (.gitea/workflows/deploy.yml) runs this at every run and commits the result to Gitea.
  NAUTOBOT_URL=http://10.0.0.10:8080 NAUTOBOT_TOKEN=... nautobot_inventory.py [--out inventory] [--model-json FILE]"""
import argparse, datetime, ipaddress, json, os, re, sys
from pathlib import Path
import requests
import yaml

HERE = Path(__file__).resolve().parent
LOCATION = "evpn-3tier"
ROLES = "spine,leaf,border,wan"                      # the devices this repo deploys (hosts.yml lists only these)
QUERY = """query {
  config_contexts(name: ["%(loc)s"]) { data }
  devices(location: ["%(loc)s"]) {
    name local_config_context_data
    role { name } rack { name } platform { name } primary_ip4 { address }
    interfaces {
      name description mac_address mtu enabled mode
      untagged_vlan { vid } tagged_vlans { vid }
      vrf { name }
      ip_addresses { address }
      cable { label }
      cable_termination { cable_end }
      cable_peer_interface { name device { name } }
    }
  }
}""" % {"loc": LOCATION}
ETH = re.compile(r"^eth(\d+)$")


class Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data): return True                     # no &id001 anchors: readable, diffable files


def fetch(url, token):
    r = requests.post(f"{url}/api/graphql/", json={"query": QUERY}, headers={"Authorization": f"Token {token}", "Accept": "application/json"}, timeout=120)
    r.raise_for_status(); body = r.json()
    if body.get("errors"): sys.exit(f"GraphQL: {body['errors']}")
    d = body["data"]
    if not d["config_contexts"]: sys.exit(f"config context {LOCATION} not found in Nautobot (seed it: evpn-3tier tools/nautobot_seed.py)")
    if not d["devices"]: sys.exit(f"no devices in location {LOCATION}")
    return d["config_contexts"][0]["data"], d["devices"]


def model(ctx, devices):
    """Nautobot -> the lab model (tools/inventory.py's JSON shape and derivations)."""
    pre = ctx["domain_prefix"]; strip = lambda n: n[len(pre):] if n.startswith(pre) else n   # noqa: E731
    ev = ctx["evpn"]; VL = {v["vlan"]: v for v in ev["vlans"]}
    nodes, links = {}, []
    for dv in sorted(devices, key=lambda x: x["local_config_context_data"]["idx"]):
        loc = dv["local_config_context_data"]; name = strip(dv["name"]); role = dv["role"]["name"].removeprefix("e3t-")
        I = {i["name"]: i for i in dv["interfaces"]}
        lo = next((ip["address"].split("/")[0] for ip in (I.get("dum0") or {}).get("ip_addresses", [])), None)
        n = {"name": name, "role": role, "idx": loc["idx"], "mgmt_ip": dv["primary_ip4"]["address"].split("/")[0], "console": loc["console"],
             "asn": loc.get("asn"), "loopback": lo, "rack": dv["rack"]["name"], "domain": dv["name"], "platform": dv["platform"]["name"], "ports": []}
        for i in sorted((i for i in dv["interfaces"] if ETH.match(i["name"])), key=lambda i: int(ETH.match(i["name"])[1])):
            p = {"name": i["name"], "mac": (i["mac_address"] or "").lower(), "kind": None, "peer": None, "peer_port": None,
                 # the port's intent (self-service Port Provisioning): admin state, access (one untagged VLAN) or trunk (tagged VLANs)
                 "enabled": i.get("enabled", True) is not False, "mode": (i.get("mode") or "").lower() or None,
                 "untagged_vlan": (i.get("untagged_vlan") or {}).get("vid"), "tagged_vlans": sorted(v["vid"] for v in i.get("tagged_vlans") or []),
                 "description": i.get("description") or ""}
            if i["name"] == "eth0": p["kind"] = "oob"
            elif i["cable"]:
                peer = i["cable_peer_interface"]
                p.update(kind=i["cable"]["label"], peer=strip(peer["device"]["name"]), peer_port=peer["name"])
                if (i["cable_termination"] or {}).get("cable_end") == "A":
                    links.append({"a": name, "a_port": i["name"], "b": p["peer"], "b_port": p["peer_port"], "kind": p["kind"]})
            elif (i["description"] or "").startswith("NAT uplink"):
                p.update(kind="network", network=re.search(r"libvirt network (\S+)", i["description"])[1])
            n["ports"].append(p)
        sub = next((i for i in dv["interfaces"] if i["name"].startswith("bond0.") and i["ip_addresses"]), None)
        if sub:                                                   # a Linux VM: its tier VLAN, address and gateway
            vid = int(sub["name"].split(".")[1]); v = VL[vid]
            n["net"] = {"vlan": vid, "hostnum": loc["hostnum"], "es": loc["es"], "vrf": v["vrf"], "vni": v["vni"],
                        "address": sub["ip_addresses"][0]["address"], "gateway": v["gateway"].split("/")[0]}
        # every VLAN the VM's bond carries: its tier VLAN, plus any other bond0.<VLAN> Nautobot lists — while a server
        # moves to another VLAN (self-service "Move a server") it has both, so its leaves carry both and it is never cut off
        n["vlans"] = sorted({int(i["name"].split(".")[1]) for i in dv["interfaces"] if re.match(r"^bond0\.\d+$", i["name"])})
        if role == "fw": n["fw_es"] = loc["es"]
        nodes[name] = n
    links.sort(key=lambda l: (nodes[l["a"]]["idx"], int(l["a_port"][3:])))
    by_role = lambda r: sorted((n for n in nodes.values() if n["role"] == r), key=lambda n: n["idx"])   # noqa: E731
    for r in ("leaf", "border"):
        for k, n in enumerate(by_role(r), 1): n["fabric_no"] = k
    for k, n in enumerate(by_role("fw"), 1): n["fw_no"] = k
    # Ethernet Segments: a node's access links to its switch pair
    es = ctx["es"]; segments = []
    for n in nodes.values():
        num = (n.get("net") or {}).get("es") or n.get("fw_es")
        if num is None: continue
        legs = sorted(((p["peer"], p["peer_port"]) for p in n["ports"] if p["kind"] == "access"), key=lambda x: nodes[x[0]]["idx"])
        segments.append({"es": num, "node": n["name"], "role": n["role"], "sys_mac": f"{es['sys_mac_prefix']}:{num:02x}",
                         "legs": [{"switch": s, "port": pp, "df_pref": es["df_pref"][min(i, len(es["df_pref"]) - 1)]} for i, (s, pp) in enumerate(legs)],
                         "vlans": n["vlans"] if n.get("vlans") else [n["net"]["vlan"]] if n.get("net") else [t["vlan"] for t in ev["transit"]]})
    transit = []
    for t in ev["transit"]:
        net = ipaddress.ip_network(t["subnet"])
        transit.append({**t, "borders": {b["name"]: f"{net.network_address + 1 + b['fabric_no']}/{net.prefixlen}" for b in by_role("border")},
                        "fws": {f["name"]: f"{net.network_address + 10 + f['fw_no']}/{net.prefixlen}" for f in by_role("fw")}})
    wl = []
    for b in by_role("border"):
        for p in (p for p in b["ports"] if p["kind"] == "wan"):
            base = f"{ctx['wan']['prefix']}.{b['fabric_no']}"
            wl.append({"border": b["name"], "border_port": p["name"], "wan": p["peer"], "wan_port": p["peer_port"],
                       "border_ip": f"{base}.0/31", "wan_ip": f"{base}.1/31"})
    sync = ipaddress.ip_network(ctx["wan"]["sync_net"])
    wan = {**ctx["wan"], "links": wl, "sync": {f["name"]: f"{sync.network_address + f['fw_no']}/{sync.prefixlen}" for f in by_role("fw")},
           "sync_vip": f"{sync.broadcast_address - 1}/{sync.prefixlen}"}
    # load balancer peerings (self-service "Set up a load balancer to peer with the fabric"): eBGP from a load balancer
    # to the switch pair it is dual-homed to, on a peering VLAN (an L2VNI between the two switches) in its VRF. Addresses:
    # switch n of the pair .n of the peering subnet, the load balancer the last usable one. The peering VLAN rides the
    # load balancer's Ethernet Segment (its bond legs carry it).
    peerings = []
    for pr in ctx.get("lb_peerings", []):
        net = ipaddress.ip_network(pr["subnet"]); sw = sorted(pr["switches"], key=lambda x: nodes[x]["idx"])
        peerings.append({**pr, "switches": sw, "switch_ips": {x: f"{net.network_address + 1 + i}/{net.prefixlen}" for i, x in enumerate(sw)},
                         "switch_asns": {x: nodes[x]["asn"] for x in sw}, "lb_ip": f"{net.broadcast_address - 1}/{net.prefixlen}",
                         "vips": [str(ipaddress.ip_network(v)) for v in pr["vips"]]})
        for seg in segments:
            if seg["node"] == pr["name"] and pr["vlan"] not in seg["vlans"]: seg["vlans"] = seg["vlans"] + [pr["vlan"]]
    return {"lab": ctx["lab"], "oob": ctx["oob"], "fabric_mtu": ctx["fabric_mtu"], "domain_prefix": pre, "max_paths": ctx["max_paths"],
            "vrf_max_paths": ctx["vrf_max_paths"], "web_vip": ctx["web_vip"], "fw_policy": ctx["fw_policy"],
            "evpn": {"rt_as": ev["rt_as"], "anycast_mac": ev["anycast_mac"], "svi_host_base": ev["svi_host_base"], "svi_border_base": ev["svi_border_base"],
                     "vrfs": ev["vrfs"], "vlans": ev["vlans"], "transit": transit, "segments": sorted(segments, key=lambda s: s["es"])},
            "wan": wan, "nodes": sorted(nodes.values(), key=lambda n: n["idx"]), "links": links, "lb_peerings": peerings,
            # the fabric's subnets (one per VLAN, from its anycast gateway): what the borders may send the WAN (TO-WAN)
            "fabric_subnets": [{"vlan": v["vlan"], "vrf": v["vrf"], "subnet": str(ipaddress.ip_interface(v["gateway"]).network)}
                               for v in sorted(ev["vlans"], key=lambda v: v["vlan"])]}


def write(m, out, url, run, roles=None):
    out = Path(out); (out / "group_vars" / "all").mkdir(parents=True, exist_ok=True); hv = out / "host_vars"; hv.mkdir(exist_ok=True)
    for f in hv.glob("*.yml"): f.unlink()                       # a device removed from Nautobot leaves no stale file
    groups = {"vyos": {"children": {}}, "linux": {"children": {}}}
    racks = {}
    for n in m["nodes"]:
        if roles and n["role"] not in roles: continue
        plat = "vyos" if n["platform"] == "vyos" else "linux"
        groups[plat]["children"].setdefault(f"role_{n['role']}", {"hosts": {}})["hosts"][n["name"]] = {"ansible_host": n["mgmt_ip"]}
        racks.setdefault(f"rack_{n['rack']}", {"hosts": {}})["hosts"][n["name"]] = {}
        (hv / f"{n['name']}.yml").write_text(f"# rendered from Nautobot device {n['domain']} — do not edit: the pipeline overwrites it\n"
                                            + yaml.dump({"e3t_node": n}, Dumper=Dumper, sort_keys=False, width=200))
    hosts = {"all": {"children": {**groups, **dict(sorted(racks.items()))}}}
    (out / "hosts.yml").write_text(f"# rendered from Nautobot ({url}, location {LOCATION}) by inventory/nautobot_inventory.py — do not edit\n"
                                   + yaml.dump(hosts, Dumper=Dumper, sort_keys=False, width=200))
    (out / "group_vars" / "all" / "e3t.yml").write_text("# the lab model, rendered from Nautobot — do not edit: the pipeline overwrites it\n"
                                                        + yaml.dump({"e3t": m}, Dumper=Dumper, sort_keys=False, width=200))
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    (out / "RENDERED.md").write_text(f"# Inventory rendered from Nautobot\n\n- when: {now}\n- from: {url} (location `{LOCATION}`, config context `{LOCATION}`)\n"
                                     f"- run: {run or 'by hand'}\n- this repo deploys: {', '.join(n['name'] for n in m['nodes'] if not roles or n['role'] in roles)}\n- lab hosts: {len(m['nodes'])} ({sum(n['platform'] == 'vyos' for n in m['nodes'])} VyOS, "
                                     f"{sum(n['platform'] != 'vyos' for n in m['nodes'])} Linux), links: {len(m['links'])}, Ethernet Segments: {len(m['evpn']['segments'])}\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default=str(HERE)); ap.add_argument("--model-json"); ap.add_argument("--roles", default=ROLES, help="comma list: the roles whose devices this repo deploys")
    ap.add_argument("--url", default=os.environ.get("NAUTOBOT_URL", "http://10.0.0.10:8080")); ap.add_argument("--token", default=os.environ.get("NAUTOBOT_TOKEN"))
    ap.add_argument("--run", default=os.environ.get("GITHUB_RUN_ID") and f"Gitea Actions run {os.environ.get('GITHUB_RUN_NUMBER')} ({os.environ.get('GITHUB_SHA', '')[:8]})")
    a = ap.parse_args()
    if not a.token: sys.exit("NAUTOBOT_TOKEN missing")
    m = model(*fetch(a.url, a.token))
    if a.model_json: Path(a.model_json).write_text(json.dumps(m, indent=1))
    write(m, a.out, a.url, a.run, roles=[r for r in a.roles.split(",") if r])
    print(f"inventory: {sum(1 for n in m['nodes'] if n['role'] in a.roles.split(','))} of {len(m['nodes'])} hosts ({a.roles}), {len(m['links'])} links, {len(m['evpn']['segments'])} Ethernet Segments, from {a.url}")
