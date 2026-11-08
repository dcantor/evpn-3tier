#!/usr/bin/env python3
"""Run a shell command on any node over SSH (Linux lab / lab, VyOS vyos / vyos; paramiko, since the shell's ssh would prompt).
   host_cmd.py NODE CMD..."""
import json, subprocess, sys
from pathlib import Path
import paramiko
LAB = Path(__file__).resolve().parents[1]
inv = json.loads(subprocess.run([str(LAB / "lab.sh"), "inventory"], capture_output=True, text=True, check=True).stdout)
n = next(x for x in inv["nodes"] if x["name"] == sys.argv[1])
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
u = "lab" if n["role"] in ("lb", "web", "app", "db") else "vyos"
c.connect(n["mgmt_ip"], username=u, password=u, look_for_keys=False, allow_agent=False, timeout=20)
_, o, e = c.exec_command(" ".join(sys.argv[2:]), timeout=120)
sys.stdout.write(o.read().decode()); sys.stderr.write(e.read().decode()); rc = o.channel.recv_exit_status(); c.close(); sys.exit(rc)
