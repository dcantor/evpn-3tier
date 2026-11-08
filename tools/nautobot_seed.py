#!/usr/bin/env python3
"""Seed the shared Nautobot with evpn-3tier from lab.conf (idempotent). After this, Nautobot is what the Ansible pipeline
(Gitea lab/evpn-3tier-ansible) deploys from: its inventory script reads Nautobot back, rebuilds the lab model and renders
every device's configuration with Jinja2 templates.

What is modelled (everything of this lab lives under the names below; other labs' objects are never touched)
  location     evpn-3tier (type Site), a rack per lab.conf RACK (spine, rack1, rack2, border, edge, wan)
  namespace    evpn-3tier: every prefix, address and VRF of the lab (10.18 / 10.61 / 172.16 / 172.31 overlap other labs' in Global)
  tenancy      tenant group evpn-3tier, a tenant per VRF (web, app, db, outside)
  IPAM         VRFs with route targets <RT_AS>:<L3VNI> (import + export); VLAN group evpn-3tier (tier VLANs, firewall
               transit VLANs, the L3VNI VLANs); prefixes: OOB, loopbacks, tier subnets, transit, wan links, sync, clients
  devices      e3t-<node> (Nautobot names are global), roles e3t-<role>, platform vyos / alpine; eth0 mgmt-only with the
               primary IPv4 (Ansible's ansible_host), ethN with the lab MACs, descriptions and the fabric MTU; dum0 with
               the loopback /32 on the fabric nodes; on the Linux VMs bond0 + bond0.<VLAN> with the tier address
  cables       every lab.conf link, label = the link kind (fabric / access / wan / sync)
  local config context per device: idx, console port, ASN; Linux VMs: hostnum, Ethernet Segment; firewalls: ES
  config context evpn-3tier (scoped to the location): what Nautobot has no model for — VNIs, anycast MAC, RT AS, MTU,
               ECMP, SVI bases, the transit / wan / sync plan, the web VIP, the firewall policy, ES settings, OOB
Stale objects it owns (devices in the location, anything in the namespace, VLANs in its group) are removed.
  NAUTOBOT_TOKEN=... nautobot_seed.py [--url http://10.0.0.10:8080]"""
import argparse, ipaddress, json, os, subprocess, sys
from pathlib import Path
import pynautobot, requests

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB / "tools")); from render import _desc   # noqa: E402
p = argparse.ArgumentParser(); p.add_argument("--url", default=os.environ.get("NAUTOBOT_URL", "http://10.0.0.10:8080"))
p.add_argument("--token", default=os.environ.get("NAUTOBOT_TOKEN")); a = p.parse_args()
if not a.token: sys.exit("NAUTOBOT_TOKEN missing")
inv = json.loads(subprocess.run([str(LAB / "lab.sh"), "inventory"], capture_output=True, text=True, check=True).stdout)
raw = subprocess.run(["bash", "-c", 'source "$1"; echo "$ES_SYS_MAC_PREFIX"; echo "${ES_DF_PREF[*]}"; for t in "${TRANSIT[@]}"; do echo "T $t"; done', "-", str(LAB / "lab.conf")],
                     capture_output=True, text=True, check=True).stdout.splitlines()
ES_PREFIX, ES_DF = raw[0], [int(x) for x in raw[1].split()]
EV, SITE = inv["evpn"], inv["lab"]; N = {n["name"]: n for n in inv["nodes"]}
nb = pynautobot.api(a.url, token=a.token); H = {"Authorization": f"Token {a.token}", "Accept": "application/json", "Content-Type": "application/json"}
changes = []


def get_or_create(ep, lookup, **d):
    o = ep.get(**lookup)
    if o is None: o = ep.create(**lookup, **d); changes.append(f"created {ep.name}: {list(lookup.values())[0]}")
    return o


def current(v):
    if hasattr(v, "id"): return str(v.id)
    if hasattr(v, "value") and hasattr(v, "label"): return v.value
    if hasattr(v, "serialize"): return {k: current(getattr(v, k)) for k in v.serialize()}
    return v


def ensure(obj, **fields):
    ch = {}
    for k, v in fields.items():
        c = current(getattr(obj, k, None))
        same = c == v if isinstance(v, (dict, list)) else (str(c).lower() == str(v).lower() if k == "mac_address" else str(c) == str(v))
        if not same: ch[k] = v
    if ch: obj.update(ch); changes.append(f"updated {getattr(obj, 'name', None) or getattr(obj, 'prefix', None) or getattr(obj, 'address', obj)}: {', '.join(ch)}")
    return obj


def gql(q):
    r = requests.post(f"{a.url}/api/graphql/", json={"query": q}, headers=H, timeout=60); r.raise_for_status()
    if r.json().get("errors"): sys.exit(f"GraphQL: {r.json()['errors']}")
    return r.json()["data"]


def patch(path, **f):
    requests.patch(f"{a.url}/api/{path}/", json=f, headers=H, timeout=60).raise_for_status()


active = nb.extras.statuses.get(name="Active"); connected = nb.extras.statuses.get(name="Connected")

# ---- location, racks, namespace, tenancy --------------------------------------------------------------------------------
site = get_or_create(nb.dcim.locations, {"name": SITE}, location_type=nb.dcim.location_types.get(name="Site").id, status=active.id)
ensure(site, description="evpn-3tier lab: a three-tier app on a VyOS EVPN fabric, firewalls between the tiers' VRFs — github.com/dcantor/evpn-3tier")
racks = {r: get_or_create(nb.dcim.racks, {"name": r, "location": site.id}, status=active.id) for r in sorted({n["rack"] for n in inv["nodes"]})}
ns = get_or_create(nb.ipam.namespaces, {"name": SITE}, location=site.id)
ensure(ns, description="evpn-3tier: every prefix, address and VRF of the lab")
tg = get_or_create(nb.tenancy.tenant_groups, {"name": SITE})
V = {v["name"]: v for v in EV["vrfs"]}
tenants = {t: get_or_create(nb.tenancy.tenants, {"name": f"e3t-{t}"}, tenant_group=tg.id) for t in V}
for t, o in tenants.items(): ensure(o, tenant_group=tg.id, description=f"evpn-3tier VRF {t} (L3VNI {V[t]['l3vni']})")

# ---- roles, device types ---------------------------------------------------------------------------------------------------
COLOR = {"spine": "e65100", "leaf": "1565c0", "border": "6a1b9a", "fw": "b71c1c", "wan": "455a64", "lb": "00838f", "web": "2563eb", "app": "16a34a", "db": "d97706"}
drole = {r: get_or_create(nb.extras.roles, {"name": f"e3t-{r}"}, color=c, content_types=["dcim.device"]) for r, c in COLOR.items()}
loop_role = nb.extras.roles.get(name="Loopback")
plat = {"vyos": nb.dcim.platforms.get(name="vyos"), "alpine": nb.dcim.platforms.get(name="alpine")}
dt = {"vyos": get_or_create(nb.dcim.device_types, {"model": "VyOS"}, manufacturer=get_or_create(nb.dcim.manufacturers, {"name": "VyOS"}).id, u_height=0),
      "alpine": get_or_create(nb.dcim.device_types, {"model": "Alpine host"}, manufacturer=get_or_create(nb.dcim.manufacturers, {"name": "Alpine Linux"}).id, u_height=0)}

# ---- VRFs, route targets, VLANs, prefixes ----------------------------------------------------------------------------------
rt = {t: get_or_create(nb.ipam.route_targets, {"name": f"{EV['rt_as']}:{V[t]['l3vni']}"}, tenant=tenants[t].id) for t in V}
vrfs = {}
for t in V:
    vrfs[t] = nb.ipam.vrfs.get(name=t, namespace=ns.id) or nb.ipam.vrfs.create(name=t, namespace=ns.id, status=active.id, tenant=tenants[t].id,
                                                                              import_targets=[rt[t].id], export_targets=[rt[t].id])
    ensure(vrfs[t], tenant=tenants[t].id, description=f"VRF {t}: L3VNI {V[t]['l3vni']}, table {V[t]['table']}, L3VNI VLAN {V[t]['l3vlan']}")
vq = {v["name"]: v for v in gql('{ vrfs(namespace: ["%s"]) { name import_targets { name } export_targets { name } } }' % SITE)["vrfs"]}
for t in V:
    want = {f"{EV['rt_as']}:{V[t]['l3vni']}"}
    if {x["name"] for x in vq[t]["import_targets"]} != want or {x["name"] for x in vq[t]["export_targets"]} != want:
        patch(f"ipam/vrfs/{vrfs[t].id}", import_targets=[rt[t].id], export_targets=[rt[t].id]); changes.append(f"vrf {t} targets")
vg = get_or_create(nb.ipam.vlan_groups, {"name": SITE}, location=site.id)
vlans, want_vids = {}, set()
def vlan(vid, name, t, desc):
    want_vids.add(vid)
    o = nb.ipam.vlans.get(vid=vid, vlan_group=vg.id) or nb.ipam.vlans.create(vid=vid, name=name, vlan_group=vg.id, status=active.id, tenant=tenants[t].id)
    vlans[vid] = ensure(o, name=name, tenant=tenants[t].id, description=desc)
for v in EV["vlans"]: vlan(v["vlan"], f"{v['vrf']}-{v['vlan']}", v["vrf"], f"tier VLAN, L2VNI {v['vni']}, anycast gateway {v['gateway']} on {'+'.join(v['on'])}")
for t in EV["transit"]: vlan(t["vlan"], f"transit-{t['vrf']}", t["vrf"], f"firewall transit {t['vrf']}, L2VNI {t['vni']}, {t['subnet']} (borders + firewalls)")
for t in V.values(): vlan(t["l3vlan"], f"{t['name']}-l3vni", t["name"], f"L3VNI {t['l3vni']} of VRF {t['name']}")
want_pfx = set()
def prefix(pfx, desc, t=None, vid=None):
    want_pfx.add(pfx)
    o = nb.ipam.prefixes.get(prefix=pfx, namespace=ns.id)
    if o is None:
        o = nb.ipam.prefixes.create(prefix=pfx, namespace=ns.id, status=active.id, type="network"); changes.append(f"created prefix {pfx}")
        patch(f"ipam/prefixes/{o.id}", location=site.id)
    ensure(o, description=desc, tenant=tenants[t].id if t else None, vlan=vlans[vid].id if vid else None)
    if t:   # VRF membership (M2M)
        if not nb.ipam.vrf_prefix_assignments.get(vrf=vrfs[t].id, prefix=o.id): nb.ipam.vrf_prefix_assignments.create(vrf=vrfs[t].id, prefix=o.id); changes.append(f"vrf {t} += {pfx}")
prefix(str(ipaddress.ip_network(f"{inv['oob']['gateway']}/24", strict=False)), f"evpn-3tier OOB (libvirt {inv['oob']['network']}): eth0 of every VM, Ansible's management network")
prefix("10.61.0.0/24", "evpn-3tier loopbacks: router-id / VTEP")
for v in EV["vlans"]: prefix(str(ipaddress.ip_interface(v["gateway"]).network), f"VLAN {v['vlan']} ({v['vrf']} tier)", v["vrf"], v["vlan"])
for t in EV["transit"]: prefix(t["subnet"], f"transit {t['vrf']}: borders .2/.3, fw1 .11, fw2 .12", t["vrf"], t["vlan"])
for w in inv["wan"]["links"]: prefix(str(ipaddress.ip_interface(w["border_ip"]).network), f"{w['border']} {w['border_port']} <-> wan {w['wan_port']}", "outside")
prefix(inv["wan"]["sync_net"], "fw1 <-> fw2 state sync (conntrack-sync, VRRP FWSYNC)")
prefix(inv["wan"]["clients"], "the internet's test clients (on wan)")

# ---- devices, interfaces, addresses -----------------------------------------------------------------------------------------
want_ips, devs, ifs = set(), {}, {}
def ip_on(addr, iface, desc, role=None, t=None):
    want_ips.add(addr)
    o = nb.ipam.ip_addresses.get(address=addr, namespace=ns.id)
    if o is None: o = nb.ipam.ip_addresses.create(address=addr, namespace=ns.id, status=active.id); changes.append(f"created ip {addr}")
    ensure(o, description=desc, role=role.id if role else None, tenant=tenants[t].id if t else None)
    for x in nb.ipam.ip_address_to_interface.filter(interface=iface.id):
        if str(x.ip_address.id) != str(o.id): x.delete(); changes.append(f"unassigned old address from {iface.name}")
    if not nb.ipam.ip_address_to_interface.get(ip_address=o.id, interface=iface.id):
        nb.ipam.ip_address_to_interface.create(ip_address=o.id, interface=iface.id); changes.append(f"{addr} -> {iface.name}")
    return o

linux_roles = ("lb", "web", "app", "db")
inv["nodes"] = [n for n in inv["nodes"] if n["role"] != "ipam"]   # the external IPAM: not in Nautobot (syncing is the user's, later)
N = {n["name"]: n for n in inv["nodes"]}
for n in inv["nodes"]:
    is_linux = n["role"] in linux_roles
    local = {"idx": n["idx"], "console": n["console"], **({"asn": n["asn"]} if n.get("asn") else {}),
             **({"hostnum": n["net"]["hostnum"], "es": n["net"]["es"]} if n.get("net") else {}),
             **({"es": next(s["es"] for s in EV["segments"] if s["node"] == n["name"])} if n["role"] == "fw" else {})}
    f = dict(role=drole[n["role"]].id, device_type=dt["alpine" if is_linux else "vyos"].id, location=site.id, rack=racks[n["rack"]].id,
             platform=plat["alpine" if is_linux else "vyos"].id, status=active.id, local_config_context_data=local,
             tenant=tenants[n["net"]["vrf"]].id if n.get("net") else None)
    d = nb.dcim.devices.get(name=n["domain"])
    if d is None: d = nb.dcim.devices.create(name=n["domain"], **{k: v for k, v in f.items() if v is not None}); changes.append(f"created device {n['domain']}")
    elif str(d.location.id) != str(site.id): sys.exit(f"device {n['domain']} exists outside {SITE}: not ours")
    else: ensure(d, **f)
    devs[n["name"]] = d
    have = {i.name: i for i in nb.dcim.interfaces.filter(device=d.id)}; want = set()
    def iface(name, itype, desc, mac=None, mgmt=False, mtu=None, parent=None, vrf=None):
        want.add(name)
        g = dict(type=itype, description=desc, mgmt_only=mgmt, status=active.id, mtu=mtu, parent_interface=parent.id if parent else None,
                 vrf=vrf.id if vrf else None, **({"mac_address": mac} if mac else {}))
        i = have.get(name)
        if i is None: i = nb.dcim.interfaces.create(device=d.id, name=name, **{k: v for k, v in g.items() if v is not None}); changes.append(f"created interface {n['domain']} {name}")
        else: ensure(i, **g)
        ifs[(n["name"], name)] = i; return i
    e0 = iface("eth0", "10gbase-t", "OOB management", mac=n["ports"][0]["mac"], mgmt=True)
    mip = ip_on(f"{n['mgmt_ip']}/24", e0, f"{n['name']} OOB (ansible_host)")
    ensure(d, primary_ip4=mip.id)
    for port in n["ports"][1:]:
        desc = f"NAT uplink: libvirt network {port['network']} (DHCP)" if port["kind"] == "network" else _desc(port)
        iface(port["name"], "10gbase-t", desc, mac=port["mac"], mtu=inv["fabric_mtu"] if port["kind"] == "fabric" else None)
    if n.get("loopback"):
        ip_on(f"{n['loopback']}/32", iface("dum0", "virtual", "loopback / router-id"), f"{n['name']} loopback", role=loop_role)
    if n.get("net"):
        if n["role"] in ("web", "app", "db", "lb"):   # the tier VRF exists on the device as far as Nautobot is concerned
            if not nb.ipam.vrf_device_assignments.get(vrf=vrfs[n["net"]["vrf"]].id, device=d.id):
                nb.ipam.vrf_device_assignments.create(vrf=vrfs[n["net"]["vrf"]].id, device=d.id); changes.append(f"vrf on {n['name']}")
        b = iface("bond0", "lag", f"LACP to {', '.join(sorted({p['peer'] for p in n['ports'] if p.get('kind') == 'access'}))} (EVPN ES {n['net']['es']})")
        sub = iface(f"bond0.{n['net']['vlan']}", "virtual", f"VLAN {n['net']['vlan']} ({n['net']['vrf']} tier)", parent=b, vrf=vrfs[n["net"]["vrf"]])
        ip_on(n["net"]["address"], sub, f"{n['name']} ({n['net']['vrf']} tier)", t=n["net"]["vrf"])
    for name in sorted(set(have) - want, key=lambda s: -s.count(".")): have[name].delete(); changes.append(f"removed interface {n['domain']} {name}")

# ---- cables -------------------------------------------------------------------------------------------------------------------
def cable_of(itf):
    c = requests.get(f"{a.url}/api/dcim/interfaces/{itf.id}/", headers=H, timeout=30).json().get("cable")
    return requests.get(f"{a.url}/api/dcim/cables/{c['id']}/", headers=H, timeout=30).json() if c else None
for l in inv["links"]:
    x, y = ifs[(l["a"], l["a_port"])], ifs[(l["b"], l["b_port"])]
    c = cable_of(x)
    if c and c.get("termination_a_id") == str(x.id) and c.get("termination_b_id") == str(y.id) and c.get("label") == l["kind"]: continue
    for z in (x, y):
        old = cable_of(z)
        if old: requests.delete(f"{a.url}/api/dcim/cables/{old['id']}/", headers=H, timeout=30); changes.append(f"removed cable on {z.name}")
    nb.dcim.cables.create(termination_a_type="dcim.interface", termination_a_id=x.id, termination_b_type="dcim.interface", termination_b_id=y.id,
                          status=connected.id, label=l["kind"]); changes.append(f"cable {l['a']}:{l['a_port']}-{l['b']}:{l['b_port']}")

# ---- stale objects ---------------------------------------------------------------------------------------------------------------
want_dev = {n["domain"] for n in inv["nodes"]}
for d in nb.dcim.devices.filter(location=site.id):
    if d.name not in want_dev: d.delete(); changes.append(f"removed device {d.name}")
for o in nb.ipam.ip_addresses.filter(namespace=ns.id):
    if str(o.address) not in want_ips: o.delete(); changes.append(f"removed ip {o.address}")
for o in nb.ipam.prefixes.filter(namespace=ns.id):
    if str(o.prefix) not in want_pfx: o.delete(); changes.append(f"removed prefix {o.prefix}")
for o in nb.ipam.vlans.filter(vlan_group=vg.id):
    if o.vid not in want_vids: o.delete(); changes.append(f"removed vlan {o.vid}")

# ---- config context: the plan Nautobot has no model for ---------------------------------------------------------------------------
TR = [dict(zip(("vlan", "vrf", "vni", "subnet"), l.split()[1:])) for l in raw if l.startswith("T ")]
CTX = {"lab": SITE, "domain_prefix": inv["domain_prefix"], "oob": inv["oob"], "fabric_mtu": inv["fabric_mtu"], "max_paths": inv["max_paths"],
       "vrf_max_paths": inv["vrf_max_paths"], "web_vip": inv["web_vip"],
       "evpn": {"rt_as": EV["rt_as"], "anycast_mac": EV["anycast_mac"], "svi_host_base": EV["svi_host_base"], "svi_border_base": EV["svi_border_base"],
                "vrfs": EV["vrfs"], "vlans": EV["vlans"], "transit": [{"vlan": int(t["vlan"]), "vrf": t["vrf"], "vni": int(t["vni"]), "subnet": t["subnet"]} for t in TR]},
       "es": {"sys_mac_prefix": ES_PREFIX, "df_pref": ES_DF},
       "wan": {k: inv["wan"][k] for k in ("prefix", "clients", "sync_net")},
       "fw_policy": inv.get("fw_policy", [])}
ctx = nb.extras.config_contexts.get(name=SITE)
if ctx is None: nb.extras.config_contexts.create(name=SITE, description="evpn-3tier plan: VNIs, transit / wan / sync, VIP, firewall policy, ES", locations=[site.id], data=CTX); changes.append("created config context")
elif current(ctx.data) != CTX: patch(f"extras/config-contexts/{ctx.id}", data=CTX, locations=[site.id]); changes.append("config context updated")
print(f"seed complete: {len(changes)} change(s)" + (":\n  " + "\n  ".join(changes[:60]) + ("\n  ..." if len(changes) > 60 else "") if changes else " (in sync)"))
