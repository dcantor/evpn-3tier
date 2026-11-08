#!/usr/bin/env python3
"""Snapshot the lab into a results directory: every VyOS node's configuration (<dir>/<node>.config.txt — diffed pre vs
post by run.sh) and routing / service state (<dir>/state/<node>.txt — kept for the record, not diffed: it has timers).
      capture.py <dir>"""
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from labssh import inventory, run                                    # noqa: E402

inv = inventory(); out = Path(sys.argv[1]); state = out / "state"; state.mkdir(parents=True, exist_ok=True)
stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S"); OP = "/opt/vyatta/bin/vyatta-op-cmd-wrapper"
CMDS = {
    "spine":  ["show bgp summary", "show bfd peers brief"],
    "leaf":   ["show bgp summary", "show bgp vrf all summary", "show evpn vni", "show evpn es", "show bfd peers brief"],
    "border": ["show bgp summary", "show bgp vrf all summary", "show evpn vni", "show evpn es", "show ip route vrf all"],
    "fw":     ["show bgp summary", "show ip route", "show vrrp", "show conntrack-sync status", "show firewall statistics"],
    "wan":    ["show bgp summary", "show ip route"],
    "lb":     ["ip -br a", "doas cat /proc/net/bonding/bond0"], "web": ["ip -br a"], "app": ["ip -br a"],
    "db":     ["ip -br a", "doas su postgres -s /bin/sh -c 'psql -qtAX -c \"select pg_is_in_recovery()\"'"],
    "ipam":   ["ip -br a", "doas docker ps --format '{{.Names}} {{.Image}} {{.Status}}'"],
}


def one(n):
    name, vy = n["name"], n["role"] in ("spine", "leaf", "border", "fw", "wan")
    try:
        blocks = []
        if vy:
            _, cfg = run(name, f"{OP} show configuration commands", timeout=120, inv=inv)
            (out / f"{name}.config.txt").write_text(f"# {name} configuration captured {stamp}\n{cfg}")
        for c in CMDS[n["role"]]:
            _, o = run(name, f"{OP} {c}" if vy else c, timeout=120, inv=inv)
            blocks.append(f"==== {c}\n{o.rstrip()}\n")
        (state / f"{name}.txt").write_text(f"# {name} state captured {stamp}\n\n" + "\n".join(blocks))
        return f"[{name}] captured"
    except Exception as e:                                           # noqa: BLE001 — a node that is down
        return f"[{name}] {e.__class__.__name__}: {e}"


with ThreadPoolExecutor(8) as ex:
    for line in ex.map(one, inv["nodes"]): print(line)
