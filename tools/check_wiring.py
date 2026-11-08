#!/usr/bin/env python3
"""Prove the cabling: for every link in lab.conf, ping the IPv6 all-nodes address out of one end and check that the
neighbour answering is the expected peer port (MACs are 52:54:00:d0:<node idx>:<port>, so a MAC names its port).
A leaf port in an LACP bond (an Ethernet Segment leg) can't be pinged out of, and the server's legs share its bond's
MAC: there the leaf's LACP partner proves the cable instead — the server's bond (system MAC = its first NIC's MAC) and
the partner port number (the server enslaves eth1, eth2, ... in order, so port n is ethn).
Exit 1 if any link is miswired or silent.   check_wiring.py"""
import json, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import paramiko

LAB = Path(__file__).resolve().parents[1]
inv = json.loads(subprocess.run([str(LAB / "lab.sh"), "inventory"], capture_output=True, text=True, check=True).stdout)
N = {n["name"]: n for n in inv["nodes"]}


def ssh(node, cmd):
    n = N[node]; user, pw = ("lab", "lab") if n["role"] in ("lb", "web", "app", "db") else ("vyos", "vyos")
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(n["mgmt_ip"], username=user, password=pw, look_for_keys=False, allow_agent=False, timeout=20)
    try:
        _, o, _ = c.exec_command(cmd, timeout=60); return o.read().decode()
    finally:
        c.close()


def lacp_partner(node, port):
    """(partner system MAC, partner port number) of a bond member port, or None if the port is in no bond."""
    out = ssh(node, f"m=$(cat /sys/class/net/{port}/master/uevent 2>/dev/null | sed -n 's/^INTERFACE=//p');"
                    f" [ -n \"$m\" ] && sudo cat /proc/net/bonding/$m")
    blk = out.split(f"Slave Interface: {port}")[1:] if out else []
    if not blk: return None
    partner = blk[0].split("Slave Interface:")[0].split("details partner lacp pdu:")[-1]
    mac = next((l.split(": ")[1].strip() for l in partner.splitlines() if "system mac address" in l), "?")
    num = next((l.split(": ")[1].strip() for l in partner.splitlines() if "port number" in l), "?")
    return mac, num


def check(link):
    a, ap, b, bp = link["a"], link["a_port"], link["b"], link["b_port"]
    want = next(p["mac"] for p in N[b]["ports"] if p["name"] == bp)
    if link["kind"] == "access" and N[a]["role"] in ("leaf", "border"):
        try: lp = lacp_partner(a, ap)
        except Exception as e:                                          # noqa: BLE001
            return link, want, [f"unreachable: {e.__class__.__name__}"]
        if lp:                                                          # a bond leg: the LACP partner names the far port
            want = f"{N[b]['ports'][1]['mac']} port {int(bp[3:])}"
            return link, want, [f"{lp[0]} port {lp[1]}"]
        # a plain access port is a bridge member without an IPv6 link-local address: what the bridge learnt on it names the far end
        try: out = ssh(a, f"/sbin/bridge fdb show dev {ap} | grep -v permanent")
        except Exception as e:                                          # noqa: BLE001
            return link, want, [f"unreachable: {e.__class__.__name__}"]
        return link, want, sorted({l.split()[0] for l in out.splitlines() if l.strip()})
    try:
        out = ssh(a, f"ping -6 -c 2 -W 2 -I {ap} ff02::1 >/dev/null 2>&1; ip -6 neigh show dev {ap}")
    except Exception as e:                                              # noqa: BLE001
        return link, want, [f"unreachable: {e.__class__.__name__}"]
    return link, want, sorted({w for w in out.split() if w.count(":") == 5})


bad = 0
with ThreadPoolExecutor(8) as ex:
    for link, want, seen in ex.map(check, inv["links"]):
        if want not in seen:
            bad += 1; print(f"MISWIRED  {link['a']} {link['a_port']} -> {link['b']} {link['b_port']} ({link['kind']}): expected {want}, saw {seen or 'nothing'}")
print(f"{len(inv['links']) - bad} of {len(inv['links'])} links verified: each far end answers with the expected peer port's MAC (bond legs: LACP partner and port)")
sys.exit(1 if bad else 0)
