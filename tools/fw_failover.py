#!/usr/bin/env python3
"""Firewall failover under a live connection: one TCP connection web1 -> app1:8080 (an echo server), a message every
100 ms, crossing the active firewall. fw1 is powered off hard (virsh destroy) — the borders' BFD notices, BGP moves
every VRF's default route to fw2, and fw2 (VRRP master on the sync link now) commits the connection states conntrackd
copied from fw1. Then fw1 comes back (it is preferred: traffic fails back to it). The connection must survive both.

  fw_failover.py [--down SECS] [--settle SECS]       report: longest gap in the echo stream each way, whether the
                                                     connection survived, the firewall the flow crossed before/after"""
import argparse, subprocess, sys, time
from pathlib import Path
from labssh import inventory, run

LAB = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser(); ap.add_argument("--down", type=int, default=40); ap.add_argument("--settle", type=int, default=150)
ap.add_argument("--src", default="web1"); ap.add_argument("--dst", default="app1"); ap.add_argument("--port", type=int, default=8080)
a = ap.parse_args()
inv = inventory(); N = {n["name"]: n for n in inv["nodes"]}
dst_ip = N[a.dst]["net"]["address"].split("/")[0]
fw1 = next(n for n in inv["nodes"] if n["role"] == "fw" and n["fw_no"] == 1)
LOG = "/tmp/fw-failover.log"

SERVER = (f"nohup python3 -c \"import socket,threading # e3t-echo\n"
          f"s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('0.0.0.0',{a.port}));s.listen(8)\n"
          f"def h(c):\n while True:\n  d=c.recv(4096)\n  if not d: break\n  c.sendall(d)\n"
          f"while True:\n c,_=s.accept();threading.Thread(target=h,args=(c,),daemon=True).start()\" >/dev/null 2>&1 &")
# the client: one connection, a line every 100 ms; logs the time of every echo and, if the connection dies, why
CLIENT = (f"nohup python3 -c \"import socket,time # e3t-client\n"
          f"s=socket.create_connection(('{dst_ip}',{a.port}),timeout=5);s.settimeout(60)\n"
          f"f=open('{LOG}','w',buffering=1);i=0;f.write('start %.3f\\n'%time.time())\n"
          f"try:\n while True:\n  s.sendall(b'%d\\n'%i);d=s.recv(64)\n  if not d: f.write('closed %.3f\\n'%time.time());break\n"
          f"  f.write('%d %.3f\\n'%(i,time.time()));i+=1;time.sleep(0.1)\n"
          f"except Exception as e: f.write('error %.3f %s\\n'%(time.time(),e))\" >/dev/null 2>&1 &")


def virsh(*args):
    return subprocess.run(["virsh", "-q", "-c", "qemu:///system", *args], capture_output=True, text=True)


def crossing():
    """Which firewall carries the flow now: the one whose conntrack table holds it with traffic counted (fw1 or fw2)."""
    seen = {}
    for fw in (n for n in inv["nodes"] if n["role"] == "fw"):
        try:
            rc, o = run(fw["name"], f"sudo /usr/sbin/conntrack -L -p tcp --dport {a.port} -d {dst_ip} 2>/dev/null | grep -c ESTABLISHED", inv=inv, timeout=20)
            seen[fw["name"]] = int(o.strip() or 0)
        except Exception:                                     # noqa: BLE001 — a powered-off firewall
            seen[fw["name"]] = None
    return seen


def gaps(lines, t0, t1):
    ts = [float(l.split()[1]) for l in lines if l.split()[0].isdigit()]
    window = [t for t in ts if t0 - 1 <= t <= t1]
    if len(window) < 2: return None
    return max(b - x for x, b in zip(window, window[1:]))


# the kills run in their own SSH session: a shell whose command line also holds the started program would match its
# own pattern and kill itself
run(a.dst, "pkill -f [e]3t-echo", inv=inv); run(a.src, "pkill -f [e]3t-client", inv=inv)
run(a.dst, SERVER, inv=inv); time.sleep(1)
run(a.src, CLIENT, inv=inv); time.sleep(3)
print(f"connection {a.src} -> {a.dst}:{a.port} open; state on the firewalls: {crossing()}")
print(f"powering off fw1 ({fw1['domain']}) ..."); t_off = time.time(); virsh("destroy", fw1["domain"])
time.sleep(a.down)
print(f"state on the firewalls with fw1 off: {crossing()}")
print("starting fw1 ..."); t_on = time.time(); virsh("start", fw1["domain"])
print(f"waiting {a.settle} s for fw1 to boot and take the traffic back ..."); time.sleep(a.settle)
print(f"state on the firewalls: {crossing()}")
_, log = run(a.src, f"cat {LOG}", inv=inv); run(a.src, "pkill -f [e]3t-client", inv=inv); run(a.dst, "pkill -f [e]3t-echo", inv=inv)
lines = log.splitlines(); end = [l for l in lines if l.startswith(("closed", "error"))]
g1, g2 = gaps(lines, t_off, t_on), gaps(lines, t_on, time.time())
print(f"\nfailover to fw2: longest gap {g1:.2f} s" if g1 is not None else "\nfailover to fw2: no echoes during the outage")
print(f"failback to fw1: longest gap {g2:.2f} s" if g2 is not None else "failback to fw1: no echoes after fw1 came back")
print(f"messages echoed: {sum(1 for l in lines if l.split()[0].isdigit())}; connection {'DIED: ' + end[0] if end else 'survived both'}")
sys.exit(1 if end or g1 is None or g2 is None else 0)
