#!/usr/bin/env bash
# Build images/e3t-alpine.qcow2 — the one base image of every Linux VM in the lab (lb1-2, web1-2, app1-2, db1-2):
# Alpine 3.22 (NoCloud cloud-init image) with everything any tier needs pre-installed, so no VM needs the internet:
#   lb   haproxy, keepalived          web  nginx           app  python3 + psycopg
#   db   postgresql17                 all  iperf3, tcpdump, mtr, curl, arping, ethtool, openssl, node-exporter
# Nothing is enabled here: lab.sh's per-node seed and tools/server_net.py turn on what each role runs.
# A throw-away builder VM on libvirt's NAT network (default) installs the packages, cleans cloud-init and powers off;
# its disk becomes the base image. Re-run to rebuild (only with every lab VM off: they are overlays on it).
#   tools/build_image.sh [alpine-nocloud.qcow2]
set -euo pipefail
LAB="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
SRC="${1:-/home/dcantor/srv6-core/images/alpine-3.22.4-nocloud.qcow2}"; OUT="$LAB/images/e3t-alpine.qcow2"
NAME=e3t-image-builder
V() { virsh -q -c qemu:///system "$@"; }
[[ -f "$SRC" ]] || { echo "source image not found: $SRC" >&2; exit 1; }
B="$LAB/images/.builder"; rm -rf "$B"; mkdir -p "$B"
qemu-img create -q -f qcow2 -b "$SRC" -F qcow2 "$B/disk.qcow2" 3G
cat > "$B/user-data" <<'U'
#cloud-config
package_update: true
packages: [iperf3, tcpdump, mtr, curl, iputils-arping, ethtool, openssl, prometheus-node-exporter,
           haproxy, keepalived, nginx, python3, py3-psycopg, postgresql17, postgresql17-client, postgresql17-contrib]
users:
  - name: lab
    plain_text_passwd: lab
    lock_passwd: false
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/sh
ssh_pwauth: true
runcmd:
  - rc-update add sshd default
  - sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication yes/' /etc/ssh/sshd_config
  - [sh, -c, "for p in haproxy keepalived nginx postgresql python3; do apk info -e $p >/dev/null && echo E3T-PKG-OK $p || echo E3T-PKG-MISSING $p; done > /dev/console"]
  - [sh, -c, "rc-update del haproxy default; rc-update del keepalived default; rc-update del nginx default; rc-update del postgresql default; true"]
  - cloud-init clean --logs
  - poweroff
U
printf 'instance-id: builder-001\nlocal-hostname: builder\n' > "$B/meta-data"
genisoimage -quiet -o "$B/seed.iso" -V cidata -J -r "$B/user-data" "$B/meta-data"
cat > "$B/domain.xml" <<X
<domain type='kvm'><name>$NAME</name><memory unit='MiB'>768</memory><vcpu>2</vcpu><cpu mode='host-passthrough' check='none'/>
<os><type arch='x86_64' machine='pc'>hvm</type><boot dev='hd'/></os><features><acpi/><apic/></features><on_poweroff>destroy</on_poweroff>
<devices><emulator>/usr/bin/qemu-system-x86_64</emulator>
<disk type='file' device='disk'><driver name='qemu' type='qcow2'/><source file='$B/disk.qcow2'/><target dev='vda' bus='virtio'/></disk>
<disk type='file' device='cdrom'><driver name='qemu' type='raw'/><source file='$B/seed.iso'/><target dev='hda' bus='ide'/><readonly/></disk>
<interface type='network'><source network='default'/><model type='virtio'/></interface>
<serial type='pty'><log file='$B/console.log' append='on'/><target port='0'/></serial><memballoon model='none'/></devices></domain>
X
touch "$B/console.log"; chmod 666 "$B/console.log"
V destroy "$NAME" >/dev/null 2>&1 || true; V undefine "$NAME" >/dev/null 2>&1 || true
V define "$B/domain.xml" >/dev/null; V start "$NAME" >/dev/null
echo "builder started (NAT network, installing the packages) ..."
for _ in $(seq 120); do [[ "$(V domstate "$NAME")" == "running" ]] || break; sleep 5; done
[[ "$(V domstate "$NAME")" == "running" ]] && { echo "builder still running after 10 min — see $B/console.log" >&2; exit 1; }
V undefine "$NAME" >/dev/null
if grep -q "E3T-PKG-MISSING" "$B/console.log" || ! grep -q "E3T-PKG-OK" "$B/console.log"; then
  echo "packages missing — see $B/console.log:" >&2; grep -a "E3T-PKG" "$B/console.log" >&2 || true; exit 1
fi
qemu-img convert -O qcow2 -c "$B/disk.qcow2" "$OUT.tmp" && mv -f "$OUT.tmp" "$OUT" && rm -rf "$B"
echo "built $OUT ($(du -h "$OUT" | cut -f1)); user lab / lab"
