#!/usr/bin/env python3
"""The one VyOS config renderer: inventory -> `set` lines per node (forked from evpn-fabric's; see its
docs/phase0-findings.md for why the overlay is built the way it is).

  base      host name, the OOB address, SSH, the loopback, every port named after its peer (fabric ports at the fabric MTU)
  underlay  (spines, leaves, borders) eBGP over each fabric port, unnumbered (IPv6 link-local, RFC 8950), one AS per
            leaf / border and one for the spines, BFD, ECMP across the spines; every node announces its loopback
  overlay   (leaves, borders) BGP EVPN on the same sessions; spines pass the next hop unchanged. A VRF + L3VNI per tier
            (symmetric IRB), the tier VLANs as L2VNIs with an anycast gateway (same IP and MAC on every leaf, on a macvlan
            over the VLAN interface), ARP suppression, explicit route targets, a probe address per VRF per VTEP.
            The borders also carry VRF `outside`, the web VLAN (the load balancers hang off them) and the firewalls'
            transit VLANs (an L2VNI between the two borders, each border with its own address in it)
  access    (leaves, borders) every Ethernet Segment leg: an LACP bond, the segment's VLANs only; uplinks tracked
  edge      (borders) the routed /31 to wan in VRF outside with eBGP to wan; in every VRF, eBGP to fw1 / fw2 on that
            VRF's transit VLAN
  firewall  (fw1, fw2) the LACP bond to the borders, a VLAN per VRF on it, eBGP to both borders on each. The firewall's
            one routing table joins the VRFs: it sends each tier VRF only a default route (so every flow leaving a tier
            lands on it) and `outside` only the web VLAN. fw2 prepends its AS twice: every flow prefers fw1 both ways.
  wan       eBGP to both borders: a default route and the test-client network; NAT out to the real internet"""
import ipaddress

VM_PORT, VL_SYSLOG_PORT = 8428, 5514                  # on the NMS: VictoriaMetrics (InfluxDB write API), VictoriaLogs (syslog)
TELEGRAF_TOKEN = "evpn3tier-lab-telegraf".ljust(86, "_") + "=="

# BFD 300 ms x 5 (1.5 s): with 3, a single-vCPU router busy for a second dropped every session (evpn-fabric)
BFD_PROFILE, BFD_INTERVAL_MS, BFD_MULTIPLIER = "fabric", 300, 5
FW_STANDBY_PREPEND = 2                                # fw2's extra AS-path length


def bfd_profile():
    b = f"set protocols bfd profile {BFD_PROFILE}"
    return [f"{b} interval transmit {BFD_INTERVAL_MS}", f"{b} interval receive {BFD_INTERVAL_MS}", f"{b} interval multiplier {BFD_MULTIPLIER}"]


def _desc(p):
    return f"{p['kind']}: {p['peer']} {p['peer_port']}" if p.get("peer") else ("NAT uplink" if p.get("kind") == "network" else "unwired")


def _N(inv):
    return {x["name"]: x for x in inv["nodes"]}


def render(inv, node):
    n = _N(inv)[node]
    gw = inv["oob"]["gateway"]
    out = [f"# {n['name']} — {n['role']} in {n['rack']} ({inv['lab']}); rendered by tools/render.py from lab.conf",
           f"set system host-name {n['name']}",
           f"set interfaces ethernet eth0 address {n['mgmt_ip']}/24",
           "set interfaces ethernet eth0 description 'OOB management'",
           f"set protocols static route 10.0.0.0/8 next-hop {gw}",
           "set service ssh port 22",
           "set system ip multipath layer4-hashing", "set system ipv6 multipath layer4-hashing"]
    out += monitoring(inv, n)
    out += bfd_profile()
    if n.get("loopback"):
        out += [f"set interfaces dummy dum0 address {n['loopback']}/32", "set interfaces dummy dum0 description 'loopback / router-id'"]
    for p in n["ports"][1:]:
        out.append(f"set interfaces ethernet {p['name']} description '{_desc(p)}'")
        if p.get("kind") == "fabric":
            out.append(f"set interfaces ethernet {p['name']} mtu {inv['fabric_mtu']}")
        if p.get("kind") == "network":
            out.append(f"set interfaces ethernet {p['name']} address dhcp")
    if n["role"] in ("spine", "leaf", "border"):
        out += underlay(inv, n)
    if n["role"] in ("leaf", "border"):
        out += overlay(inv, n)
        out += access(inv, n)
    if n["role"] == "border":
        out += edge(inv, n)
    if n["role"] == "fw":
        out += firewall(inv, n)
        out += fw_policy(inv, n)
        out += fw_sync(inv, n)
    if n["role"] == "wan":
        out += wan(inv, n)
    return "\n".join(out) + "\n"


def monitoring(inv, n):
    nms = inv["oob"].get("nms")
    if not nms: return []
    t = "set service monitoring telegraf"
    return [f"set service monitoring prometheus node-exporter listen-address {n['mgmt_ip']}",
            f"set service monitoring prometheus frr-exporter listen-address {n['mgmt_ip']}",
            f"{t} influxdb url http://{nms}", f"{t} influxdb port {VM_PORT}", f"{t} influxdb bucket {inv['lab']}",
            f"{t} influxdb authentication organization lab", f"{t} influxdb authentication token {TELEGRAF_TOKEN}",
            f"{t} global-tag lab value {inv['lab']}", f"{t} global-tag role value {n['role']}", f"{t} global-tag rack value {n['rack']}",
            f"set system syslog remote {nms} port {VL_SYSLOG_PORT}", f"set system syslog remote {nms} protocol udp",
            f"set system syslog remote {nms} facility all level info"]


def _node_mac(n, last):
    """A MAC in the lab's own scheme for a virtual interface: <OUI of eth0>:<node idx>:<last>."""
    oui = n["ports"][0]["mac"].rsplit(":", 2)[0]
    return f"{oui}:{n['idx']:02x}:{last:02x}"


def _fabric_ports(n):
    return [p for p in n["ports"] if p.get("kind") == "fabric"]


def underlay(inv, n):
    out = [f"set protocols bgp system-as {n['asn']}", f"set protocols bgp parameters router-id {n['loopback']}",
           f"set protocols bgp address-family ipv4-unicast network {n['loopback']}/32",
           f"set protocols bgp address-family ipv4-unicast maximum-paths ebgp {inv['max_paths']}",
           "set protocols bgp parameters bestpath as-path multipath-relax",
           "set protocols bgp parameters log-neighbor-changes"]
    for p in _fabric_ports(n):
        nb = f"set protocols bgp neighbor {p['name']}"
        out += [f"{nb} interface remote-as external", f"{nb} description '{p['peer']}'", f"{nb} bfd profile {BFD_PROFILE}",
                f"{nb} address-family ipv4-unicast"]
        # a spine's l2vpn-evpn has a setting under it: VyOS lists only the deepest line (show configuration commands), so
        # the bare line would look missing to a line-by-line comparer (Ansible's vyos_config) on every run
        out.append(f"{nb} address-family l2vpn-evpn attribute-unchanged next-hop" if n["role"] == "spine" else f"{nb} address-family l2vpn-evpn")
    return out


def _where(n):
    return "leaves" if n["role"] == "leaf" else "borders"


def vrfs_of(inv, n):
    """The VRFs a VTEP carries: a leaf those of its VLANs, a border every VRF."""
    ev = inv["evpn"]
    if n["role"] == "border": return ev["vrfs"]
    used = {v["vrf"] for v in ev["vlans"] if "leaves" in v["on"]}
    return [t for t in ev["vrfs"] if t["name"] in used]


def own_addr(inv, n, cidr):
    """A leaf's / border's own address in a tier VLAN: .<base + its number>."""
    ev = inv["evpn"]; net = ipaddress.ip_interface(cidr).network
    base = ev["svi_host_base"] if n["role"] == "leaf" else ev["svi_border_base"]
    return f"{net.network_address + base + n['fabric_no']}/{net.prefixlen}"


def overlay(inv, n):
    ev = inv["evpn"]; rt = ev["rt_as"]
    vlans = [v for v in ev["vlans"] if _where(n) in v["on"]]
    transit = ev["transit"] if n["role"] == "border" else []
    last = n["loopback"].split(".")[-1]
    out = [f"set interfaces vxlan vxlan0 source-address {n['loopback']}", "set interfaces vxlan vxlan0 port 4789",
           "set interfaces vxlan vxlan0 parameters external", "set interfaces vxlan vxlan0 parameters nolearning",
           "set interfaces vxlan vxlan0 parameters neighbor-suppress", f"set interfaces vxlan vxlan0 mtu {inv['fabric_mtu']}",
           "set interfaces bridge br0 enable-vlan", "set interfaces bridge br0 description 'VLAN-aware bridge: VLAN <-> VNI'",
           # unique router MAC per VTEP: every clone of the base image has the same machine-id (evpn-fabric phase 2)
           f"set interfaces bridge br0 mac {_node_mac(n, 0xfe)}", f"set interfaces vxlan vxlan0 mac {_node_mac(n, 0xfd)}",
           "set protocols bgp address-family l2vpn-evpn advertise-all-vni"]
    for i, t in enumerate(vrfs_of(inv, n)):
        vrf = f"set vrf name {t['name']}"
        out += [f"{vrf} table {t['table']}", f"{vrf} vni {t['l3vni']}", f"{vrf} description 'VRF {t['name']} (L3VNI {t['l3vni']})'",
                f"set interfaces vxlan vxlan0 vlan-to-vni {t['l3vlan']} vni {t['l3vni']}",
                f"set interfaces bridge br0 member interface vxlan0 allowed-vlan {t['l3vlan']}",
                f"set interfaces bridge br0 vif {t['l3vlan']} vrf {t['name']}",
                f"set interfaces bridge br0 vif {t['l3vlan']} description 'L3VNI {t['l3vni']} ({t['name']})'",
                f"{vrf} protocols bgp system-as {n['asn']}",
                f"{vrf} protocols bgp address-family ipv4-unicast redistribute connected",
                f"{vrf} protocols bgp address-family l2vpn-evpn advertise ipv4 unicast",
                f"{vrf} protocols bgp address-family l2vpn-evpn route-target both {rt}:{t['l3vni']}",
                f"{vrf} protocols bgp address-family ipv4-unicast maximum-paths ebgp {inv['vrf_max_paths']}",
                f"{vrf} protocols bgp parameters bestpath as-path multipath-relax",
                f"{vrf} protocols bgp parameters log-neighbor-changes"]
        dum = f"dum{10 + i}"
        out += [f"set interfaces dummy {dum} address {t['probe']}.{last}/32", f"set interfaces dummy {dum} vrf {t['name']}",
                f"set interfaces dummy {dum} description 'probe address in {t['name']}'"]
    if vlans:
        out.append("set protocols bgp address-family l2vpn-evpn advertise-svi-ip")
    for v in vlans:
        br = f"set interfaces bridge br0 vif {v['vlan']}"; pe = f"set interfaces pseudo-ethernet peth{v['vlan']}"
        gw = ipaddress.ip_interface(v["gateway"])
        out += [f"set interfaces vxlan vxlan0 vlan-to-vni {v['vlan']} vni {v['vni']}",
                f"set interfaces bridge br0 member interface vxlan0 allowed-vlan {v['vlan']}",
                f"{br} address {own_addr(inv, n, v['gateway'])}", f"{br} mac {_node_mac(n, 0xfe)}", f"{br} vrf {v['vrf']}",
                f"{br} ip enable-arp-ignore", f"{br} ip enable-arp-announce",
                f"{br} description 'VLAN {v['vlan']} / L2VNI {v['vni']} ({v['vrf']})'",
                # the anycast gateway on a macvlan; its MAC is set by tools/vyos_files.py (VyOS can't commit a
                # pseudo-ethernet with both a MAC and a VRF)
                f"{pe} source-interface br0.{v['vlan']}", f"{pe} address {gw.ip}/32", f"{pe} vrf {v['vrf']}",
                f"{pe} ip enable-arp-ignore", f"{pe} ip disable-arp-filter",
                f"{pe} description 'VLAN {v['vlan']} anycast gateway {gw} ({ev['anycast_mac']}, set by tools/vyos_files.py)'",
                f"set protocols bgp address-family l2vpn-evpn vni {v['vni']} route-target both {rt}:{v['vni']}"]
    for t in transit:
        br = f"set interfaces bridge br0 vif {t['vlan']}"
        out += [f"set interfaces vxlan vxlan0 vlan-to-vni {t['vlan']} vni {t['vni']}",
                f"set interfaces bridge br0 member interface vxlan0 allowed-vlan {t['vlan']}",
                f"{br} address {t['borders'][n['name']]}", f"{br} mac {_node_mac(n, 0xfe)}", f"{br} vrf {t['vrf']}",
                f"{br} description 'transit {t['vrf']} <-> firewalls (L2VNI {t['vni']})'",
                f"set protocols bgp address-family l2vpn-evpn vni {t['vni']} route-target both {rt}:{t['vni']}"]
    return out


def access(inv, n):
    """Ethernet Segment legs: an LACP bond per segment, the same ESI and LACP system MAC on both switches of the pair,
    carrying only the segment's VLANs. Uplinks tracked: a switch cut off from both spines takes its legs down."""
    out = [f"set interfaces ethernet {p['name']} evpn uplink" for p in _fabric_ports(n)]
    legs = {(l["switch"], l["port"]): s for s in inv["evpn"]["segments"] for l in s["legs"]}
    for p in [p for p in n["ports"] if p.get("kind") == "access"]:
        seg = legs[(n["name"], p["name"])]
        bond = f"bond{seg['es']}"; b = f"set interfaces bonding {bond}"
        pref = next(l["df_pref"] for l in seg["legs"] if l["switch"] == n["name"])
        others = ", ".join(l["switch"] for l in seg["legs"] if l["switch"] != n["name"])
        out += [f"{b} mode 802.3ad", f"{b} lacp-rate fast", f"{b} min-links 1", f"{b} member interface {p['name']}",
                f"{b} system-mac {seg['sys_mac']}", f"{b} evpn es-id {seg['es']}", f"{b} evpn es-sys-mac {seg['sys_mac']}",
                f"{b} evpn es-df-pref {pref}", f"{b} description 'ES {seg['es']}: {seg['node']} (with {others})'",
                # boots down: the protodown watcher (tools/vyos_files.py) raises it once EVPN has converged
                f"{b} disable"]
        out += [f"set interfaces bridge br0 member interface {bond} allowed-vlan {v}" for v in seg["vlans"]]
    return out


def _asn(inv, name):
    return _N(inv)[name]["asn"]


def edge(inv, n):
    """Border: the /31 to wan in VRF outside with eBGP to wan; per VRF, eBGP to both firewalls on the transit VLAN."""
    out = []
    for w in (w for w in inv["wan"]["links"] if w["border"] == n["name"]):
        e = f"set interfaces ethernet {w['border_port']}"; ip = w["wan_ip"].split("/")[0]
        nb = f"set vrf name outside protocols bgp neighbor {ip}"
        out += [f"{e} address {w['border_ip']}", f"{e} vrf outside",
                f"{nb} remote-as {_asn(inv, w['wan'])}", f"{nb} description '{w['wan']} {w['wan_port']}'", f"{nb} bfd profile {BFD_PROFILE}",
                f"{nb} address-family ipv4-unicast"]
    # what a border sends the firewalls: no transit addresses and no host /32s. The other border's transit address
    # arrives here as an EVPN type-2 MAC/IP route (advertise-svi-ip) and lands in the VRF as a /32; sent on, it made
    # fw2 reach border2 *through* border1 — routed, TTL - 1, and single-hop BFD / eBGP to border2 died
    pl = "set policy prefix-list TO-FW"
    out += [f"{pl} description 'what the borders send the firewalls: tier subnets and the way out, no transit, no /32s'"]
    for r, t in enumerate(inv["evpn"]["transit"], 1):
        out += [f"{pl} rule {r * 10} action deny", f"{pl} rule {r * 10} prefix {t['subnet']}", f"{pl} rule {r * 10} le 32"]
    out += [f"{pl} rule 1000 action permit", f"{pl} rule 1000 prefix 0.0.0.0/0", f"{pl} rule 1000 le 24"]
    for t in inv["evpn"]["transit"]:
        for fw, cidr in t["fws"].items():
            nb = f"set vrf name {t['vrf']} protocols bgp neighbor {cidr.split('/')[0]}"
            out += [f"{nb} remote-as {_asn(inv, fw)}", f"{nb} description '{fw} ({t['vrf']})'", f"{nb} bfd profile {BFD_PROFILE}",
                    f"{nb} address-family ipv4-unicast prefix-list export TO-FW"]
    return out


def firewall(inv, n):
    """The firewall's routing: bond to the borders, a VLAN per VRF, eBGP to both borders in each. Policy: phase 2."""
    ev = inv["evpn"]; me = n["name"]
    seg = next(s for s in ev["segments"] if s["node"] == me)
    legs = [p["name"] for p in n["ports"] if p.get("kind") == "access"]
    sync = next(p["name"] for p in n["ports"] if p.get("kind") == "sync")
    web = next(v for v in ev["vlans"] if v["vrf"] == "web")
    web_net = str(ipaddress.ip_interface(web["gateway"]).network)
    b = "set interfaces bonding bond0"
    out = [f"{b} mode 802.3ad", f"{b} lacp-rate fast", f"{b} hash-policy layer3+4",
           f"{b} description 'ES {seg['es']}: LACP to {', '.join(l['switch'] for l in seg['legs'])}'",
           *[f"{b} member interface {l}" for l in legs],
           f"set interfaces ethernet {sync} address {inv['wan']['sync'][me]}",
           f"set protocols bgp system-as {n['asn']}", f"set protocols bgp parameters router-id {n['loopback']}",
           f"set protocols bgp address-family ipv4-unicast maximum-paths ebgp {inv['max_paths']}",
           "set protocols bgp parameters bestpath as-path multipath-relax", "set protocols bgp parameters log-neighbor-changes",
           "set policy prefix-list DEFAULT-ONLY rule 10 action permit", "set policy prefix-list DEFAULT-ONLY rule 10 prefix 0.0.0.0/0",
           "set policy prefix-list DEFAULT-ONLY description 'what a tier VRF gets from the firewall: the default route'",
           "set policy prefix-list PUBLIC rule 10 action permit", f"set policy prefix-list PUBLIC rule 10 prefix {web_net}",
           "set policy prefix-list PUBLIC description 'what outside gets from the firewall: the web VLAN only'"]
    standby = n["fw_no"] > 1
    for name, pl in (("TO-TIER", "DEFAULT-ONLY"), ("TO-OUTSIDE", "PUBLIC")):
        rm = f"set policy route-map {name} rule 10"
        out += [f"{rm} action permit", f"{rm} match ip address prefix-list {pl}"]
        if standby:
            out.append(f"{rm} set as-path prepend '{' '.join([str(n['asn'])] * FW_STANDBY_PREPEND)}'")
    if standby:   # no match clause: originate whenever the table holds any route, with the prepend
        out += ["set policy route-map ORIGINATE rule 10 action permit",
                f"set policy route-map ORIGINATE rule 10 set as-path prepend '{' '.join([str(n['asn'])] * FW_STANDBY_PREPEND)}'"]
    for t in ev["transit"]:
        vif = f"set interfaces bonding bond0 vif {t['vlan']}"
        out += [f"{vif} address {t['fws'][me]}", f"{vif} description 'transit {t['vrf']}'"]
        for border, cidr in t["borders"].items():
            nb = f"set protocols bgp neighbor {cidr.split('/')[0]}"
            out += [f"{nb} remote-as {_asn(inv, border)}", f"{nb} description '{border} ({t['vrf']})'", f"{nb} bfd profile {BFD_PROFILE}",
                    f"{nb} address-family ipv4-unicast route-map export {'TO-OUTSIDE' if t['vrf'] == 'outside' else 'TO-TIER'}"]
            if t["vrf"] == "outside":
                # the web subnet reaches the firewall from a border (AS path "65122 ..."); sent on to that same border's
                # VRF outside it was dropped as a loop — so one border's outside never had it, and with the other border
                # down wan had no route at all. as-override puts the firewall's AS in place of the border's
                out.append(f"{nb} address-family ipv4-unicast as-override")
            if t["vrf"] != "outside":   # FRR's default-originate skips the export route-map: fw2 prepends through its own
                out.append(f"{nb} address-family ipv4-unicast default-originate" + (" route-map ORIGINATE" if standby else ""))
    return out


def fw_zones(inv, n):
    """{ZONE: [interfaces]}: one per VRF (its transit VLAN), SYNC (the link to the other firewall), MGMT (eth0). MGMT
    must be a zone: an interface in none is dropped by LOCAL's default action (evpn-clab 0.12.0)."""
    z = {t["vrf"].upper(): [f"bond0.{t['vlan']}"] for t in inv["evpn"]["transit"]}
    z["SYNC"] = [p["name"] for p in n["ports"] if p.get("kind") == "sync"]
    z["MGMT"] = ["eth0"]
    return z


def fw_policy(inv, n):
    """The zone-based firewall (lab.conf FW_POLICY): replies always pass; between VRF zones only what the policy lists
    may start, everything else is dropped and logged. To the firewall itself: BGP, BFD and ping from the transit VLANs;
    anything from MGMT and SYNC. From the firewall: anything."""
    z = fw_zones(inv, n); names = list(z) + ["LOCAL"]; c = []
    for src in names:
        for dst in names:
            if src == dst: continue
            fw = f"{src}-{dst}"; name = f"set firewall ipv4 name {fw}"
            if src == "LOCAL" or src in ("MGMT", "SYNC") and dst == "LOCAL":
                c.append(f"{name} default-action accept")
            elif src in ("MGMT", "SYNC") or dst in ("MGMT", "SYNC"):     # nothing is routed to or from the management / sync links
                c += [f"{name} default-action drop", f"{name} default-log"]
            else:
                c += [f"{name} default-action drop", f"{name} default-log",
                      f"{name} rule 1 action accept", f"{name} rule 1 state established", f"{name} rule 1 state related",
                      f"{name} rule 1 description 'replies'",
                      f"{name} rule 2 action drop", f"{name} rule 2 state invalid", f"{name} rule 2 description 'invalid'"]
                if dst == "LOCAL":
                    for i, (proto, port, what) in enumerate([("tcp", "179", "BGP"), ("udp", "3784-3785", "BFD"), ("icmp", None, "ping")], 1):
                        r = f"{name} rule {10 * i}"
                        c += [f"{r} action accept", f"{r} protocol {proto}", f"{r} description '{what} from the borders'"]
                        if port: c.append(f"{r} destination port {port}")
                else:
                    i = 10
                    for p in inv.get("fw_policy", []):
                        if (p["from"].upper(), p["to"].upper()) != (src, dst): continue
                        r = f"{name} rule {i}"
                        c += [f"{r} action accept", f"{r} protocol {p['proto']}", f"{r} destination address {p['dest']}", f"{r} log",
                              f"{r} description '{p['from']} -> {p['to']} {p['dest']} {p['proto']}{' ' + p['port'] if p['port'] else ''}'"]
                        if p["port"]: c.append(f"{r} destination port {p['port']}")
                        if p["proto"] == "tcp": c.append(f"{r} state new")
                        i += 10
            c.append(f"set firewall zone {dst} from {src} firewall name {fw}")
    for zone, ifs in z.items():
        c += [f"set firewall zone {zone} default-action drop", f"set firewall zone {zone} intra-zone-filtering action accept",
              *[f"set firewall zone {zone} member interface {i}" for i in ifs]]
    c += ["set firewall zone LOCAL local-zone", "set firewall zone LOCAL default-action drop"]
    return c


def fw_sync(inv, n):
    """Connection-state sync to the other firewall (conntrackd over the sync link, unicast to the peer). VyOS ties it to
    VRRP: the VRRP master's conntrackd commits the states it learnt from the peer into its kernel when it takes over.
    The VRRP group lives on the sync link only, its address unused: fw1 (priority 200) is master while it lives."""
    me = n["name"]; sync = next(p["name"] for p in n["ports"] if p.get("kind") == "sync")
    peer = next(ip for f, ip in inv["wan"]["sync"].items() if f != me).split("/")[0]
    g = "set high-availability vrrp group FWSYNC"; cs = "set service conntrack-sync"
    return [f"{g} vrid 30", f"{g} interface {sync}", f"{g} address {inv['wan']['sync_vip']}",
            f"{g} priority {200 if n['fw_no'] == 1 else 100}", f"{g} advertise-interval 1",
            f"{g} description 'which firewall is active, for conntrack-sync'",
            "set high-availability vrrp sync-group FW member FWSYNC",
            f"{cs} interface {sync} peer {peer}", f"{cs} failover-mechanism vrrp sync-group FW",
            f"{cs} accept-protocol tcp", f"{cs} accept-protocol udp", f"{cs} accept-protocol icmp",
            f"{cs} disable-external-cache"]


def wan(inv, n):
    """wan: eBGP to both borders — a default route and the test clients' network — and NAT out of its uplink."""
    uplink = next(p["name"] for p in n["ports"] if p.get("kind") == "network")
    clients = ipaddress.ip_network(inv["wan"]["clients"])
    out = [f"set interfaces dummy dum1 address {clients.network_address + 1}/{clients.prefixlen}",
           "set interfaces dummy dum1 description 'the test clients (curl --interface 198.51.100.1)'",
           f"set protocols bgp system-as {n['asn']}", f"set protocols bgp parameters router-id {n['loopback']}",
           f"set protocols bgp address-family ipv4-unicast network {clients}",
           "set protocols bgp parameters log-neighbor-changes",
           "set policy prefix-list FROM-DC rule 10 action permit", "set policy prefix-list FROM-DC rule 10 prefix 172.16.0.0/16",
           "set policy prefix-list FROM-DC rule 10 le 32",
           "set policy prefix-list TO-DC rule 10 action permit", "set policy prefix-list TO-DC rule 10 prefix 0.0.0.0/0",
           "set policy prefix-list TO-DC rule 20 action permit", f"set policy prefix-list TO-DC rule 20 prefix {clients}",
           f"set nat source rule 100 outbound-interface name {uplink}", "set nat source rule 100 translation address masquerade",
           "set nat source rule 100 description 'the data centre out to the real internet'"]
    for w in (w for w in inv["wan"]["links"] if w["wan"] == n["name"]):
        e = f"set interfaces ethernet {w['wan_port']}"; ip = w["border_ip"].split("/")[0]
        nb = f"set protocols bgp neighbor {ip}"
        out += [f"{e} address {w['wan_ip']}",
                f"{nb} remote-as {_asn(inv, w['border'])}", f"{nb} description '{w['border']} {w['border_port']}'", f"{nb} bfd profile {BFD_PROFILE}",
                f"{nb} address-family ipv4-unicast default-originate",
                f"{nb} address-family ipv4-unicast prefix-list import FROM-DC",
                f"{nb} address-family ipv4-unicast prefix-list export TO-DC"]
    return out


def render_all(inv):
    return {n["name"]: render(inv, n["name"]) for n in inv["nodes"] if n["role"] in ("spine", "leaf", "border", "fw", "wan")}
