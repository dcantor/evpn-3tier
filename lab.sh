#!/usr/bin/env bash
# evpn-3tier lab controller (libvirt/KVM): VyOS spines / leaves / borders / firewalls / wan, Alpine load balancers and
# web / app / db servers, see lab.conf. Every VM is the libvirt domain e3t-<node>.
set -euo pipefail
source "$(dirname "$(readlink -f "$0")")/lab.conf"

# Re-exec under the libvirt group if this login session doesn't have it yet.
if ! id -nG | tr ' ' '\n' | grep -qx libvirt && getent group libvirt | grep -qw "${USER:-$(id -un)}"; then
  exec sg libvirt -c "$(printf '%q ' "$0" "$@")"
fi

V() { virsh -q -c "$LIBVIRT_URI" "$@"; }
die() { echo "error: $*" >&2; exit 1; }
dom() { echo "${DOMAIN_PREFIX}$1"; }
node_dir() { echo "$LAB_DIR/nodes/$1"; }
defined() { V dominfo "$(dom "$1")" &>/dev/null; }
known() { [[ -n "${ROLE[$1]:-}" ]] || die "no node named $1 (nodes: ${ALL_NODES[*]})"; }
ours() {   # domain names are host-wide: refuse to touch a same-named VM that belongs to something else
  defined "$1" || return 0
  local xml; xml="$(V dumpxml "$(dom "$1")")"
  [[ "$xml" == *"<source file='$(node_dir "$1")/"* ]] || die "a VM named $(dom "$1") exists but is not part of this lab"
}
state() { V domstate "$(dom "$1")" 2>/dev/null || echo undefined; }
running() { [[ "$(state "$1")" == "running" ]]; }
nodes_or_all() { local n; if [[ $# -gt 0 ]]; then for n in "$@"; do known "$n"; done; echo "$*"; else echo "${ALL_NODES[*]}"; fi; }
is_linux() { case "${ROLE[$1]}" in lb|web|app|db|ipam) return 0;; esac; return 1; }
is_tier() { case "${ROLE[$1]}" in lb|web|app|db) return 0;; esac; return 1; }   # on the fabric (a bond, a tier VLAN)
is_vyos() { ! is_linux "$1"; }
vyos_nodes_or_all() { local n out=(); for n in $(nodes_or_all "$@"); do is_vyos "$n" && out+=("$n"); done; echo "${out[*]:-}"; }
linux_nodes_or_all() { local n out=(); for n in $(nodes_or_all "$@"); do is_linux "$n" && out+=("$n"); done; echo "${out[*]:-}"; }
PY="${E3T_PYTHON:-$LAB_DIR/tests/.venv/bin/python}"; [[ -x "$PY" ]] || PY=python3
need_python() { [[ -n "${E3T_PYTHON:-}" || -x "$LAB_DIR/tests/.venv/bin/python" ]] || "$LAB_DIR/tests/setup.sh"; }

# ---- networks -------------------------------------------------------------------------------------------------------------
ensure_networks() {
  if ! V net-info "$OOB_NET" &>/dev/null; then V net-define "$LAB_DIR/networks/$OOB_NET.xml"; V net-autostart "$OOB_NET" >/dev/null; fi
  [[ "$(V net-info "$OOB_NET" | awk '/Active/{print $2}')" == "yes" ]] || V net-start "$OOB_NET"
}

# ---- point-to-point links (UDP tunnels between VMs) -----------------------------------------------------------------------
port_local() { echo $(( UDP_BASE + NODE_IDX[$1]*100 + $2 )); }            # the port a NIC listens on when it anchors a link
port_far()   { echo $(( UDP_BASE + 10000 + NODE_IDX[$1]*100 + $2 )); }    # ...and the one it sends to (the other end listens there)
node_ports() { seq 1 "${ROLE_PORTS[${ROLE[$1]}]}"; }
mac() { printf '%s:%02x:%02x' "$MAC_OUI" "${NODE_IDX[$1]}" "$2"; }
link_peer() {   # node port -> "peer_node peer_port kind end(1|2)" or "" when unwired
  local me="$1:$2" l a b k
  for l in "${LINKS[@]}"; do
    read -r a b k <<<"$l"
    [[ "$a" == "$me" ]] && { echo "${b%%:*} ${b##*:} $k 1"; return; }
    [[ "$b" == "$me" ]] && { echo "${a%%:*} ${a##*:} $k 2"; return; }
  done
  return 0
}

# ---- domain XML -------------------------------------------------------------------------------------------------------------
serial_xml() { cat <<X
    <serial type='tcp'>
      <source mode='bind' host='127.0.0.1' service='${CONSOLE_PORT[$1]}'/>
      <protocol type='raw'/>
      <log file='$(node_dir "$1")/console.log' append='on'/>
      <target port='0'/>
    </serial>
X
}
udp_nic_xml() {   # one <interface type='udp'>: the first end of a link listens on its own port_local, the second mirrors it
  local n="$1" p="$2" peer local remote pn pp k end
  peer="$(link_peer "$n" "$p")"; local="$(port_local "$n" "$p")"; remote="$(port_far "$n" "$p")"
  if [[ -n "$peer" ]]; then
    read -r pn pp k end <<<"$peer"
    [[ "$end" == "2" ]] && { local="$(port_far "$pn" "$pp")"; remote="$(port_local "$pn" "$pp")"; }
    echo "    <!-- eth$p: $k link to $pn eth$pp -->"
  else
    echo "    <!-- eth$p: unwired -->"
  fi
  cat <<X
    <interface type='udp'>
      <mac address='$(mac "$n" "$p")'/>
      <source address='127.0.0.1' port='$remote'>
        <local address='127.0.0.1' port='$local'/>
      </source>
      <model type='virtio'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='$(printf '0x%02x' $((3 + p)))' function='0x0'/>
    </interface>
X
}
nic_xml() {   # eth0 on the OOB network, or ethN on another libvirt network (NET_PORT)
  local n="$1" p="$2" net="$3"
  cat <<X
    <interface type='network'>
      <mac address='$(mac "$n" "$p")'/>
      <source network='$net'/>
      <model type='virtio'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='$(printf '0x%02x' $((3 + p)))' function='0x0'/>
    </interface>
X
}
domain_xml() {
  local n="$1" d p title ram vcpu; d="$(node_dir "$n")"
  if is_linux "$n"; then title="Alpine ${ROLE[$n]} ($n, ${RACK[$n]})"; ram="${LINUX_RAM_MIB[${ROLE[$n]}]}"; vcpu=1
  else title="VyOS ${ROLE[$n]} ($n, ${RACK[$n]})"; ram="$VYOS_RAM_MIB"; vcpu="$VYOS_VCPU"; fi
  cat <<X
<domain type='kvm' xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'>
  <name>$(dom "$n")</name>
  <uuid>$(uuidgen --sha1 --namespace @dns --name "$LAB_NAME.$n")</uuid>
  <title>$title</title>
  <description>$LAB_NAME lab node $n</description>
  <memory unit='MiB'>$ram</memory>
  <vcpu placement='static'>$vcpu</vcpu>$(is_vyos "$n" && printf "\n  <cputune><shares>%s</shares></cputune>" "${VYOS_CPU_SHARES:-100}")
  <cpu mode='host-passthrough' check='none'/>
  <os><type arch='x86_64' machine='pc'>hvm</type><boot dev='hd'/></os>
  <features><acpi/><apic/></features>
  <clock offset='utc'/>
  <on_poweroff>destroy</on_poweroff><on_reboot>restart</on_reboot><on_crash>restart</on_crash>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='$d/disk.qcow2'/>
      <target dev='vda' bus='virtio'/>
    </disk>
X
  if is_linux "$n"; then cat <<X
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='$d/seed.iso'/>
      <target dev='hda' bus='ide'/>
      <readonly/>
    </disk>
X
  fi
  echo "    <!-- eth0: OOB management ${MGMT_IP[$n]} -->"; nic_xml "$n" 0 "$OOB_NET"
  for p in $(node_ports "$n"); do udp_nic_xml "$n" "$p"; done
  if [[ -n "${NET_PORT[$n]:-}" ]]; then echo "    <!-- eth${NET_PORT[$n]%%:*}: libvirt network ${NET_PORT[$n]##*:} (DHCP) -->"; nic_xml "$n" "${NET_PORT[$n]%%:*}" "${NET_PORT[$n]##*:}"; fi
  serial_xml "$n"
  cat <<X
    <memballoon model='none'/>
  </devices>
  <!-- every virtio NIC reports 10 Gb/s full duplex: LACP (802.3ad bonding) refuses ports whose speed/duplex is unknown,
       which is what virtio reports by default -->
  <qemu:commandline>
    <qemu:arg value='-global'/><qemu:arg value='virtio-net-pci.speed=10000'/>
    <qemu:arg value='-global'/><qemu:arg value='virtio-net-pci.duplex=full'/>
  </qemu:commandline>
</domain>
X
}

# ---- build ---------------------------------------------------------------------------------------------------------------
linux_seed() {   # cloud-init NoCloud seed: OOB address, lab / lab, sshd; the bond, VLAN and services come from tools/server_net.py
  local n="$1" d; d="$(node_dir "$n")"
  printf 'instance-id: %s-001\nlocal-hostname: %s\n' "$n" "$n" > "$d/meta-data"
  cat > "$d/network-config" <<U
version: 2
ethernets:
  oob:
    match: { macaddress: "$(mac "$n" 0)" }
    set-name: eth0
    addresses: [${MGMT_IP[$n]}/24]
    routes: [{ to: 10.0.0.0/8, via: $OOB_GATEWAY }]
U
  cat > "$d/user-data" <<U
#cloud-config
# $n (${ROLE[$n]}, ${RACK[$n]}): eth0 = OOB ${MGMT_IP[$n]}; eth1/eth2 = the links to its leaf / border pair (an LACP bond, tools/server_net.py)
hostname: $n
users:
  - name: lab
    plain_text_passwd: lab
    lock_passwd: false
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/sh
ssh_pwauth: true
write_files:
  - path: /etc/doas.d/lab.conf
    permissions: "0400"
    content: "permit nopass lab\n"
  - path: /etc/motd
    content: "$n — $LAB_NAME ${ROLE[$n]} in ${RACK[$n]}. OOB eth0 ${MGMT_IP[$n]}.\n"
runcmd:
  - rc-update add sshd default
  - rc-service sshd restart
  - rc-update add node-exporter default
  - rc-service node-exporter restart
  - [sh, -c, "for i in eth1 eth2; do ip link set \$i up 2>/dev/null; done"]
U
  genisoimage -quiet -o "$d/seed.iso.tmp" -V cidata -J -r "$d/user-data" "$d/meta-data" "$d/network-config" && mv -f "$d/seed.iso.tmp" "$d/seed.iso"
}
build() {
  local n="$1" d base; d="$(node_dir "$n")"; mkdir -p "$d"
  if [[ "${ROLE[$n]}" == ipam ]]; then base="$IPAM_IMAGE"
  elif is_linux "$n"; then base="$LINUX_IMAGE"
  else base="$VYOS_IMAGE"; [[ -f "$d/vyos_config.txt" ]] || die "$d/vyos_config.txt missing (run: lab.sh gen)"; fi
  [[ -f "$base" ]] || die "base image not found: $base$(is_linux "$n" && echo ' (run: tools/build_image.sh or tools/build_ipam_image.sh)')"
  [[ -f "$d/disk.qcow2" ]] || { echo "[$n] creating overlay disk on $(basename "$base")"; qemu-img create -q -f qcow2 -b "$base" -F qcow2 "$d/disk.qcow2"; }
  is_linux "$n" && linux_seed "$n"
  domain_xml "$n" > "$d/domain.xml"
  V define "$d/domain.xml" >/dev/null
}

# ---- readiness / day-0 ------------------------------------------------------------------------------------------------------
ssh_ready() { timeout 8 bash -c "exec 3<>/dev/tcp/${MGMT_IP[$1]}/22" 2>/dev/null; }
bootstrap_vyos() {   # day-0 over the serial console (nodes/<n>/vyos_config.txt), logged to nodes/<n>/bootstrap.log
  local n="$1" d; d="$(node_dir "$n")"
  {
    echo "[$n] waiting for the VyOS login prompt..."
    python3 "$LAB_DIR/tools/vyos_console.py" wait 127.0.0.1 "${CONSOLE_PORT[$n]}" 600
    echo "[$n] applying day-0 config"
    python3 "$LAB_DIR/tools/vyos_console.py" push 127.0.0.1 "${CONSOLE_PORT[$n]}" "$d/vyos_config.txt"
    for _ in $(seq 30); do ssh_ready "$n" && break; sleep 5; done
    ssh_ready "$n" && echo "[$n] ready: ssh vyos@${MGMT_IP[$n]} (vyos)" || echo "[$n] warning: SSH not answering yet"
  } > "$d/bootstrap.log" 2>&1
}

# ---- commands --------------------------------------------------------------------------------------------------------------
cmd_gen() {        # render nodes/<n>/vyos_config.txt from lab.conf (tools/render.py via the inventory)
  python3 "$LAB_DIR/tools/gen_configs.py"
}
cmd_up() {
  ensure_networks
  local n; for n in $(nodes_or_all "$@"); do
    ours "$n"; defined "$n" || build "$n"
    [[ -f "$(node_dir "$n")/console.log" ]] || { touch "$(node_dir "$n")/console.log"; chmod 644 "$(node_dir "$n")/console.log"; }
    if running "$n"; then echo "[$n] already running"; else V start "$(dom "$n")" >/dev/null; echo "[$n] started (console: 127.0.0.1:${CONSOLE_PORT[$n]})"; fi
  done
}
cmd_down() {       # VyOS: ACPI shutdown (config saved by bootstrap / configure); Linux VMs: ACPI too, then power off
  local n pids=(); for n in $(nodes_or_all "$@"); do
    { running "$n" || { echo "[$n] not running"; exit 0; }
      V shutdown "$(dom "$n")" >/dev/null 2>&1 || true; for _ in $(seq 30); do running "$n" || break; sleep 2; done
      running "$n" && V destroy "$(dom "$n")" >/dev/null; echo "[$n] stopped"; } &
    pids+=($!)
  done
  wait "${pids[@]}" 2>/dev/null || true
}
cmd_bootstrap() {  # push the day-0 config to VyOS nodes over their serial consoles, all in parallel
  local n pids=() names=() i rc=0
  for n in $(vyos_nodes_or_all "$@"); do
    running "$n" || { echo "[$n] not running, skipped"; continue; }
    bootstrap_vyos "$n" & pids+=($!); names+=("$n"); echo "[$n] bootstrapping in the background (log: nodes/$n/bootstrap.log)"
  done
  for i in "${!pids[@]}"; do
    wait "${pids[$i]}" || true; n="${names[$i]}"
    if grep -qE 'Invalid|Commit failed|failed|Error|!!|Traceback|timeout' "$(node_dir "$n")/bootstrap.log"; then
      echo "[$n] FAIL — see nodes/$n/bootstrap.log"; grep -nE 'Invalid|Commit failed|failed|Error|!!|Traceback|timeout' "$(node_dir "$n")/bootstrap.log" | head -5; rc=1
    else echo "[$n] OK $(tail -1 "$(node_dir "$n")/bootstrap.log")"; fi
  done
  [[ ${#names[@]} -eq 0 ]] && return $rc
  # then what the CLI can't express (tools/vyos_files.py), once SSH answers
  need_python; cmd_wait "${names[@]}" >/dev/null; "$PY" "$LAB_DIR/tools/vyos_files.py" push "${names[@]}" || rc=1
  return $rc
}
cmd_configure() {  # (re)apply nodes/<n>/vyos_config.txt over SSH — idempotent; Linux VMs: bond, VLAN, routes (tools/server_net.py)
  need_python
  # the segment bonds are rendered `disable`d and only the watcher installed by vyos_files.py raises them: never push
  # configs with a vyos_files.py that cannot run (0.4.0 development: every bond in the lab stayed down)
  [[ -z "$(vyos_nodes_or_all "$@")" ]] || "$PY" "$LAB_DIR/tools/vyos_files.py" check >/dev/null || die "tools/vyos_files.py is broken: nothing pushed"
  local n lx=() vy=(); for n in $(vyos_nodes_or_all "$@"); do "$PY" "$LAB_DIR/tools/vyos_push.py" "${MGMT_IP[$n]}" "$(node_dir "$n")/vyos_config.txt" | sed "s/^/[$n] /"; vy+=("$n"); done
  [[ ${#vy[@]} -eq 0 ]] || "$PY" "$LAB_DIR/tools/vyos_files.py" push "${vy[@]}"
  for n in $(linux_nodes_or_all "$@"); do is_tier "$n" && lx+=("$n"); done
  [[ ${#lx[@]} -eq 0 ]] || "$PY" "$LAB_DIR/tools/server_net.py" push "${lx[@]}"
}
cmd_wait() {       # block until SSH answers
  local n; for n in $(nodes_or_all "$@"); do
    for _ in $(seq 60); do ssh_ready "$n" && break; sleep 5; done
    ssh_ready "$n" && echo "[$n] SSH ready" || echo "[$n] SSH NOT ready"
  done
}
cmd_rebuild() {    # re-define domains (and Linux seeds) from lab.conf without touching disks
  local n; for n in $(nodes_or_all "$@"); do running "$n" && die "$n is running; stop it first"; defined "$n" && V undefine "$(dom "$n")" >/dev/null; build "$n"; echo "[$n] redefined"; done
}
cmd_clean() {      # destroy VMs and delete overlay disks (base images untouched)
  local n; for n in $(nodes_or_all "$@"); do
    ours "$n"; running "$n" && V destroy "$(dom "$n")" >/dev/null; defined "$n" && V undefine "$(dom "$n")" >/dev/null
    rm -f "$(node_dir "$n")"/{disk.qcow2,seed.iso,seed.iso.tmp,meta-data,user-data,network-config,domain.xml,console.log,bootstrap.log}
    echo "[$n] removed"
  done
}
cmd_status() {     # the VM column is the libvirt domain (the Lab Hub maps nodes to domains through it)
  printf '%-8s %-7s %-10s %-11s %-11s %-11s %-6s %-7s %-7s\n' NODE ROLE STATE VM MGMT-IP LOOPBACK AS RACK CONSOLE
  local n; for n in "${ALL_NODES[@]}"; do
    printf '%-8s %-7s %-10s %-11s %-11s %-11s %-6s %-7s %-7s\n' "$n" "${ROLE[$n]}" "$(state "$n")" "$(dom "$n")" "${MGMT_IP[$n]}" "${LOOPBACK[$n]:--}" "${BGP_AS[$n]:--}" "${RACK[$n]}" "${CONSOLE_PORT[$n]}"
  done
  echo; echo "links (point-to-point UDP tunnels):"
  local l a b k; for l in "${LINKS[@]}"; do read -r a b k <<<"$l"; printf '  %-8s eth%-2s <->  %-8s eth%-2s  %s\n' "${a%%:*}" "${a##*:}" "${b%%:*}" "${b##*:}" "$k"; done
}
cmd_inventory() {  # the lab as JSON (tools/inventory.py) — what the renderer, the tests and the portal read
  {
    echo "LAB $LAB_NAME $OOB_NET $OOB_GATEWAY $FABRIC_MTU $DOMAIN_PREFIX $MAX_PATHS $EVPN_RT_AS $ANYCAST_MAC $VRF_MAX_PATHS"
    echo "NMS ${NMS_IP:--}"; echo "SVI $SVI_HOST_BASE $SVI_BORDER_BASE"
    echo "WAN $WAN_PREFIX $WAN_CLIENTS $SYNC_NET"; echo "VIP $WEB_VIP"
    local v n p peer
    for v in "${VRFS[@]}"; do echo "VRF $v ${VRF_TABLE[$v]} ${VRF_L3VNI[$v]} ${VRF_L3VLAN[$v]} ${VRF_PROBE[$v]}"; done
    for v in "${VLANS[@]}"; do echo "VLAN $v"; done
    for v in "${TRANSIT[@]}"; do echo "TRANSIT $v"; done
    for v in "${LINUX_NET[@]}"; do echo "LINUXNET $v"; done
    for n in "${!FW_ES[@]}"; do echo "FWES $n ${FW_ES[$n]}"; done
    for v in "${FW_POLICY[@]}"; do echo "FWPOLICY $v"; done
    echo "ESCONF $ES_SYS_MAC_PREFIX ${ES_DF_PREF[*]}"
    for n in "${ALL_NODES[@]}"; do
      echo "NODE $n ${ROLE[$n]} ${NODE_IDX[$n]} ${MGMT_IP[$n]} ${CONSOLE_PORT[$n]} ${BGP_AS[$n]:--} ${LOOPBACK[$n]:--} ${RACK[$n]} $(dom "$n") $(mac "$n" 0)"
      for p in $(node_ports "$n"); do peer="$(link_peer "$n" "$p")"; echo "PORT $n eth$p $(mac "$n" "$p") ${peer:--}"; done
      [[ -n "${NET_PORT[$n]:-}" ]] && echo "NET $n eth${NET_PORT[$n]%%:*} ${NET_PORT[$n]##*:} $(mac "$n" "${NET_PORT[$n]%%:*}")"
      true
    done
  } | python3 "$LAB_DIR/tools/inventory.py"
}
cmd_console() { local n="${1:?node}"; known "$n"; running "$n" || die "$n is not running"; echo "Connecting to $n console (exit: Ctrl-] then q)"; socat -,raw,echo=0,escape=0x1d "tcp:127.0.0.1:${CONSOLE_PORT[$n]}"; }
cmd_ssh() {
  local n="${1:?node}"; shift || true; known "$n"
  if is_linux "$n"; then ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o PubkeyAuthentication=no "lab@${MGMT_IP[$n]}" "$@"
  else ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "vyos@${MGMT_IP[$n]}" "$@"; fi
}
cmd_show() { local n="${1:?node}"; shift; known "$n"; is_vyos "$n" || die "$n is not a VyOS node"; need_python; "$PY" "$LAB_DIR/tools/vyos_cmd.py" "${MGMT_IP[$n]}" "$*"; }
cmd_log() { tail -n "${2:-50}" -f "$(node_dir "${1:?node}")/console.log"; }
cmd_verify() { need_python; "$PY" "$LAB_DIR/tools/verify.py" "$@"; }   # control plane + data plane, end to end
cmd_webapp() {     # the portal (webapp/, FastAPI) on :8119; as a service: evpn-3tier-webapp (user unit)
  [[ -x "$LAB_DIR/webapp/.venv/bin/python" ]] || "$LAB_DIR/webapp/setup.sh"
  cd "$LAB_DIR/webapp" && exec .venv/bin/python app.py
}
cmd_chaos() { need_python; "$PY" "$LAB_DIR/tools/chaos.py" "$@"; }   # a component fails under load (tools/chaos.py)
cmd_db() { need_python; "$PY" "$LAB_DIR/tools/dbctl.py" "$@"; }       # status | promote | reseed | switchover
cmd_test() { need_python; exec "$LAB_DIR/tests/run.sh" "$@"; }       # the Robot Framework suites (results/<timestamp>/)
cmd_selfservice() {   # the self-service app (selfservice/, Django) on :8120; as a service: evpn-3tier-selfservice
  [[ -x "$LAB_DIR/selfservice/.venv/bin/gunicorn" ]] || "$LAB_DIR/selfservice/setup.sh"
  cd "$LAB_DIR/selfservice" && exec .venv/bin/gunicorn selfservice.wsgi --bind 0.0.0.0:"${SELFSERVICE_PORT:-8120}" --workers 2 --access-logfile - --error-logfile -
}
cmd_ipam() { need_python; "$PY" "$LAB_DIR/tools/phpipam_load.py" "$@"; }   # load the lab's subnets and addresses into phpIPAM
cmd_services() { need_python; "$PY" "$LAB_DIR/tools/services.py" "$@"; }   # the application: db, app, web, lb (tools/services.py)
cmd_fw-failover() { need_python; "$PY" "$LAB_DIR/tools/fw_failover.py" "$@"; }   # power fw1 off under a live connection, then back
cmd_check-wiring() { need_python; "$PY" "$LAB_DIR/tools/check_wiring.py"; }   # every link reaches the peer lab.conf says

usage() {
  cat <<U
usage: $(basename "$0") <command> [node...]
  gen                render nodes/<n>/vyos_config.txt from lab.conf
  up [node..]        create (if needed) and start VMs                       (default: all)
  bootstrap [node..] push the day-0 config to VyOS nodes over the console (first boot only; parallel)
  configure [node..] re-apply nodes/<n>/vyos_config.txt over SSH (idempotent); Linux VMs: bond, VLAN, routes
  wait [node..]      wait until SSH answers
  down [node..]      stop VMs (ACPI shutdown, VyOS config saved)
  status             nodes, VMs, addresses, links, consoles
  inventory          the lab as JSON
  console <node>     attach to the serial console
  ssh <node> [cmd]   ssh to a node (VyOS vyos/vyos, Linux lab/lab)
  show <node> <cmd>  run a VyOS operational command (e.g. show bgp summary)
  log <node> [n]     follow a node's console log
  check-wiring       prove every link reaches the peer port lab.conf names (by MAC; bond legs by LACP partner)
  verify [--quick]   fabric, firewall sessions and preference, data plane, the firewall policy matrix (--quick: no matrix)
  services [--reseed] [node..]  deploy the application: PostgreSQL primary + replica, app, nginx, HAProxy + keepalived
  selfservice        the self-service app on :8120 (Django: request workflows on the lab); as a service: evpn-3tier-selfservice
  ipam [--check]     load every subnet, VLAN, VRF and address of the lab into phpIPAM (node ipam, http://10.18.0.5/)
  webapp             the portal on :8119 (state, runs, /shop/, /metrics, /api/sd, /docs); as a service: evpn-3tier-webapp
  chaos <scenario>   under load from wan: lb | web | app | db | leaf | border | fw | all — fail it, restore, count losses
  db [status|promote N|reseed N FROM|switchover N]   PostgreSQL roles
  test [robot args]  the Robot suites (~35 min); --exclude disruptive: the read-only ones (~3 min)
  fw-failover        one TCP connection web1 -> app1 while fw1 is powered off and back: gaps, survival
  rebuild [node..]   re-generate domain XML / Linux seeds (keeps disks)
  clean [node..]     stop, undefine and delete overlay disks
nodes: ${ALL_NODES[*]}
U
}

cmd="${1:-}"; shift || true
case "$cmd" in
  gen|up|down|bootstrap|configure|wait|status|inventory|console|ssh|show|log|rebuild|clean|check-wiring|verify|fw-failover|services|chaos|db|test|webapp|ipam|selfservice) "cmd_$cmd" "$@" ;;
  *) usage; exit 1 ;;
esac
