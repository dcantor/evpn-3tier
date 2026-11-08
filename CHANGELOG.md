# Changelog

All notable changes to this lab are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.3.0] - 2026-11-08

### Added
- Phase 3: the application (app/): Order Desk — a page (nginx on web1 / web2) and an order API (app/app.py on app1 /
  app2, psycopg 3) on PostgreSQL 17 (db1 primary, db2 streaming replica, hot standby). HAProxy 3.2 on lb1 / lb2 in front
  of the web tier only (TLS, health checks), keepalived holding the web VIP 172.16.10.10. Each answer names the lb, web,
  app and db that served it.
- `./lab.sh services [--reseed] [node...]` (tools/services.py) deploys it, idempotently.
- `verify`: the application from outside (VIP placement, every web and app server used, the primary, the client
  address, an order written and replicated) and the VIP in the policy matrix — 65 checks.

## [0.2.0] - 2026-11-08

### Added
- Phase 2: the firewall policy (lab.conf `FW_POLICY`) as VyOS zones on fw1 / fw2 — per-VRF zones, SYNC, MGMT, LOCAL;
  replies pass, only the listed flows start, the rest dropped and logged.
- conntrack-sync between the firewalls over the sync link (now 172.31.255.0/29), tied to VRRP group FWSYNC.
- `./lab.sh verify`: 57 checks — fabric sessions, BFD, Ethernet Segments, firewall sessions, fw1 as next hop, VRRP
  master, data plane, and the policy matrix (temporary listeners; every flow open exactly when the policy allows it).
- `./lab.sh fw-failover`: fw1 powered off and back under a live connection; measured 1.8 s pause each way, no reset.
- tools/labssh.py: shared SSH helper (retries sshd's MaxStartups throttling).

## [0.1.0] - 2026-11-08

### Added
- Phase 0: `lab.conf` (19 VMs, 35 links), `lab.sh` (gen / up / down / bootstrap / configure / status / inventory / ssh /
  show / check-wiring / rebuild / clean), the Alpine base image builder (`tools/build_image.sh`: haproxy, keepalived,
  nginx, python3 + psycopg, PostgreSQL 17), the OOB network `e3t-oob` (10.18.0.0/24).
- Phase 1 (fabric): eBGP unnumbered underlay with BFD, BGP EVPN overlay, VRFs web / app / db / outside, anycast
  gateways, EVPN multihoming for every server, load balancer and firewall bond; the firewalls' transit VLANs and eBGP
  per VRF; wan with a default route and the test clients 198.51.100.0/24.
- `tools/host_cmd.py`: a shell command on any node over SSH.

### Fixed (found while building)
- A border sent the firewalls the other border's transit address as a /32 (EVPN type-2 via `advertise-svi-ip`): fw2
  reached border2 *through* border1, routed with TTL - 1, so single-hop BFD and eBGP failed. Borders now export only
  the tier subnets and the default route to the firewalls (prefix-list TO-FW).
- VRF outside's L3VNI VLAN 4099 is outside 802.1Q's 1-4094 and VyOS dropped it silently: now 4090, and the inventory
  refuses out-of-range VLANs.
