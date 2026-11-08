# Changelog

All notable changes to this lab are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.7.0] - 2026-11-08

### Added
- An external IPAM: node `ipam` (20th VM, 10.18.0.5, Alpine + Docker) running phpIPAM 1.8.3 + MariaDB 11.8 behind nginx
  (HTTPS for the API), built by tools/build_ipam_image.sh. `./lab.sh ipam` (tools/phpipam_load.py) loads every subnet,
  VLAN, VRF and address reservation of the lab from lab.conf, idempotently: 4 VRFs, 11 VLANs, 20 subnets, 102
  addresses. Not synced with Nautobot (later).
- managed deletions in the VyOS pipelines (Gitea evpn-3tier-switches / -firewalls): what the templates no longer render is
  deleted from the device, protected paths excepted, capped at 25 per node.

### Fixed
- verify retries `show vrrp` and the portal samples it every 30 s (two readers of keepalived's dump collided).

## [0.6.1] - 2026-11-08

### Changed
- The deploy pipeline is split into four Gitea repos with one pipeline each: evpn-3tier-switches (spines, leaves,
  borders, wan), -firewalls, -loadbalancers, -servers. Each inventory lists only its devices; the runner `ansible` is
  registered at user scope and serves all four. lab/evpn-3tier-ansible is archived. First runs: changed=0, 61/61.

## [0.6.0] - 2026-11-08

### Added
- CI/CD from Nautobot with Ansible (Gitea lab/evpn-3tier-ansible, runner `ansible`): `tools/nautobot_seed.py` puts the
  lab into Nautobot (location / namespace / tenant group evpn-3tier: devices, interfaces, MACs, cables, IPAM, VRFs,
  VLANs, a config context); the pipeline renders the Ansible inventory from Nautobot's GraphQL API, commits it to Gitea
  every run, renders every VyOS config with Jinja2 (identical to tools/render.py), runs check mode, applies and verifies.

### Fixed
- Spines: only `... l2vpn-evpn attribute-unchanged next-hop` (VyOS lists only the deepest line, so the bare one was
  re-sent by a line-by-line comparer on every run). The firewalls' eth3 description is set once (two lines set it).

## [0.5.1] - 2026-11-08

### Fixed
- Suite 06's limits: requests in flight on lb1 / web1 when it loses power are lost (only new connections are rerouted);
  the first full run from the portal lost 4 and 1 against a limit of 0. Now at most 8 / 5 reads, still no gap over 1 s.
  Full run from the portal: 14 of 14.

## [0.5.0] - 2026-11-08

### Added
- Phase 5: the portal (webapp/, :8119, user unit evpn-3tier-webapp, `./lab.sh webapp`): the request path right now, the
  live topology, load balancers, firewalls and policy, the fabric; runs (verify, failure scenarios with per-phase tables,
  the firewall failover, Robot suites, redeploy); /shop/ (the Order Desk relayed through wan), /haproxy/<lb>/ (stats),
  /metrics and /api/sd for the NMS.
- wan relays its OOB address :8443 to the web VIP from the test clients' address (tools/vyos_files.py): how the portal
  reaches the application.
- The Lab Hub lists the lab (labs.json); the NMS scrapes it (lab-portal 0.53.0: job, 9 alert rules, Grafana dashboard).

### Changed
- The Order Desk page uses relative API paths (it works at / on the VIP and under /shop/ on the portal).

### Fixed
- dbctl reseed: OpenRC's stale "crashed" state made `rc-service postgresql start` a no-op, so a re-seeded replica never
  started (the full Robot run's database failover failed on it); it now zaps the state and stops any leftover
  postmaster, keeps one primary_conninfo, and shows PostgreSQL's log when the replica does not stream. Commands to the
  db nodes are quoted with shlex.

## [0.4.0] - 2026-11-08

### Added
- Phase 4: `./lab.sh chaos <lb|web|app|db|leaf|border|fw|all>` (tools/chaos.py) — a component powered off for 20 s
  under load from wan (an HTTPS read every 100 ms, a write every second, fresh connections) and back; per phase:
  requests, failures, longest gap, which nodes served. Measured: lb1, web1, app1, fw1, leaf1 0 lost; border1 4 reads;
  db1 (db2 promoted 5 s later) 32 reads / 3 writes, 3.3 s.
- `./lab.sh db status|promote|reseed|switchover` (tools/dbctl.py).
- The Robot suites (tests/, `./lab.sh test`): 01 fabric, 02 firewalls, 03 data plane, 04 application, 05 policy,
  06 server failures, 07 network failures, 08 database failover, 09 a live connection through fw1 failover — 14 tests;
  06-09 tagged disruptive. Results in results/<timestamp>/ and results/latest (for the Lab Hub, phase 5), with every
  router's configuration before and after and their diff.

### Fixed
- The web VIP on the VRRP virtual MAC (keepalived use_vmac): with the holder's MAC, one border kept the old one for
  minutes after a move (the gratuitous ARP reaches one border; EVPN-MH neighbour sync is not installed by this kernel)
  and black-holed half the flows.
- The firewalls' outside sessions use as-override: the web route came back to the border it came from and was dropped
  as a loop, so one border's VRF outside never had it — a total outage with the other border down.
- Ethernet Segment bonds boot disabled and the protodown watcher raises them when EVPN has converged: a rebooting
  leaf no longer attracts its servers' traffic early (~6 s of loss before). `configure` refuses to push if the watcher
  cannot run (a broken watcher left every bond down once during development).
- nginx no longer marks app servers down for the app's 503 (the database failed): a 3 s database pause had evicted both
  for 10 s. Now 3 failures in 5 s, connection errors and timeouts only.
- The Linux VMs' network is an OpenRC service before keepalived / haproxy / nginx / postgresql / the app: keepalived
  started before the VLAN existed and quit at every boot of a load balancer. Rebuilding the bond restarts keepalived.
- fw_failover pauses the app on app1 (it needs port 8080 for its echo server).

### Open
- After a fail-back the standby firewall may lack long-idle connections until their state changes (not yet tested:
  double failover with an idle connection).

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
