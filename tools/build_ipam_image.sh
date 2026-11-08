#!/usr/bin/env bash
# Build images/e3t-ipam.qcow2 — the external IPAM VM (node `ipam`): Alpine 3.22 + Docker, with phpIPAM and MariaDB pulled
# in at build time (the VM needs no internet) and started by compose at every boot. Nothing about the lab is in the image:
# tools/phpipam_load.py fills phpIPAM through its API once the VM is up. nginx in front: HTTPS :443 (the API: phpIPAM's
# token auth is HTTPS only), HTTP :80 (the UI); phpIPAM listens on 127.0.0.1:8080 and trusts nginx's X-Forwarded-Proto.
#   phpipam/phpipam-www  v1.8.3   (the web UI + REST API, Apache on :80)
#   mariadb              11.8.8   (its database; data in the docker volume ipam-db)
# A throw-away builder VM on libvirt's NAT network (default) installs and pulls, then powers off; its disk is the image.
#   tools/build_ipam_image.sh [alpine-nocloud.qcow2]
set -euo pipefail
LAB="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
SRC="${1:-/home/dcantor/srv6-core/images/alpine-3.22.4-nocloud.qcow2}"; OUT="$LAB/images/e3t-ipam.qcow2"
PHPIPAM=phpipam/phpipam-www:v1.8.3; MARIADB=mariadb:11.8.8
NAME=e3t-ipam-builder
V() { virsh -q -c qemu:///system "$@"; }
B="$LAB/images/.ipam-builder"; rm -rf "$B"; mkdir -p "$B"
qemu-img create -q -f qcow2 -b "$SRC" -F qcow2 "$B/disk.qcow2" 8G
cat > "$B/user-data" <<U
#cloud-config
package_update: true
packages: [docker, docker-cli-compose, curl, e2fsprogs-extra, cloud-utils-growpart, nginx, openssl]
users:
  - name: lab
    plain_text_passwd: lab
    lock_passwd: false
    shell: /bin/sh
ssh_pwauth: true
growpart: {mode: auto, devices: ["/"]}
resize_rootfs: true
write_files:
  - path: /opt/ipam/compose.yml
    content: |
      # phpIPAM for evpn-3tier (tools/build_ipam_image.sh). Started by /etc/local.d/ipam.start at every boot.
      services:
        db:
          image: $MARIADB
          restart: unless-stopped
          environment:
            MARIADB_ROOT_PASSWORD: ipam-root
            MARIADB_DATABASE: phpipam
            MARIADB_USER: phpipam
            MARIADB_PASSWORD: phpipam
          volumes: [ipam-db:/var/lib/mysql]
        web:
          image: $PHPIPAM
          restart: unless-stopped
          depends_on: [db]
          ports: ["127.0.0.1:8080:80"]         # only nginx (below) reaches it: nobody can fake the X-Forwarded-Proto it trusts
          environment:
            TZ: UTC
            IPAM_TRUST_X_FORWARDED: "true"     # nginx terminates TLS; phpIPAM's API (ssl_token) needs to know it was HTTPS
            IPAM_DATABASE_HOST: db
            IPAM_DATABASE_USER: phpipam
            IPAM_DATABASE_PASS: phpipam
            IPAM_DATABASE_NAME: phpipam
            IPAM_DATABASE_WEBHOST: "%"
      volumes:
        ipam-db: {}
  - path: /etc/local.d/ipam.start
    permissions: "0755"
    content: |
      #!/bin/sh
      # wait for docker, then phpIPAM + MariaDB
      for i in \$(seq 30); do docker info >/dev/null 2>&1 && break; sleep 2; done
      cd /opt/ipam && docker compose up -d >/var/log/ipam-compose.log 2>&1
      # the TLS certificate (self-signed, made once, on the first boot: every VM gets its own key)
      if [ ! -s /etc/ipam-tls/ipam.crt ]; then
        mkdir -p /etc/ipam-tls && openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj /CN=ipam.evpn-3tier.lab \\
          -addext "subjectAltName=DNS:ipam.evpn-3tier.lab,IP:\$(ip -4 -o addr show eth0 | awk '{print \$4}' | cut -d/ -f1)" \\
          -keyout /etc/ipam-tls/ipam.key -out /etc/ipam-tls/ipam.crt 2>/dev/null && chmod 600 /etc/ipam-tls/ipam.key
      fi
      rc-service nginx restart >/dev/null 2>&1
  - path: /etc/nginx/http.d/default.conf
    content: |
      # phpIPAM behind nginx (tools/build_ipam_image.sh): HTTPS :443 (the API needs it: token auth is HTTPS only),
      # HTTP :80 (the web UI; the Lab Hub's relay to the LAN). phpIPAM itself listens on 127.0.0.1:8080 only.
      server {
          listen 443 ssl default_server;
          ssl_certificate     /etc/ipam-tls/ipam.crt;
          ssl_certificate_key /etc/ipam-tls/ipam.key;
          client_max_body_size 20m;
          location / {
              proxy_pass http://127.0.0.1:8080;
              proxy_set_header Host \$host;
              proxy_set_header X-Forwarded-Proto https;
              proxy_set_header X-Forwarded-For \$remote_addr;
          }
      }
      server {
          listen 80 default_server;
          client_max_body_size 20m;
          location / {
              proxy_pass http://127.0.0.1:8080;
              proxy_set_header Host \$host;
              proxy_set_header X-Forwarded-Proto http;
              proxy_set_header X-Forwarded-For \$remote_addr;
          }
      }
  - path: /etc/doas.d/lab.conf
    permissions: "0400"
    content: "permit nopass lab\n"
runcmd:
  - rc-update add sshd default
  - sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication yes/' /etc/ssh/sshd_config
  - rc-update add cgroups boot           # OpenRC: docker will not start without the cgroups service
  - rc-update add docker default
  - rc-update add local default
  - rc-service cgroups start
  - rc-service docker start
  - [sh, -c, "df -h / > /dev/console"]
  - [sh, -c, "for i in \$(seq 30); do docker info >/dev/null 2>&1 && break; sleep 2; done; docker pull $PHPIPAM && docker pull $MARIADB && echo E3T-PULL-OK > /dev/console || echo E3T-PULL-FAILED > /dev/console"]
  - [sh, -c, "docker run --rm --entrypoint sh $PHPIPAM -c 'ls /phpipam/db/SCHEMA.sql && grep -n api_allow_unsafe /phpipam/config.docker.php' > /dev/console 2>&1; true"]
  - rc-service docker stop
  - cloud-init clean --logs
  - poweroff
U
printf 'instance-id: ipam-builder-001\nlocal-hostname: ipam-builder\n' > "$B/meta-data"
genisoimage -quiet -o "$B/seed.iso" -V cidata -J -r "$B/user-data" "$B/meta-data"
cat > "$B/domain.xml" <<X
<domain type='kvm'><name>$NAME</name><memory unit='MiB'>1536</memory><vcpu>2</vcpu><cpu mode='host-passthrough' check='none'/>
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
echo "builder started (NAT network: Docker, $PHPIPAM, $MARIADB) ..."
for _ in $(seq 180); do [[ "$(V domstate "$NAME")" == "running" ]] || break; sleep 5; done
[[ "$(V domstate "$NAME")" == "running" ]] && { echo "builder still running after 15 min — see $B/console.log" >&2; exit 1; }
V undefine "$NAME" >/dev/null
grep -a "E3T-PULL-OK" "$B/console.log" >/dev/null || { echo "image pull failed — see $B/console.log" >&2; grep -a E3T "$B/console.log" >&2 || true; exit 1; }
grep -aE "SCHEMA.sql|api_allow_unsafe" "$B/console.log" | head -3 || true
qemu-img convert -O qcow2 -c "$B/disk.qcow2" "$OUT.tmp" && mv -f "$OUT.tmp" "$OUT" && rm -rf "$B"
echo "built $OUT ($(du -h "$OUT" | cut -f1)); phpIPAM $PHPIPAM, MariaDB $MARIADB; user lab / lab"
