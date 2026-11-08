#!/usr/bin/env python3
"""Install what the VyOS CLI cannot express, over SSH, on the nodes that need it. Idempotent.

  leaves and borders (the VTEPs): the commit post-hook also turns MAC learning off on the bridge port vxlan0.
  leaves    a commit post-hook for the anycast gateways (the macvlans pethV over br0.V, tools/render.py):
            - it sets their MAC: this VyOS build fails to commit a pseudo-ethernet with both a MAC and a VRF;
            - it gives that MAC a local FDB entry on br0 in its VLAN. A MAC on an interface stacked on the bridge only
              lands in br0's own address list; without an entry in the bridge, frames to the gateway are unknown
              unicast — flooded to every VTEP over VXLAN and delivered locally too, so every leaf answers or routes its
              own copy (duplicate replies, every routed packet multiplied by the number of leaves).
            - it sets a 5-minute reachable time on those VLAN interfaces and schedules the neighbor refresh below.
            It runs after every commit (a commit can recreate the interfaces) and at boot.
            And the neighbor refresh (every minute, from cron): every host neighbor on a gateway VLAN interface is
            re-probed (arping from the leaf's own MAC), so it stays REACHABLE. With EVPN multihoming, FRR withdraws a
            host's MAC/IP route as soon as the kernel lets its entry go STALE (30 s after the last confirmation by
            default) — FRR issues 9347, 12574; Cumulus keeps neighbors fresh with its neighmgrd for the same reason.
            Without it an idle host's route comes and goes, and the first packets to it after a quiet spell are lost.
            And the protodown watcher (a daemon the hook keeps running): FRR decides when a leaf's segment ports must
            go down — every uplink (`evpn uplink`) lost (core isolation), or the startup delay after boot or after the
            uplinks return — but carries it out with the kernel's protodown, which this kernel offers on none of the
            lab's devices (bond, virtio: "Protodown not supported by device"). The watcher follows FRR's
            protodownReasons each second and sets the segment bonds down / up instead; LACP stops, and the server drops
            that leg within its 3 s timeout rather than sending into a leaf that can't reach the fabric.
            It also heals stale multihoming redirects (every 10 s): while a segment's local port is down, FRR points the
            segment's host MACs at vxlan0 (a static bridge entry: "send to the peer"); when the port comes back, FRR
            sometimes leaves that entry behind — and the host is black-holed in that VLAN on this leaf. Seen after commits
            that rebuild the bridge's VLAN interfaces (config pushes). An entry is removed only when FRR has the MAC as
            local on a segment port that is up and no protodown reason applies; the bridge relearns it at once.
            And it gives every local segment port its bridge backup (every 5 s): `backup_port vxlan0 backup_nhid <FRR's
            next-hop group of the segment's peers>`. While the port is down (its leg cut), the kernel bridge sends what
            this leaf forwards to the segment's hosts to the peer leaves over VXLAN, instead of into the dead port. FRR
            10.6 creates the group but leaves the port without a backup — and a leaf whose leg is down still attracts
            traffic for routes its host announces over BGP (the session survives through the peer): Kubernetes nodes'
            pod and service routes were black-holed for ~1 in 5 connections while a leg was down.

  every VyOS node: FRR logs routing state changes (BGP %ADJCHANGE, BFD) to syslog. VyOS renders `log syslog`, but FRR
            emits state changes at informational and VyOS only raises FRR to it when /tmp/vyos.frr.debug exists (tmpfs);
            a pre-config boot hook creates the flag before the configuration loads, and the level is set live now
            (as srv6-core's tools/frr_logging.py does).

  vyos_files.py push NODE...     (lab.sh bootstrap and lab.sh configure call it for every VyOS node)"""
import json, subprocess, sys
from pathlib import Path
import paramiko

LAB = Path(__file__).resolve().parents[1]
HOOK = "/config/scripts/commit/post-hooks.d/90-evpn-3tier-anycast"
OLD = ["/config/scripts/commit/post-hooks.d/90-evpn-3tier-anycast-fdb"]                  # earlier names, removed
BOOT = "/config/scripts/vyos-postconfig-bootup.script"
PRECONFIG = "/config/scripts/vyos-preconfig-bootup.script"
PRECONFIG_SH = "#!/bin/sh\n# evpn-3tier: FRR logs routing state changes (informational) — see tools/vyos_files.py\ntouch /tmp/vyos.frr.debug\n"
FRR_LOG_NOW = ("sudo touch /tmp/vyos.frr.debug && sudo vtysh -c 'configure terminal' -c 'log syslog informational' -c 'end' >/dev/null")
REFRESH = "/config/scripts/evpn-3tier-neigh-refresh"
CRON = "/etc/cron.d/evpn-3tier-neigh-refresh"                # not under /config: the hook re-creates it
PROTODOWN = "/config/scripts/evpn-3tier-protodown"
STARTUP_DELAY = 30                                            # s: EVPN-MH segment ports held down after the uplinks return
ANYCAST = r"""#!/bin/sh
PATH=/usr/sbin:/usr/bin:/sbin:/bin; export PATH   # cron runs with /usr/bin:/bin only, and `bridge` is in /sbin
# evpn-3tier (tools/vyos_files.py): the anycast gateways. Every macvlan over a VLAN interface of br0 (pethV over br0.V)
# gets the anycast MAC, and the bridge a local FDB entry for it in that VLAN, so frames to the gateway are delivered
# here instead of being flooded to every VTEP.
[ "$(id -u)" = 0 ] || exec sudo "$0" "$@" </dev/null   # sudo (use_pty) would read the committing session's typed-ahead input: a `save` sent after `commit` was swallowed
[ -d /sys/class/net/br0 ] || exit 0
# no MAC learning on the VXLAN bridge port: zebra installs every remote MAC itself (FRR's EVPN docs). With learning on, a
# local dual-homed host's frame that comes back over VXLAN from the segment peer (an ARP / ND reply it hashed to the peer)
# teaches the bridge the host is behind vxlan0, and zebra turns that into a static vxlan0 entry: the host black-holed
# in that VLAN on this leaf. VyOS has no setting for the bridge-port flag.
bridge link set dev vxlan0 learning off 2>/dev/null
# EVPN multihoming: how long a leaf keeps its segment ports down after its uplinks return (or after boot) — FRR's 180 s
# default made a rack's return cost 3 minutes; the fabric converges in seconds. VyOS has no setting for it (zebra's
# `evpn mh startup-delay`); set after every commit, as VyOS rewrites FRR's configuration. At boot the timer has already
# started with the default when this runs.
vtysh -c 'configure terminal' -c 'evpn mh startup-delay @STARTUP_DELAY@' >/dev/null 2>&1
# BGP in the tenant VRFs (hosts and Kubernetes nodes announcing services): no N-bit (RFC 8538). With it, a host whose hold
# timer expires — a dead node — counts as restarting, and its routes stay for its restart time (15 s more black hole);
# without it, graceful restart still covers what it is for: a Cilium agent / BIRD restart closes the session without a
# notification. The fabric's own instance keeps it. FRR's knob; VyOS has none.
vtysh -c 'show running-config' 2>/dev/null | sed -n 's/^router bgp \([0-9]*\) vrf \(.*\)$/\1 \2/p' | while read -r asn vrf; do
  vtysh -c 'configure terminal' -c "router bgp $asn vrf $vrf" -c 'no bgp graceful-restart notification' >/dev/null 2>&1
done
mac=@ANYCAST_MAC@
for d in /sys/class/net/br0.*; do
  [ -e "$d" ] || continue
  vid=${d##*.}
  for u in "$d"/upper_peth*; do
    [ -e "$u" ] || continue
    p=${u##*/upper_}
    [ "$(cat /sys/class/net/$p/address)" = "$mac" ] || ip link set dev "$p" address "$mac"
    bridge fdb replace "$mac" dev br0 vlan "$vid" self local
    sysctl -qw "net.ipv4.neigh.br0/$vid.base_reachable_time_ms=300000"     # REACHABLE 2.5-7.5 min after each probe
    sysctl -qw "net.ipv6.neigh.br0/$vid.base_reachable_time_ms=300000" 2>/dev/null
    sysctl -qw "net.ipv4.neigh.$p.base_reachable_time_ms=300000" "net.ipv6.neigh.$p.base_reachable_time_ms=300000" 2>/dev/null
  done
done
[ -f @CRON@ ] || printf '* * * * * root @REFRESH@\n' > @CRON@
v=$(md5sum @PROTODOWN@ | cut -c1-12)
if [ "$(cat /run/evpn-3tier-protodown.ver 2>/dev/null)" != "$v" ]; then pkill -f @PROTODOWN@; echo "$v" > /run/evpn-3tier-protodown.ver; fi   # a new version replaces the running one
pgrep -f @PROTODOWN@ >/dev/null || setsid -f @PROTODOWN@ </dev/null >/dev/null 2>&1
exit 0
"""
PROTODOWN_PY = r"""#!/usr/bin/env python3
# evpn-3tier (tools/vyos_files.py): carry out FRR's EVPN-MH protodown decision, which this kernel can't (no protodown on
# bond or virtio devices). While `show evpn json` lists protodownReasons (uplinks-down, startup-delay), every local
# Ethernet Segment port is set down; when the reasons clear, the ports this watcher took down come back up.
import json, os, subprocess, syslog, time

if os.geteuid() != 0: os.execvp("sudo", ["sudo", __file__])
STATE = "/run/evpn-3tier-protodown"                    # the ports this watcher took down (survives a restart)
syslog.openlog("evpn-3tier-protodown")


def vtysh(cmd):
    try: return json.loads(subprocess.run(["vtysh", "-c", cmd], capture_output=True, text=True, timeout=10).stdout)
    except Exception: return None                       # noqa: BLE001 — FRR restarting: try again next second


def admin_up(port):
    r = subprocess.run(["ip", "-j", "link", "show", port], capture_output=True, text=True)
    return r.returncode == 0 and "UP" in json.loads(r.stdout)[0].get("flags", [])


def load():
    try: return set(open(STATE).read().split())
    except OSError: return set()


def heal_redirects(ports, reasons):
    # remove `<mac> dev vxlan0 vlan <v> master br0 static` entries for MACs FRR knows as local on an up segment port
    if reasons: return
    try:
        fdb = json.loads(subprocess.run(["bridge", "-j", "fdb", "show", "br", "br0"], capture_output=True, text=True, timeout=10).stdout or "[]")
    except Exception: return                                                 # noqa: BLE001
    stale = [e for e in fdb if e.get("ifname") == "vxlan0" and e.get("vlan") and e.get("state") == "static" and "extern_learn" not in (e.get("flags") or [])]
    if not stale: return
    macs = vtysh("show evpn mac vni all json") or {}
    local = {(m, d.get("vlan")): d.get("intf") for vni in macs.values() for m, d in (vni.get("macs") or {}).items() if d.get("type") == "local"}
    for e in stale:
        intf = local.get((e["mac"], e["vlan"])) or next((i for (m, v), i in local.items() if m == e["mac"]), None)
        if intf in ports and admin_up(intf):
            subprocess.run(["bridge", "fdb", "del", e["mac"], "dev", "vxlan0", "vlan", str(e["vlan"]), "master"])
            syslog.syslog(f"healed: {e['mac']} vlan {e['vlan']} pointed at vxlan0 (a stale multihoming redirect) though it is local on {intf}")


def backups():
    # a local segment port's bridge backup: vxlan0 with FRR's next-hop group of the segment's peer VTEPs
    for e in vtysh("show evpn es detail json") or []:
        p, nhg, f = e.get("accessPort"), e.get("nexthopGroup"), e.get("flags") or []
        if not p or "local" not in f: continue
        try: link = json.loads(subprocess.run(["bridge", "-j", "-d", "link", "show", "dev", p], capture_output=True, text=True, timeout=10).stdout or "[{}]")[0]
        except Exception: continue                                           # noqa: BLE001
        want = nhg if nhg and "remote" in f and "nexthopGroupActive" in f else None
        have = link.get("backup_nhid") if link.get("backup_port") == "vxlan0" else None
        if want == have: continue
        if want: subprocess.run(["bridge", "link", "set", "dev", p, "backup_port", "vxlan0", "backup_nhid", str(want)])
        else: subprocess.run(["bridge", "link", "set", "dev", p, "nobackup_port"])
        syslog.syslog(f"{p}: bridge backup {'vxlan0 nhid ' + str(want) if want else 'removed'} (segment {e.get('esi')})")


def evpn_settled_since(state={"since": None}):
    # when every BGP session carrying EVPN became Established (None while any is not)
    d = vtysh("show bgp l2vpn evpn summary json") or {}
    peers = list((d.get("peers") or {}).values())
    if peers and all(p.get("state") == "Established" for p in peers):
        state["since"] = state["since"] or time.time()
    else:
        state["since"] = None
    return state["since"]


# evpn-3tier: the segment bonds boot DISABLED (tools/render.py) and this watcher owns their admin state from then on.
# Up only when FRR names no protodown reason — or only its startup delay (whose timer started at boot with FRR's 180 s
# default) while every EVPN session has been Established for SETTLE s. A bond VyOS brought up at boot attracted the
# servers' traffic (LACP aggregates in ~1 s) into a leaf that had not converged yet: ~6 s of loss on every reboot.
SETTLE = 20
downed, ports, tick = load(), set(), 0
while True:
    evpn, es = vtysh("show evpn json"), vtysh("show evpn es json")
    if evpn is not None and es is not None:
        ports |= {e["accessPort"] for e in es if "local" in e.get("flags", []) and e.get("accessPort")}
        reasons = evpn.get("protodownReasons") or []
        since = evpn_settled_since()
        ok = not reasons or (set(reasons) <= {"startup-delay"} and since is not None and time.time() - since >= SETTLE)
        for p in sorted(ports):
            if not ok and admin_up(p):
                subprocess.run(["ip", "link", "set", p, "down"]); downed.add(p)
                syslog.syslog(f"{p} down: FRR protodown ({', '.join(reasons) or 'EVPN not settled'})")
            elif ok and not admin_up(p):
                subprocess.run(["ip", "link", "set", p, "up"]); downed.discard(p)
                syslog.syslog(f"{p} up: no protodown reason left" + (f" (startup delay waived: EVPN settled {SETTLE} s)" if reasons else ""))
        open(STATE, "w").write(" ".join(sorted(downed)))
        tick += 1
        if tick % 10 == 0: heal_redirects(ports, reasons)
        if tick % 5 == 1: backups()
    time.sleep(1)
"""
REFRESH_SH = r"""#!/bin/sh
PATH=/usr/sbin:/usr/bin:/sbin:/bin; export PATH   # cron runs with /usr/bin:/bin only, and `bridge` is in /sbin
# evpn-3tier (tools/vyos_files.py): keep every LOCAL host neighbor on a gateway VLAN interface REACHABLE (cron, every
# minute), so FRR never withdraws a host's MAC/IP route for being STALE (and the bridge never ages out a quiet host's
# MAC). Remote = EVPN installed the host's MAC on vxlan0 (those entries don't age): hosts' ARP for the anycast gateway
# floods to every leaf, so every leaf's macvlan knows every host, and probing a remote one would claim it here. Local hosts known only on the macvlan (they
# talked to their gateway, nothing else) are pinged from the leaf's own address, which creates their entry on the VLAN
# interface — the one FRR advertises from; the others are re-probed with arping.
for d in /sys/class/net/br0.*; do
  [ -n "$(ls -d "$d"/upper_peth* 2>/dev/null)" ] || continue
  dev=${d##*/}; vid=${dev#br0.}; vrf=$(sed -n 's/^INTERFACE=//p' "$d/master/uevent" 2>/dev/null)
  remote_macs=" $(bridge fdb show br br0 vlan "$vid" | awk '$2 == "dev" && $3 == "vxlan0" {printf "%s ", $1}')"
  { for u in "$d"/upper_peth*; do ip -4 neigh show dev "${u##*/upper_}"; done; ip -4 neigh show dev "$dev"; } |
  while read -r ip f2 mac rest; do
    [ "$f2" = lladdr ] || continue                                  # no MAC (e.g. zebra's "proto zebra" sync entries)
    case "$rest" in *extern_learn*|*NOARP*|*PERMANENT*|*FAILED*|*INCOMPLETE*) continue;; esac
    case "$remote_macs" in *" $mac "*) continue;; esac                # behind another leaf
    # only hosts of the VLAN's subnet: a service address a server announces over BGP (routed via it) can leave an entry too
    ip -4 route get "$ip" vrf "$vrf" 2>/dev/null | head -1 | grep -v " via " | grep -q " dev $dev " || continue
    if ip -4 neigh show "$ip" dev "$dev" | grep -q lladdr; then
      arping -q -c 1 -w 1 -I "$dev" "$ip" &
    else
      ip vrf exec "$vrf" ping -c 1 -W 1 -I "$dev" "$ip" >/dev/null 2>&1 &
    fi
    # and on the anycast gateway: a host's ARP for its gateway leaves an entry on the macvlan too, which FRR merges with the
    # VLAN interface's — when it goes STALE FRR marks the host local-inactive and withdraws it. It can't be probed from
    # there (a /32, no route out), so it is set REACHABLE with the MAC the VLAN interface confirmed.
    for u in "$d"/upper_peth*; do p=${u##*/upper_}; ip -4 neigh show "$ip" dev "$p" | grep -q lladdr && ip -4 neigh replace "$ip" lladdr "$mac" dev "$p" nud reachable; done
  done
  # IPv6 (dual-stack VLANs): the same, link-local addresses included. A ping is no ND confirmation (the entry went STALE
  # between runs and FRR flapped the host local-inactive), so when the host answers, the entry the VLAN interface has is
  # set REACHABLE again with its own MAC: the same state, which the kernel doesn't notify. Not `nud probe`: its PROBE
  # state is local-inactive to FRR, and every flap re-installs the host's MH sync entry, which this kernel rejects
  # (docs/phase0-findings.md). A host the VLAN interface lacks is pinged, which creates its entry.
  { for u in "$d"/upper_peth*; do ip -6 neigh show dev "${u##*/upper_}"; done; ip -6 neigh show dev "$dev"; } |
  while read -r ip f2 mac rest; do
    [ "$f2" = lladdr ] || continue                                  # no MAC (e.g. zebra's "proto zebra" sync entries)
    case "$rest" in *extern_learn*|*NOARP*|*PERMANENT*|*FAILED*|*INCOMPLETE*) continue;; esac
    case "$remote_macs" in *" $mac "*) continue;; esac
    vl=$(ip -6 neigh show "$ip" dev "$dev")
    case "$vl" in
      *lladdr*)
        set -- $vl; r=; case "$vl" in *" router"*) r=router;; esac   # `change` would clear the router flag
        { ip vrf exec "$vrf" ping -6 -c 1 -W 1 -I "$dev" "$ip" >/dev/null 2>&1 &&
          ip -6 neigh change "$ip" lladdr "$3" dev "$dev" nud reachable $r 2>/dev/null; } & ;;
      *) case "$ip" in fe80:*) ;; *) ip vrf exec "$vrf" ping -6 -c 1 -W 1 -I "$dev" "$ip" >/dev/null 2>&1 & ;; esac ;;
    esac
    for u in "$d"/upper_peth*; do p=${u##*/upper_}; ip -6 neigh show "$ip" dev "$p" | grep -q lladdr && ip -6 neigh replace "$ip" lladdr "$mac" dev "$p" nud reachable; done
  done
done
wait
exit 0
"""


def files_for(inv, n):
    """Files per node, in install order; for a leaf the last one (the commit hook) is run once installed."""
    if n["role"] not in ("spine", "leaf", "border", "fw", "wan"): return {}
    if n["role"] not in ("leaf", "border"): return {PRECONFIG: PRECONFIG_SH}
    hook = (ANYCAST.replace("@ANYCAST_MAC@", inv["evpn"]["anycast_mac"]).replace("@CRON@", CRON).replace("@REFRESH@", REFRESH)
            .replace("@PROTODOWN@", PROTODOWN).replace("@STARTUP_DELAY@", str(STARTUP_DELAY)))
    return {PRECONFIG: PRECONFIG_SH, REFRESH: REFRESH_SH, PROTODOWN: PROTODOWN_PY, HOOK: hook}


def push(inv, node, n):
    files = files_for(inv, n)
    if not files: return f"[{node}] nothing to install"
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(n["mgmt_ip"], username="vyos", password="vyos", look_for_keys=False, allow_agent=False, timeout=20)
    try:
        def run(cmd, data=None):
            i, o, e = c.exec_command(cmd, timeout=60)
            if data is not None: i.write(data); i.channel.shutdown_write()
            rc = o.channel.recv_exit_status()
            if rc: raise RuntimeError(f"{cmd}: {e.read().decode().strip()}")
            return o.read().decode()
        for old in OLD:
            run(f"sudo rm -f {old}; sudo sed -i '\\|{old}|d' {BOOT}")
        for path, body in files.items():
            run(f"sudo mkdir -p {Path(path).parent} && sudo tee {path} >/dev/null && sudo chmod 755 {path}", body)
        run(FRR_LOG_NOW)                                                                         # FRR state changes to syslog, now
        if HOOK in files:
            run(f"grep -qF '{HOOK}' {BOOT} || echo '{HOOK}' | sudo tee -a {BOOT} >/dev/null")    # at boot
            run(HOOK)                                                                            # and now
        return f"[{node}] installed {', '.join(files)}"
    finally:
        c.close()


if __name__ == "__main__":
    if sys.argv[1:] == ["check"]:            # lab.sh configure runs this BEFORE pushing any config: the segment bonds boot
        compile(PROTODOWN_PY, "protodown watcher", "exec")   # disabled, and only this watcher raises them
        print("vyos_files: ok"); sys.exit(0)
    if len(sys.argv) < 3 or sys.argv[1] != "push": sys.exit(__doc__)
    inv = json.loads(subprocess.run([str(LAB / "lab.sh"), "inventory"], capture_output=True, text=True, check=True).stdout)
    N = {n["name"]: n for n in inv["nodes"]}
    rc = 0
    for node in sys.argv[2:]:
        try: print(push(inv, node, N[node]))
        except Exception as e: print(f"[{node}] FAILED: {e}"); rc = 1
    sys.exit(rc)
