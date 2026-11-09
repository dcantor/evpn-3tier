"""Read-only device tools: configurations and live state, fetched over SSH (tools/labssh.py) — no approval needed.

Only what is listed here ever runs on a device: the front end names a device and a format or state categories, never
a command. Secrets (password hashes, keys, VRRP / BGP passwords, …) are redacted before anything leaves the server."""
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings

LINUX_ROLES = ("lb", "web", "app", "db", "ipam")
ROLE_LABEL = {"spine": "Spine", "leaf": "Leaf", "border": "Border leaf", "fw": "Firewall", "wan": "WAN", "lb": "Load balancer",
              "web": "Web server", "app": "App server", "db": "Database", "ipam": "IPAM"}
OP = "/opt/vyatta/bin/vyatta-op-cmd-wrapper"

# ---- configurations ---------------------------------------------------------------------------------------------------------
VYOS_FORMATS = {"commands": ("Set commands", f"{OP} show configuration commands"),
                "tree": ("Hierarchical", f"{OP} show configuration")}
LINUX_FILES = {
    "*": ["/etc/network/interfaces", "/usr/local/sbin/e3t-net"],
    "lb": ["/etc/haproxy/haproxy.cfg", "/etc/keepalived/keepalived.conf", "/etc/bird.conf"],
    "web": ["/etc/nginx/http.d/default.conf"],
    "app": ["/etc/init.d/e3t-app"],
    "db": ["/var/lib/postgresql/17/data/postgresql.conf", "/var/lib/postgresql/17/data/pg_hba.conf"],
    "ipam": ["/etc/nginx/http.d/default.conf", "/etc/local.d/ipam.start"],
}
REDACT = [
    (re.compile(r"((?:encrypted|plaintext)-password|password|secret|pre-shared-secret|key|community|auth_pass|auth-pass)(\s+)'[^']*'", re.I), r"\1\2'<redacted>'"),
    (re.compile(r"((?:encrypted|plaintext)-password|password|secret|auth_pass|auth-pass)(\s+)(?!')(\S+)", re.I), r"\1\2<redacted>"),
    (re.compile(r"^(\s*(?:stats auth|userlist\s+\S+|user\s+\S+\s+(?:insecure-)?password)\s+)\S+.*$", re.I | re.M), r"\1<redacted>"),
    (re.compile(r"(password\s*=\s*)\S+", re.I), r"\1<redacted>"),
]

# ---- state ------------------------------------------------------------------------------------------------------------------
# category -> (label, description, {kind: [(title, how, command)]}, roles or None); how: op (VyOS op mode), vtysh, sh
STATE = {
    "interfaces": ("Interfaces", "Link and admin state, addresses, MACs, MTU, VRF", {
        "vyos": [("Interfaces", "op", "show interfaces")],
        "linux": [("Interfaces", "sh", "ip -br link; echo; ip -br addr")]}, None),
    "routes": ("Routing tables", "IPv4 routes in every VRF / table", {
        "vyos": [("Summary", "vtysh", "show ip route vrf all summary"), ("Routes", "vtysh", "show ip route vrf all")],
        "linux": [("Routes", "sh", "ip -4 route show table all | grep -vE '^(local|broadcast|multicast) '")]}, None),
    "bgp": ("BGP", "Sessions and prefixes", {
        "vyos": [("BGP summary (every VRF)", "vtysh", "show bgp vrf all summary"), ("EVPN sessions", "vtysh", "show bgp l2vpn evpn summary")],
        "linux": [("BIRD protocols", "sh", "doas birdc show protocols"), ("BIRD routes", "sh", "doas birdc show route")]}, ("vyos", "lb")),
    "aspath": ("BGP AS paths", "Every BGP path with its AS path, next hop and origin; best and multipath marked", {
        "vyos": [("IPv4 unicast, every VRF", "vtysh", "show bgp vrf all ipv4 unicast"),
                 ("EVPN IP prefixes (type 5)", "vtysh", "show bgp l2vpn evpn route type prefix"),
                 ("_json", "vtysh", "show bgp vrf all ipv4 unicast json")],
        "linux": [("BIRD routes with attributes", "sh", "doas birdc show route all"),
                  ("BGP sessions: local and neighbour AS (the borders show the AS path as received)", "sh", "doas birdc show protocols all 'border*'")]}, ("vyos", "lb")),
    "evpn": ("EVPN / VXLAN", "VNIs, remote VTEPs, Ethernet segments", {
        "vyos": [("VNIs", "vtysh", "show evpn vni"), ("Ethernet segments", "vtysh", "show evpn es"), ("Remote VTEPs", "vtysh", "show evpn vni detail")]}, ("leaf", "border")),
    "mac": ("MAC addresses", "Learned MACs: EVPN (local and remote) and the bridge's FDB", {
        "vyos": [("EVPN MACs (every VNI)", "vtysh", "show evpn mac vni all"), ("Bridge FDB (br0, dynamic)", "sh", "/sbin/bridge fdb show br br0 | grep -v permanent")],
        "linux": [("Bridge / bond FDB", "sh", "/sbin/bridge fdb show 2>/dev/null | grep -v permanent || echo 'no bridge'")]}, None),
    "arp": ("ARP / neighbours", "IPv4 neighbours", {
        "vyos": [("ARP", "op", "show arp")],
        "linux": [("Neighbours", "sh", "ip -4 neigh")]}, None),
    "bfd": ("BFD", "BFD sessions", {"vyos": [("BFD peers", "vtysh", "show bfd peers brief")]}, ("vyos",)),
    "vrrp": ("VRRP", "VRRP groups and who is master", {
        "vyos": [("VRRP", "op", "show vrrp")],
        "linux": [("keepalived (addresses held)", "sh", "ip -br addr | grep -E 'bond0|lo'; doas rc-service keepalived status")]}, ("fw", "lb")),
    "bond": ("Bond / LACP", "The server's bond to its two leaves", {"linux": [("bond0", "sh", "cat /proc/net/bonding/bond0")]}, ("lb", "web", "app", "db")),
    "services": ("Services", "Running services", {"linux": [("Services", "sh", "rc-status default"), ("Listening sockets", "sh", "ss -ltn")]}, ("linux",)),
    "system": ("System", "Version, uptime, load", {
        "vyos": [("Version", "op", "show version"), ("Uptime", "op", "show system uptime")],
        "linux": [("System", "sh", "cat /etc/alpine-release | sed 's/^/Alpine /'; uname -r; uptime")]}, None),
}


def _labssh():
    sys.path.insert(0, str(settings.LAB_DIR / "tools"))
    import labssh                                                             # noqa: E402
    return labssh


_inv = {"t": 0, "v": None}


def inventory():
    if _inv["v"] is None or time.time() - _inv["t"] > 60:
        _inv.update(t=time.time(), v=_labssh().inventory())
    return _inv["v"]


def kind(role):
    return "linux" if role in LINUX_ROLES else "vyos"


def devices():
    return [{"name": n["name"], "role": n["role"], "role_label": ROLE_LABEL.get(n["role"], n["role"]), "kind": kind(n["role"]),
             "mgmt_ip": n["mgmt_ip"], "rack": n.get("rack"), "asn": n.get("asn")} for n in inventory()["nodes"]]


def device(name):
    d = next((x for x in devices() if x["name"] == name), None)
    if d is None: raise KeyError(name)
    return d


def redact(text):
    for rx, rep in REDACT: text = rx.sub(rep, text)
    return text


def _run_many(name, parts, timeout=90):
    """Run several commands in one SSH session; returns their outputs in order (marker-separated)."""
    marks = [f"@@E3T-{i}@@" for i in range(len(parts))]
    script = "; ".join(f"echo '{m}'; {cmd} 2>&1" for m, cmd in zip(marks, parts))
    _, out = _labssh().run(name, script, timeout=timeout, inv=inventory())
    chunks, cur, buf = {}, None, []
    for line in out.splitlines():
        if line.strip() in marks:
            if cur is not None: chunks[cur] = "\n".join(buf).strip("\n")
            cur, buf = line.strip(), []
        else: buf.append(line)
    if cur is not None: chunks[cur] = "\n".join(buf).strip("\n")
    return [chunks.get(m, "") for m in marks]


def config(name, fmt="commands"):
    d = device(name); t0 = time.monotonic()
    if d["kind"] == "vyos":
        label, cmd = VYOS_FORMATS.get(fmt) or VYOS_FORMATS["commands"]
        text = redact(_run_many(name, [cmd])[0])
        files = [{"path": f"{name} — {label.lower()}", "text": text}]
    else:
        paths = LINUX_FILES["*"] + LINUX_FILES.get(d["role"], [])
        outs = _run_many(name, [f"doas cat {p} 2>/dev/null || echo '(not present)'" for p in paths])
        files = [{"path": p, "text": redact(o)} for p, o in zip(paths, outs) if o != "(not present)"]
        text = "\n\n".join(f"# ===== {f['path']} =====\n{f['text']}" for f in files)
    return {"device": name, "kind": d["kind"], "format": fmt if d["kind"] == "vyos" else "files", "text": text, "files": files,
            "lines": text.count("\n") + 1, "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "ms": int(1000 * (time.monotonic() - t0))}


def configs(names, fmt="commands"):
    with ThreadPoolExecutor(max_workers=6) as ex:
        return list(ex.map(lambda n: _safe(config, n, fmt), names))


def _safe(f, name, *a):
    try: return f(name, *a)
    except Exception as e:                                                    # noqa: BLE001
        return {"device": name, "error": f"{type(e).__name__}: {e}"}


def applies(cat, d):
    _, _, cmds, roles = STATE[cat]
    if d["kind"] not in cmds: return False
    return roles is None or d["kind"] in roles or d["role"] in roles


def state_catalog():
    return [{"key": k, "label": v[0], "help": v[1], "kinds": list(v[2]), "roles": list(v[3]) if v[3] else None} for k, v in STATE.items()]


def _cmd(how, cmd):
    return {"op": f"{OP} {cmd}", "vtysh": f"vtysh -c '{cmd}'", "sh": cmd}[how]


def state(name, cats):
    d = device(name); t0 = time.monotonic()
    want = [c for c in cats if c in STATE and applies(c, d)]
    plan = [(c, title, how, cmd) for c in want for title, how, cmd in STATE[c][2][d["kind"]]]
    outs = _run_many(name, [_cmd(how, cmd) for _, _, how, cmd in plan], timeout=120)
    results = {}
    for (c, title, how, cmd), out in zip(plan, outs):
        r = results.setdefault(c, {"key": c, "label": STATE[c][0], "sections": []})
        if title == "_json":                                                  # machine-readable twin: becomes a table
            if c == "aspath": r["paths"] = parse_frr_paths(out)
            continue
        r["sections"].append({"title": title, "command": cmd if how != "vtysh" else f"vtysh: {cmd}", "output": redact(out)})
    if "interfaces" in results:
        out = results["interfaces"]["sections"][0]["output"]
        results["interfaces"]["table"] = parse_vyos_interfaces(out) if d["kind"] == "vyos" else parse_linux_interfaces(out)
    return {"device": name, "kind": d["kind"], "role": d["role"], "results": [results[c] for c in want],
            "skipped": [c for c in cats if c in STATE and c not in want],
            "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "ms": int(1000 * (time.monotonic() - t0))}


# ---- BGP paths (FRR's JSON: every VRF, every path) ---------------------------------------------------------------------------
def parse_frr_paths(out):
    import json as _json
    try: d = _json.loads(out)
    except ValueError: return None
    rows = []
    for vrf, t in d.items():
        if not isinstance(t, dict): continue
        for prefix, paths in (t.get("routes") or {}).items():
            for p in paths:
                nh = [n.get("ip") or n.get("interface") or n.get("hostname") for n in p.get("nexthops", [])]
                rows.append({"vrf": t.get("vrfName", vrf), "prefix": prefix, "best": bool(p.get("bestpath")), "multipath": bool(p.get("multipath")),
                             "valid": bool(p.get("valid")), "as_path": p.get("path", ""), "origin": p.get("origin"),
                             "next_hop": ", ".join(x for x in nh if x) or "", "peer": p.get("peerId"), "reason": p.get("selectionReason"),
                             "local_as": t.get("localAS")})
    vorder = {"default": 0}
    return sorted(rows, key=lambda r: (vorder.get(r["vrf"], 1), r["vrf"], _pfx_key(r["prefix"]), not r["best"]))


def _pfx_key(p):
    try:
        import ipaddress as _ip
        n = _ip.ip_network(p, strict=False); return (n.version, int(n.network_address), n.prefixlen)
    except ValueError: return (9, 0, 0)


# ---- interface tables (the state view shows them as a table with up / down badges) -------------------------------------------
def parse_vyos_interfaces(out):
    lines = out.splitlines()
    try: dash = next(i for i, l in enumerate(lines) if l.startswith("---"))
    except StopIteration: return None
    head = lines[dash - 1]; cols = [m.start() for m in re.finditer(r"\S+", lines[dash])] + [10_000]
    names = [head[cols[i]:cols[i + 1]].strip() for i in range(len(cols) - 1)]
    rows = []
    for l in lines[dash + 1:]:
        if not l.strip(): continue
        cells = [l[cols[i]:cols[i + 1]].strip() if i < len(cols) - 2 else l[cols[i]:].strip() for i in range(len(cols) - 1)]
        rec = dict(zip(names, cells))
        if not rec.get("Interface") and rows:                                 # a second address of the interface above
            if rec.get("IP Address"): rows[-1]["addresses"].append(rec["IP Address"])
            continue
        sl = rec.get("S/L", "")
        rows.append({"name": rec.get("Interface"), "addresses": [a for a in [rec.get("IP Address")] if a and a != "-"], "mac": rec.get("MAC"),
                     "vrf": rec.get("VRF"), "mtu": rec.get("MTU"), "admin": "up" if sl.startswith("u") else "down" if sl.startswith("A") else sl.split("/")[0],
                     "link": "up" if sl.endswith("u") else "down", "description": rec.get("Description")})
    return rows


def parse_linux_interfaces(out):
    link, addr = {}, {}
    for l in out.splitlines():
        f = l.split()
        if len(f) < 2: continue
        n = f[0].split("@")[0]
        if len(f) >= 3 and re.match(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", f[2]): link[n] = {"state": f[1], "mac": f[2]}
        else: addr[n] = {"state": f[1], "addresses": [a for a in f[2:] if "/" in a]}
    rows = []
    for n in list(link) + [x for x in addr if x not in link]:
        st = (link.get(n) or addr.get(n))["state"]
        rows.append({"name": n, "addresses": (addr.get(n) or {}).get("addresses", []), "mac": (link.get(n) or {}).get("mac"), "vrf": None, "mtu": None,
                     "admin": "down" if st == "DOWN" else "up", "link": "up" if st in ("UP", "UNKNOWN") else "down", "description": None})
    return rows

