#!/usr/bin/env python3
"""Load every subnet, VLAN, VRF and address reservation of evpn-3tier into phpIPAM (node `ipam`, https://10.18.0.5/).
The source is the lab itself (`lab.sh inventory`, i.e. lab.conf): what the devices actually run. Idempotent: run it
again after a lab.conf change and it creates what is missing and corrects what differs (it does not delete).
Nautobot is not touched: syncing phpIPAM and Nautobot is for later.

  bootstrap (once, over SSH)  the phpIPAM schema, its demo data removed, an admin password, the API on, an API app `e3t`
                              (ssl_code: a static app token, HTTPS only — nginx terminates TLS in front of phpIPAM). Credentials are made
                              at random and kept in ~/.config/evpn-3tier/phpipam.env (mode 600), not in git.
  load (the REST API)         section evpn-3tier; VRFs web / app / db / outside (RD = the lab's RT <rt_as>:<L3VNI>);
                              VLAN domain evpn-3tier with the tier, transit and L3VNI VLANs; subnets nested under their
                              containers (172.16.0.0/16, 172.31.0.0/16, 10.255.0.0/16), each with its VRF and VLAN;
                              every address with hostname, description, MAC (where the lab knows it) and gateway flag:
                              OOB (every VM), loopbacks, anycast gateways, the switches' own VLAN addresses, the servers,
                              the web VIP, transit (borders, firewalls), wan links, the sync link and its VRRP address,
                              the per-VRF probe addresses, the test clients.
  phpipam_load.py [--check]   --check: report what would be created / changed, change nothing"""
import argparse, html, ipaddress, json, os, secrets, subprocess, sys
from pathlib import Path
import requests, urllib3

sys.path.insert(0, str(Path(__file__).resolve().parent))
from labssh import inventory, run                                     # noqa: E402

urllib3.disable_warnings()
CRED = Path.home() / ".config/evpn-3tier/phpipam.env"
APP = "e3t"
ap = argparse.ArgumentParser(); ap.add_argument("--check", action="store_true"); a = ap.parse_args()
inv = inventory(); N = {n["name"]: n for n in inv["nodes"]}; EV = inv["evpn"]
IPAM = N["ipam"]["mgmt_ip"]; API = f"https://{IPAM}/api/{APP}"
log = []


# ---- bootstrap ----------------------------------------------------------------------------------------------------------------
def creds():
    if CRED.exists():
        return dict(l.split("=", 1) for l in CRED.read_text().split() if "=" in l)
    return None


def sql(q):
    rc, out = run("ipam", f"doas docker exec -i ipam-db-1 mariadb -uphpipam -pphpipam phpipam -N -e {json.dumps(q)}", timeout=120, inv=inv)
    if rc: sys.exit(f"SQL failed: {q[:80]}...: {out[-300:]}")
    return out.strip()


def bootstrap():
    c = creds()
    have_schema = sql("SHOW TABLES LIKE 'settings'") == "settings"
    if c and have_schema and sql(f"SELECT CONCAT(app_code, ' ', app_security) FROM api WHERE app_id='{APP}'") == f"{c['TOKEN']} ssl_code": return c
    if a.check: sys.exit("phpIPAM is not bootstrapped yet (run without --check)")
    if not have_schema:
        rc, out = run("ipam", "doas docker exec ipam-web-1 cat /phpipam/db/SCHEMA.sql | doas docker exec -i ipam-db-1 mariadb -uphpipam -pphpipam phpipam", timeout=300, inv=inv)
        if rc: sys.exit(f"schema import failed: {out[-400:]}")
        # phpIPAM ships demo sections / subnets / addresses / VLANs: this IPAM holds the lab only
        sql("DELETE FROM ipaddresses; DELETE FROM subnets; DELETE FROM sections; DELETE FROM vlans; DELETE FROM vrf")
        log.append("schema loaded, demo data removed")
    c = c or {"ADMIN_USER": "Admin", "ADMIN_PASSWORD": secrets.token_urlsafe(12), "TOKEN": secrets.token_hex(16)}
    _, h = run("ipam", f"doas docker exec ipam-web-1 php -r 'echo password_hash(\"{c['ADMIN_PASSWORD']}\", PASSWORD_DEFAULT);'", inv=inv)
    sql(f"UPDATE users SET password='{h.strip()}', passChange='No' WHERE username='Admin'")
    sql("UPDATE settings SET api=1, enableVRF=1, siteTitle='evpn-3tier IPAM', siteURL='https://" + IPAM + "/'")
    sql(f"INSERT INTO api (app_id, app_code, app_permissions, app_security, app_comment) VALUES ('{APP}', '{c['TOKEN']}', 3, 'ssl_code', "
        f"'evpn-3tier: tools/phpipam_load.py') ON DUPLICATE KEY UPDATE app_code='{c['TOKEN']}', app_permissions=3, app_security='ssl_code'")
    CRED.parent.mkdir(parents=True, exist_ok=True)
    CRED.write_text(f"PHPIPAM_URL=https://{IPAM}/\nADMIN_USER={c['ADMIN_USER']}\nADMIN_PASSWORD={c['ADMIN_PASSWORD']}\nAPI_APP={APP}\nTOKEN={c['TOKEN']}\n")
    CRED.chmod(0o600); log.append(f"admin password and API app {APP} set (credentials: {CRED})")
    return c


# ---- the API ------------------------------------------------------------------------------------------------------------------
class Ipam:
    def __init__(self, token):
        self.s = requests.Session(); self.s.verify = False; self.s.headers.update({"token": token, "Content-Type": "application/json"})

    def req(self, method, path, **kw):
        r = self.s.request(method, f"{API}/{path.strip('/')}/", timeout=30, **kw)
        try: body = r.json()
        except ValueError: sys.exit(f"{method} {path}: HTTP {r.status_code} {r.text[:200]}")
        # only a GET may answer 404 ("no such object"): a 404 on a write is a failure ("Vlan does not exist")
        if not body.get("success") and not (method == "GET" and r.status_code in (200, 404)):
            sys.exit(f"{method} {path}: {r.status_code} {body.get('message')} — payload {kw.get('json')}")
        return body.get("data") if body.get("success") else None

    def ensure(self, what, found, create_path, payload, update_path_fn):
        """found: the existing object (or None). Creates, or patches the fields that differ."""
        if found is None:
            if a.check: log.append(f"would create {what}"); return None
            self.req("POST", create_path, json=payload); log.append(f"created {what}"); return "new"
        # phpIPAM stores text HTML-escaped and strips what looks like a tag ("<->"): the plan avoids "<", compare unescaped
        diff = {k: v for k, v in payload.items() if html.unescape(str(found.get(k) if found.get(k) is not None else "")) != str(v if v is not None else "")}
        if diff:
            if a.check: log.append(f"would update {what}: {', '.join(diff)}"); return found
            self.req("PATCH", update_path_fn(found), json=diff); log.append(f"updated {what}: {', '.join(diff)}")
        return found


def plan():
    """The lab's addressing: VRFs, VLANs, subnets (with container parents), addresses — from the inventory."""
    rt = EV["rt_as"]; leaves = sorted((n for n in inv["nodes"] if n["role"] == "leaf"), key=lambda n: n["idx"])
    borders = sorted((n for n in inv["nodes"] if n["role"] == "border"), key=lambda n: n["idx"])
    vrfs = [{"name": v["name"], "rd": f"{rt}:{v['l3vni']}", "description": f"evpn-3tier VRF {v['name']}: L3VNI {v['l3vni']}, kernel table {v['table']}"} for v in EV["vrfs"]]
    vlans = [{"number": v["vlan"], "name": f"{v['vrf']}", "description": f"tier VLAN, L2VNI {v['vni']}, VRF {v['vrf']}, anycast gateway {v['gateway']}"} for v in EV["vlans"]]
    vlans += [{"number": t["vlan"], "name": f"transit-{t['vrf']}", "description": f"firewall transit {t['vrf']}, L2VNI {t['vni']} (borders + firewalls)"} for t in EV["transit"]]
    vlans += [{"number": v["l3vlan"], "name": f"{v['name']}-l3vni", "description": f"L3VNI {v['l3vni']} of VRF {v['name']} (symmetric IRB)"} for v in EV["vrfs"]]
    S, A = [], []                                # subnets: (cidr, description, vrf, vlan, parent); addresses: (ip, cidr, host, desc, mac, gw)
    mac = lambda n, port: next((p["mac"] for p in n["ports"] if p["name"] == port), None)    # noqa: E731
    oob = str(ipaddress.ip_network(f"{inv['oob']['gateway']}/24", strict=False))
    S.append((oob, f"OOB management (libvirt {inv['oob']['network']}): eth0 of every VM", None, None, None))
    A.append((inv["oob"]["gateway"], oob, "lab-host", "lab host, OOB gateway (virbr-e3toob)", None, True))
    for n in inv["nodes"]:
        A.append((n["mgmt_ip"], oob, n["name"], f"{n['role']} OOB eth0, VM {n['domain']}, console :{n['console']}", mac(n, "eth0"), False))
    S.append(("10.61.0.0/24", "loopbacks: BGP router-id, VXLAN tunnel endpoint (VTEP) on leaves and borders", None, None, None))
    for n in inv["nodes"]:
        if n.get("loopback"): A.append((n["loopback"], "10.61.0.0/24", n["name"], f"loopback: router-id{', VTEP' if n['role'] in ('leaf', 'border') else ''}, AS {n['asn']}", None, False))
    S.append(("172.16.0.0/16", "evpn-3tier data centre: tier VLANs and firewall transit", None, None, None))
    for v in EV["vlans"]:
        gw = ipaddress.ip_interface(v["gateway"]); net = str(gw.network)
        S.append((net, f"VLAN {v['vlan']} — {v['vrf']} tier (L2VNI {v['vni']}), gateway {gw.ip} (anycast)", v["vrf"], v["vlan"], "172.16.0.0/16"))
        A.append((str(gw.ip), net, f"gw-{v['vrf']}", f"anycast gateway, every leaf{' + border' if 'borders' in v['on'] else ''}", EV["anycast_mac"], True))
        sw = (leaves if "leaves" in v["on"] else []) + (borders if "borders" in v["on"] else [])
        for k, s in enumerate(sw):
            base = EV["svi_host_base"] if s["role"] == "leaf" else EV["svi_border_base"]
            no = s.get("fabric_no") or (leaves.index(s) + 1 if s in leaves else borders.index(s) + 1)
            A.append((str(gw.network.network_address + base + no), net, s["name"], f"{s['name']} own address (br0.{v['vlan']})", None, False))
    for n in inv["nodes"]:
        if n.get("net"):
            A.append((n["net"]["address"].split("/")[0], str(ipaddress.ip_interface(n["net"]["address"]).network), n["name"],
                      f"{n['role']}: bond0.{n['net']['vlan']}, LACP, EVPN ES {n['net']['es']}", mac(n, "eth1"), False))
    web = next(v for v in EV["vlans"] if v["vrf"] == "web")
    A.append((inv["web_vip"], str(ipaddress.ip_interface(web["gateway"]).network), "web-vip", "web VIP: keepalived lb1/lb2, HAProxy :443/:80", "00:00:5e:00:01:0a", False))
    for t in EV["transit"]:
        S.append((t["subnet"], f"firewall transit {t['vrf']} (VLAN {t['vlan']}, L2VNI {t['vni']}): eBGP borders to firewalls", t["vrf"], t["vlan"], "172.16.0.0/16"))
        for b, c in t["borders"].items(): A.append((c.split("/")[0], t["subnet"], b, f"{b} br0.{t['vlan']} (VRF {t['vrf']})", None, False))
        for f, c in t["fws"].items(): A.append((c.split("/")[0], t["subnet"], f, f"{f} bond0.{t['vlan']} ({'preferred' if f == 'fw1' else 'standby'})", None, False))
    S.append(("172.31.0.0/16", "evpn-3tier edge: border to wan links, the firewalls' sync link", None, None, None))
    for w in inv["wan"]["links"]:
        net = str(ipaddress.ip_interface(w["border_ip"]).network)
        S.append((net, f"{w['border']} {w['border_port']} to wan {w['wan_port']} (VRF outside, eBGP)", "outside", None, "172.31.0.0/16"))
        A.append((w["border_ip"].split("/")[0], net, w["border"], f"{w['border']} {w['border_port']} (VRF outside)", None, False))
        A.append((w["wan_ip"].split("/")[0], net, "wan", f"wan {w['wan_port']}", mac(N["wan"], w["wan_port"]), False))
    sync = inv["wan"]["sync_net"]
    S.append((sync, "fw1 to fw2 state sync (conntrack-sync), VRRP group FWSYNC", None, None, "172.31.0.0/16"))
    for f, c in inv["wan"]["sync"].items(): A.append((c.split("/")[0], sync, f, f"{f} eth3 (sync link)", mac(N[f], "eth3"), False))
    A.append((inv["wan"]["sync_vip"].split("/")[0], sync, "fwsync-vrrp", "VRRP FWSYNC (active firewall, conntrack-sync)", None, False))
    S.append(("10.255.0.0/16", "probe addresses: each VTEP's /32 in each VRF (type-5 routes; verify)", None, None, None))
    for v in EV["vrfs"]:
        net = f"{v['probe']}.0/24"; S.append((net, f"probes in VRF {v['name']}", v["name"], None, "10.255.0.0/16"))
        used = {x["vrf"] for x in EV["vlans"] if "leaves" in x["on"]}
        for n in leaves + borders:
            if n["role"] == "border" or v["name"] in used:
                A.append((f"{v['probe']}.{n['loopback'].split('.')[-1]}", net, n["name"], f"probe address in VRF {v['name']}", None, False))
    cl = ipaddress.ip_network(inv["wan"]["clients"])
    S.append((str(cl), "the internet's test clients (on wan, announced to the borders)", None, None, None))
    A.append((str(cl.network_address + 1), str(cl), "wan-clients", "wan dum1: test clients (load, portal relay)", None, False))
    return vrfs, vlans, S, A


def main():
    c = bootstrap()
    api = Ipam(c["TOKEN"])
    vrfs, vlans, S, A = plan()
    sec = next((s for s in api.req("GET", "sections") or [] if s["name"] == "evpn-3tier"), None)
    api.ensure("section evpn-3tier", sec, "sections", {"name": "evpn-3tier", "description": "evpn-3tier lab: every subnet and reservation (from lab.conf, tools/phpipam_load.py)"},
               lambda f: f"sections/{f['id']}")
    sec = next((s for s in api.req("GET", "sections") or [] if s["name"] == "evpn-3tier"), None)
    have_vrf = {v["name"]: v for v in api.req("GET", "vrf") or []}
    for v in vrfs: api.ensure(f"VRF {v['name']}", have_vrf.get(v["name"]), "vrf", v, lambda f: f"vrf/{f['vrfId']}")
    have_vrf = {v["name"]: v for v in api.req("GET", "vrf") or []}
    dom = next((d for d in api.req("GET", "l2domains") or [] if d["name"] == "evpn-3tier"), None)
    api.ensure("VLAN domain evpn-3tier", dom, "l2domains", {"name": "evpn-3tier", "description": "evpn-3tier: tier, transit and L3VNI VLANs"}, lambda f: f"l2domains/{f['id']}")
    dom = next((d for d in api.req("GET", "l2domains") or [] if d["name"] == "evpn-3tier"), None)
    have_vlan = {int(v["number"]): v for v in (api.req("GET", f"l2domains/{dom['id']}/vlans") if dom else None) or []}
    for v in vlans: api.ensure(f"VLAN {v['number']}", have_vlan.get(v["number"]), "vlan", {**v, "domainId": dom["id"] if dom else None}, lambda f: f"vlan/{f['vlanId']}")
    have_vlan = {int(v["number"]): v for v in (api.req("GET", f"l2domains/{dom['id']}/vlans") if dom else None) or []}
    if not sec: print("\n".join(log)); return                         # --check before the section exists
    sid = {}
    for cidr, desc, vrf, vlan, parent in S:                           # containers come before their children in S
        net = ipaddress.ip_network(cidr)
        found = api.req("GET", f"subnets/cidr/{net.network_address}/{net.prefixlen}")
        found = next((x for x in (found or []) if x["sectionId"] == sec["id"]), None) if isinstance(found, list) else found
        # no VRF / VLAN: leave the field out (phpIPAM refuses vlanId 0: "Vlan does not exist")
        payload = {"subnet": str(net.network_address), "mask": net.prefixlen, "sectionId": sec["id"], "description": desc,
                   "masterSubnetId": sid.get(parent, 0) if parent else 0,
                   **({"vrfId": have_vrf[vrf]["vrfId"]} if vrf in have_vrf else {}), **({"vlanId": have_vlan[vlan]["vlanId"]} if vlan in have_vlan else {})}
        upd = {k: payload[k] for k in ("description", "vrfId", "vlanId", "masterSubnetId") if k in payload}
        r = api.ensure(f"subnet {cidr}", found, "subnets", payload, lambda f: f"subnets/{f['id']}") if not found else api.ensure(f"subnet {cidr}", found, "subnets", upd, lambda f: f"subnets/{f['id']}")
        found = api.req("GET", f"subnets/cidr/{net.network_address}/{net.prefixlen}")
        found = next((x for x in (found or []) if x["sectionId"] == sec["id"]), None) if isinstance(found, list) else found
        if found: sid[cidr] = found["id"]
    for cidr in {c for _, c, *_ in A}:
        if cidr not in sid: continue
        have = {x["ip"]: x for x in api.req("GET", f"subnets/{sid[cidr]}/addresses") or []}
        for ip, net, host, desc, mac, gw in [x for x in A if x[1] == cidr]:
            # phpIPAM: an address description is varchar(64)
            p = {"hostname": host, "description": desc[:64], "is_gateway": 1 if gw else 0, **({"mac": mac} if mac else {})}
            if ip in have: api.ensure(f"address {ip}", have[ip], None, p, lambda f: f"addresses/{f['id']}")
            else: api.ensure(f"address {ip} ({host})", None, "addresses", {"subnetId": sid[cidr], "ip": ip, **p}, None)
    n_sub, n_addr = len(S), len(A)
    print(("\n".join(log[:40]) + ("\n..." if len(log) > 40 else "") + "\n") if log else "", end="")
    print(f"{'check' if a.check else 'loaded'}: {len(vrfs)} VRFs, {len(vlans)} VLANs, {n_sub} subnets, {n_addr} addresses; "
          f"{len(log)} change(s){' would be made' if a.check else ''} — https://{IPAM}/ (credentials: {CRED})")


if __name__ == "__main__":
    main()
