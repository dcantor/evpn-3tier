#!/usr/bin/env python3
"""Turn `lab.sh inventory`'s line records into the lab's JSON inventory: what the config renderer, the tools, the tests
and the portal read. lab.conf stays the one source; this only reshapes it (and derives the addressing plan)."""
import ipaddress, json, sys

lab, nodes, links, vrfs, vlans, transit, linux, fw_es = {}, {}, [], [], [], [], {}, {}
for line in sys.stdin:
    f = line.split()
    if not f: continue
    if f[0] == "LAB":
        lab = {"lab": f[1], "oob": {"network": f[2], "gateway": f[3]}, "fabric_mtu": int(f[4]), "domain_prefix": f[5],
               "max_paths": int(f[6]), "vrf_max_paths": int(f[9]), "evpn": {"rt_as": int(f[7]), "anycast_mac": f[8]}}
    elif f[0] == "NMS":
        lab["oob"]["nms"] = None if f[1] == "-" else f[1]
    elif f[0] == "SVI":
        lab["evpn"].update(svi_host_base=int(f[1]), svi_border_base=int(f[2]))
    elif f[0] == "WAN":
        lab["wan"] = {"prefix": f[1], "clients": f[2], "sync_net": f[3]}
    elif f[0] == "VIP":
        lab["web_vip"] = f[1]
    elif f[0] == "VRF":
        _, name, table, l3vni, l3vlan, probe = f
        vrfs.append({"name": name, "table": int(table), "l3vni": int(l3vni), "l3vlan": int(l3vlan), "probe": probe})
    elif f[0] == "VLAN":
        _, vid, vrf, vni, gw, where = f
        vlans.append({"vlan": int(vid), "vrf": vrf, "vni": int(vni), "gateway": gw, "on": where.split("+")})
    elif f[0] == "TRANSIT":
        _, vid, vrf, vni, net = f
        transit.append({"vlan": int(vid), "vrf": vrf, "vni": int(vni), "subnet": net})
    elif f[0] == "LINUXNET":
        _, name, vid, hostnum, es = f
        linux[name] = {"vlan": int(vid), "hostnum": int(hostnum), "es": int(es)}
    elif f[0] == "FWES":
        fw_es[f[1]] = int(f[2])
    elif f[0] == "FWPOLICY":
        _, src, dst, proto, port, to = f
        lab.setdefault("fw_policy", []).append({"from": src, "to": dst, "proto": proto, "port": None if port == "-" else port, "dest": to})
    elif f[0] == "ESCONF":
        lab["es"] = {"sys_mac_prefix": f[1], "df_pref": [int(x) for x in f[2:]]}
    elif f[0] == "NODE":
        _, name, role, idx, mgmt, console, asn, lo, rack, domain, mac0 = f
        nodes[name] = {"name": name, "role": role, "idx": int(idx), "mgmt_ip": mgmt, "console": int(console),
                       "asn": None if asn == "-" else int(asn), "loopback": None if lo == "-" else lo, "rack": rack,
                       "domain": domain, "ports": [{"name": "eth0", "mac": mac0, "kind": "oob", "peer": None, "peer_port": None}]}
    elif f[0] == "PORT":
        _, name, port, mac, *peer = f
        p = {"name": port, "mac": mac, "kind": None, "peer": None, "peer_port": None}
        if peer and peer[0] != "-":
            p.update(peer=peer[0], peer_port=f"eth{peer[1]}", kind=peer[2])
            if peer[3] == "1": links.append({"a": name, "a_port": port, "b": peer[0], "b_port": f"eth{peer[1]}", "kind": peer[2]})
        nodes[name]["ports"].append(p)
    elif f[0] == "NET":
        _, name, port, net, mac = f
        nodes[name]["ports"].append({"name": port, "mac": mac, "kind": "network", "network": net, "peer": None, "peer_port": None})

by_role = lambda r: sorted((n for n in nodes.values() if n["role"] == r), key=lambda n: n["idx"])
VL = {v["vlan"]: v for v in vlans}
for i, n in enumerate(by_role("leaf"), 1): n["fabric_no"] = i
for i, n in enumerate(by_role("border"), 1): n["fabric_no"] = i
for i, n in enumerate(by_role("fw"), 1): n["fw_no"] = i

# every Linux VM: its tier VLAN, address and gateway
for name, l in linux.items():
    v = VL[l["vlan"]]; gw = ipaddress.ip_interface(v["gateway"])
    nodes[name]["net"] = {**l, "vrf": v["vrf"], "vni": v["vni"], "address": f"{gw.network.network_address + l['hostnum']}/{gw.network.prefixlen}",
                          "gateway": str(gw.ip)}
# the Ethernet Segments: a node's access links to its leaf / border pair
es = lab.pop("es")
segments = []
for n in nodes.values():
    num = (n.get("net") or {}).get("es") or fw_es.get(n["name"])
    if num is None: continue
    legs = sorted(((p["peer"], p["peer_port"]) for p in n["ports"] if p.get("kind") == "access"), key=lambda x: nodes[x[0]]["idx"])
    seg = {"es": num, "node": n["name"], "role": n["role"], "sys_mac": f"{es['sys_mac_prefix']}:{num:02x}",
           "legs": [{"switch": s, "port": pp, "df_pref": es["df_pref"][min(i, len(es["df_pref"]) - 1)]} for i, (s, pp) in enumerate(legs)]}
    # what the segment carries: a Linux VM its tier VLAN, a firewall every transit VLAN
    seg["vlans"] = [n["net"]["vlan"]] if n.get("net") else [t["vlan"] for t in transit]
    segments.append(seg)
bad = [x["vlan"] for x in vlans + transit] + [x["l3vlan"] for x in vrfs]
bad = [v for v in bad if not 1 <= v <= 4094]
if bad: sys.exit(f"lab.conf: VLAN IDs out of 802.1Q range 1-4094 (VyOS drops them silently): {bad}")
lab["evpn"].update(vrfs=vrfs, vlans=vlans, transit=transit, segments=sorted(segments, key=lambda s: s["es"]))
# transit addressing: border n .1+n, fw n .10+n
for t in transit:
    net = ipaddress.ip_network(t["subnet"])
    t["borders"] = {b["name"]: f"{net.network_address + 1 + b['fabric_no']}/{net.prefixlen}" for b in by_role("border")}
    t["fws"] = {fw["name"]: f"{net.network_address + 10 + fw['fw_no']}/{net.prefixlen}" for fw in by_role("fw")}
# border n <-> wan: <prefix>.<n>.0/31, border .0, wan .1
wl = []
for b in by_role("border"):
    for p in (p for p in b["ports"] if p.get("kind") == "wan"):
        base = f"{lab['wan']['prefix']}.{b['fabric_no']}"
        wl.append({"border": b["name"], "border_port": p["name"], "wan": p["peer"], "wan_port": p["peer_port"],
                   "border_ip": f"{base}.0/31", "wan_ip": f"{base}.1/31"})
lab["wan"]["links"] = wl
sync = ipaddress.ip_network(lab["wan"]["sync_net"])
lab["wan"]["sync"] = {fw["name"]: f"{sync.network_address + fw['fw_no']}/{sync.prefixlen}" for fw in by_role("fw")}
lab["wan"]["sync_vip"] = f"{sync.broadcast_address - 1}/{sync.prefixlen}"
for r in lab.get("fw_policy", []):
    if r["dest"] == "VIP": r["dest"] = lab["web_vip"]
print(json.dumps({**lab, "nodes": list(nodes.values()), "links": links}, indent=1))
