"""SSH to lab nodes (paramiko): Linux VMs lab / lab, VyOS vyos / vyos. Shared by verify.py and the other tools."""
import json, subprocess
from pathlib import Path
import paramiko

LAB = Path(__file__).resolve().parents[1]
LINUX_ROLES = ("lb", "web", "app", "db")


def inventory():
    return json.loads(subprocess.run([str(LAB / "lab.sh"), "inventory"], capture_output=True, text=True, check=True).stdout)


def run(node, cmd, timeout=60, inv=None):
    """(exit status, stdout + stderr) of a shell command on a node."""
    inv = inv or inventory()
    n = next(x for x in inv["nodes"] if x["name"] == node)
    u = "lab" if n["role"] in LINUX_ROLES else "vyos"
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    for attempt in range(3):                      # sshd's MaxStartups throttles a burst of logins: retry
        try:
            c.connect(n["mgmt_ip"], username=u, password=u, look_for_keys=False, allow_agent=False, timeout=20, banner_timeout=30)
            break
        except (paramiko.SSHException, OSError):
            if attempt == 2: raise
    try:
        _, o, e = c.exec_command(cmd, timeout=timeout)
        out = o.read().decode() + e.read().decode()
        return o.channel.recv_exit_status(), out
    finally:
        c.close()


def vtysh_json(node, cmd, inv=None):
    rc, out = run(node, f"vtysh -c '{cmd} json'", inv=inv)
    return json.loads(out) if rc == 0 and out.strip() else None
