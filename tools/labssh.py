"""SSH to lab nodes (paramiko): Linux VMs lab / lab, VyOS vyos / vyos. Shared by verify.py and the other tools."""
import json, os, subprocess, sys
from pathlib import Path
import paramiko

LAB = Path(__file__).resolve().parents[1]
LINUX_ROLES = ("lb", "web", "app", "db", "ipam")


def inventory():
    """The lab model. By default lab.conf's (lab.sh inventory). E3T_MODEL=<file>: a model a pipeline rendered from Nautobot
    (nautobot_inventory.py --model-json); E3T_MODEL=nautobot: built from Nautobot now. Nautobot's is the live plan — the
    self-service VLANs, peerings and moved servers — so the pipelines' verify and Fabric Factory's checks use it."""
    src = os.environ.get("E3T_MODEL")
    if src == "nautobot": return nautobot_model()
    if src: return json.loads(Path(src).read_text())
    return json.loads(subprocess.run([str(LAB / "lab.sh"), "inventory"], capture_output=True, text=True, check=True).stdout)


def nautobot_model(url=None, token=None):
    """The lab model from Nautobot (tools/nautobot_inventory.py: the pipelines' own inventory code). The token: the
    argument, else NAUTOBOT_TOKEN, else the NMS's /opt/nautobot/.env (read over SSH, never stored)."""
    sys.path.insert(0, str(LAB / "tools"))
    import nautobot_inventory as ni                                           # noqa: E402
    url = url or os.environ.get("NAUTOBOT_URL", "http://10.0.0.10:8080")
    token = token or os.environ.get("NAUTOBOT_TOKEN") or subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "lab@10.0.0.10", "grep ^NAUTOBOT_SUPERUSER_API_TOKEN= /opt/nautobot/.env | cut -d= -f2-"],
        capture_output=True, text=True, timeout=30).stdout.strip()
    ctx, devices = ni.fetch(url, token)
    return ni.model(ctx, devices)


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
