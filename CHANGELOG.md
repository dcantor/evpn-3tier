# Changelog

All notable changes to this lab are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.26.0] - 2026-11-09

### Added
- **Add a VRF** (Fabric Factory workflow): a new tier zone behind the firewall pair — VRF + L3VNI, a transit VLAN with
  eBGP from both borders to fw1 / fw2, and a firewall zone denying by default except the Standard Firewall Rules (ICMP).
  The requester names it (2-8 characters); the rest is allocated for added VRF z (1-20): table 2000+z, L3VNI 51000+z,
  L3VNI VLAN 3900+z, transit VLAN 3800+z (L2VNI 13800+z), the z-th /28 of 172.16.240.0/23 (a phpIPAM pool), probes
  10.255.(100+z). Optionally with its first VLAN (Add a VLAN's steps in the same request, one run of each pipeline).
  Subnets are announced to the WAN like the other tiers'. Up to 20 added VRFs.
- **Delete a VRF**: an added VRF only, refused while it holds a VLAN, a server, an LB peering or a custom firewall rule.
- `nautobot_seed.py --check` lists added VRFs among what a seed would discard.

### Changed
- Standard Firewall Rules: the zones come from Nautobot's plan (every VRF with a transit), not a fixed list; a zone
  added gets ICMP from it to every VLAN, a zone deleted loses every standard rule from or to it. The custom
  firewall-rule form offers added zones.
- Add a VLAN's executor is split into steps (phpIPAM, Nautobot, rules, checks) that Add a VRF reuses.
- Remove a VLAN: the last VLAN of an added VRF may be removed (the design VRFs keep that protection).

## [0.25.0] - 2026-11-09

### Added
- **Add a leaf pair** (Fabric Factory workflow): a new rack of two leaves on the running fabric. Everything is allocated
  from the next leaf number k: index 16+k, mgmt 10.18.0.(20+k), loopback 10.61.0.(10+k), AS 65100+k, rack ceil(k/2),
  uplinks to spine1 / spine2 eth(2+k). Stages: pre-flight (host memory, the spines' spare ports, nothing allocated in
  use) -> phpIPAM -> Nautobot (rack, devices, interfaces, addresses, cables) -> rack and cable (the VMs, day-0 over the
  serial console) -> switches pipeline (whole lab verified) -> checks (BGP + BFD to both spines, the same VNIs and
  anycast gateways as leaf1, loopbacks reachable) -> ping from the WAN. The new leaves' eth3-eth7 are spare ports for
  Port Provisioning (disabled). Up to two pairs (leaf5-leaf8).
- **Remove a leaf pair**: the last pair added; refused while a server is cabled to it or a spare port is provisioned.
- lab.conf: `added-leaves.conf` (written by the workflow, not in git) holds the added pairs — the hardware inventory;
  `E3T_PLAN_LEAVES` previews a pair without writing anything.
- The spines have four spare fabric ports, eth7-eth10 (added once, one spine restarted at a time, verified).
- `nautobot_seed.py --check`: what a seed would discard. The seed now **refuses** while Nautobot holds self-service
  changes (VLANs, firewall rules, peerings, profiles, added devices, provisioned ports) unless `--reset`.

### Changed
- UDP link ports: nodes with index 21-40 (added leaves) use a second block, 40000-41999 / far side 50000-51999
  (36000-37999's far side belongs to c8000v-dmvpn-lab). Nodes 1-20 keep their ports.

## [0.24.1] - 2026-11-09

### Fixed
- The web app's 30-second lab-model cache now refreshes as soon as a request has finished: the worker changes Nautobot
  in its own process, so a profile, VLAN or owner it had just created could be missing from the forms for up to 30 s
  (found while recording the demo: a new profile was not yet offered).

## [0.24.0] - 2026-11-09

### Added
- Port Provisioning: **port profiles** — named presets (access: one VLAN; trunk: tagged VLANs, optionally one untagged
  native VLAN) in Nautobot's plan (`port_profiles`); a workflow creates, changes or deletes one; a change can be
  re-applied to every port that uses it (one request, one pipeline run); a profile in use cannot be deleted. Tools page
  *Port Profiles* (with links that pre-fill the request form).
- Port **owners**: applications are Nautobot tenants (tenant group e3t-applications, custom field `e3t_app_id`
  APP-<digits>), each owned by a team (contact association, role Owner); a port belongs to one application
  (relationship `e3t_port_application`) and carries its profile (custom field `e3t_port_profile`). Enabling a spare port
  needs an owner; the switch shows `<APP-ID> <application> — <what is plugged in>`. Tools page *Applications* (operators
  add / change; recorded, not enforced). `manage.py e3t_ownership_schema` creates the Nautobot schema (idempotent).
- **Default** action: a spare port back to disabled with no VLANs, profile, owner or description.
- Trunks may carry one untagged (native) VLAN (switches template: `native-vlan` + `allowed-vlan`).
- Request forms can be pre-filled from the URL (known fields only).

### Tested live
- APP-1042 (Storage team); profile backup-trunk created (#41); leaf2 eth7 on it, owned by APP-1042 (#42: bridge 41, 47
  tagged, 43 untagged; description 'APP-1042 Backup service — backup appliance'); the profile changed and re-applied
  (#43); the port defaulted (#44: shut, no VLANs, owner and profile cleared); the profile deleted (#45).

## [0.23.0] - 2026-11-09

### Added
- Fabric Factory: **Port Provisioning** (a change workflow, approved by an operator). Enable or disable a leaf port; on
  a spare port choose access (one VLAN, untagged) or trunk (the VLANs to allow, tagged), or no VLANs. Uplinks to the
  spines are blocked (listed as such, refused by the form and by the worker). A server's bond leg can only be enabled
  or disabled — disabled only while its other leg is up in Nautobot and live — and its VLAN follows the server. The
  intent lives in Nautobot's native interface fields (enabled, mode, untagged VLAN, tagged VLANs, description); the
  switches pipeline renders it (`disable`, bridge `native-vlan` / `allowed-vlan`, managed deletions take away the rest);
  checks read the port's admin state and the kernel bridge's VLANs; ping test from the WAN. Request forms gain a
  multi-select field (toggle chips).
- Spare ports: eth6 and eth7 on every leaf (lab.conf `ROLE_PORTS[leaf]=7`, unwired UDP NICs; each leaf restarted once,
  one at a time, verify 61/61 after each); in Nautobot, disabled until provisioned.
- verify: an Ethernet Segment leg disabled on purpose (the port's enabled flag in Nautobot's model) is not expected up.
  A VLAN provisioned on a port cannot be removed.

### Tested live
- #34-#38: leaf1 eth6 as a trunk (VLANs 41, 47 tagged on the bridge), then access VLAN 42, then disabled with no VLANs;
  web1's leg leaf1 eth3 disabled (web1 on leaf2; the pipeline's verify passed) and enabled again; an uplink change
  refused. Found and fixed: the live bond check read the wrong line of `show interfaces bonding` (it refused safely).

## [0.22.0] - 2026-11-09

### Added
- Fabric Factory Tools: **ASN Database** (`/tools/asns`; `GET /api/tools/asns`, `/api/tools/asns.xlsx`) — every ASN of
  the fabric (switches, firewalls, WAN, load balancers, and the route-target AS) with its devices, roles, racks, router
  IDs, peer ASNs and VRFs, plus every BGP session of the design (underlay + EVPN, firewall transit per VRF, WAN, load
  balancer peerings); and **VLAN Database** (`/tools/vlans`; `GET /api/tools/vlans`, `/api/tools/vlans.xlsx`) — every
  VLAN of the evpn-3tier group (tier, firewall transit, LB peering, L3VNI) with name, VRF, VNI, subnet, gateway, size,
  where it lives, hosts / members and the request that made it. Read-only, from Nautobot's model; searchable, filterable;
  downloadable as Excel (openpyxl: frozen header, filters, an About sheet). First run: 12 ASNs, 34 BGP sessions, 20 VLANs.

## [0.21.0] - 2026-11-09

### Added
- Fabric Factory: four change workflows (approved by an operator, carried out by the worker, every stage idempotent):
  - **Change a VLAN's span** (leaves, or leaves + borders): Nautobot -> switches pipeline (managed deletions) -> the
    VLAN on exactly the new span -> ping from the WAN -> phpIPAM gateway description. Refused while something attached
    to the borders uses the VLAN.
  - **Add a custom firewall rule** (from zone, to zone, TCP / UDP / ICMP, address or subnet, port or range): validated
    against the zone's address space and the existing policy (no duplicates, nothing already allowed); its own rule
    number (500-899) so no other rule is renumbered; firewalls pipeline; checked on both firewalls; a live TCP connection
    or ping from the source zone to a VM at the destination.
  - **Remove a custom firewall rule** (self-service ones only).
  - **Move a server to another VLAN** (web and app servers, within their VRF, keeping their host number): phpIPAM
    reserves -> both VLANs on the bond (switches) -> custom rules that reach the old address get a twin for the new one
    (firewalls) -> the address moves (servers; load balancers for a web server; verify skipped mid-move) -> old VLAN and
    old twins off (switches / firewalls, full verify) -> checks, ping -> phpIPAM releases the old address. The tier keeps
    serving throughout. Database servers and load balancers are refused (replication peers; the VIP).
- Pipelines (all four Gitea repos): verify runs on the model rendered from Nautobot (not lab.conf); workflow_dispatch
  input `verify` (false only for the middle runs of a multi-pipeline change); one inventory script in all four repos
  (a server's bond carries every bond0.<VLAN> Nautobot lists); custom rules keep their own number; pg_hba allows every
  app server's subnet and every db server's for replication.
- `tools/labssh.py`: `E3T_MODEL=<file>|nautobot` makes the lab's tools (verify) use Nautobot's model;
  `tools/nautobot_inventory.py` (the pipelines' model builder). verify handles port ranges in custom rules.
- The worker's device checks read the lab from Nautobot. PyYAML added to the app's requirements.

### Tested live
- Requests #20-#27: VLAN 47 to the borders and back; web -> db tcp 6432 rule added (a real connection through the
  firewall) and removed; web2 moved to VLAN 47 and back; app1 moved to VLAN 42 (its web -> app rule followed it) and
  back. ~11 minutes per move, every final verify passed, phpIPAM <-> Nautobot 116/116 afterwards.
- Found and fixed: moving back created a needless twin rule (the subnet rule already covered the address).

## [0.20.1] - 2026-11-09

### Changed
- VLANs 10, 20 and 30 renamed vlan_10_web, vlan_20_app and vlan_30_db in Nautobot and phpIPAM (they were web-10 /
  app-20 / db-30 and web / app / db, from the initial loads), the vlan_<ID>_<VRF> format the self-service VLANs use.
  `tools/nautobot_seed.py` and `tools/phpipam_load.py` now name tier VLANs that way (`lab.sh ipam --check`: 0
  changes). Reconciliation: 120/120, no warnings.
- VLAN 40 (VRF app, request #2) renamed app-backup -> vlan_40_app in Nautobot and phpIPAM: every tier VLAN now
  follows vlan_<ID>_<VRF>.

## [0.20.0] - 2026-11-09

### Added
- phpIPAM ↔ Nautobot reconciliation (read-only; it reports, it repairs nothing): `workflows/reconcile.py`,
  `GET /api/fabric/reconcile`, `manage.py e3t_reconcile [--json]` (exit 0 in sync, 1 drift, 2 a source did not
  answer), and a *Sources of truth* card on the Fabric Dashboard. Compares VRFs; VLAN IDs and names; every subnet /
  prefix with its VRF and VLAN (phpIPAM's containers aside); phpIPAM's probe subnets against the plan's probe bases;
  the plan's gateways reserved and flagged in phpIPAM; every address Nautobot assigns reserved in phpIPAM; and
  Nautobot's config-context plan against its own VLAN and prefix objects. Differences of a request still open are
  shown as in flight, not drift. First run: 117/120, no drift; 3 warnings (VLANs 10, 20, 30 are named web / app / db
  in phpIPAM and web-10 / app-20 / db-30 in Nautobot, from the initial loads).

## [0.19.0] - 2026-11-09

### Added
- Fabric Factory: **Fabric Dashboard** (New request → Tools, `/tools/fabric`; API `GET /api/fabric`, `GET /api/fabric/health`,
  read-only, no approval). From Nautobot: switches by role, every device by role and model / platform, racks, a topology
  drawn from the cabling, VRFs (L3 VNI, VLANs, subnets, servers), subnets by kind, uplinks and oversubscription per
  leaf / border, design parameters. Design KPIs: leaf-to-spine ECMP width, oversubscription, fabric capacity, capacity
  after a spine failure, dual-homed endpoints, single points of failure, VLAN ID use, 172.16.0.0/16 allocated. Change
  KPIs from the app's requests (completed, success rate, median change time, open). Live health vs. design over SSH
  (cached 30 s): devices reachable, spine fabric sessions vs. spine links, BGP and BFD sessions up, VNI consistency
  across the leaves, software versions, per-device table.

## [0.18.0] - 2026-11-09

### Added
- Standard Firewall Rules: ICMP to every tier VLAN from every other zone (outside and the other tiers). In Nautobot's
  fw_policy as entries tagged standard (backfilled for VLANs 10, 20, 30, 40-44: 24 entries), on the firewalls as rule
  1000 + VLAN ID (firewalls run #8); the lab's renderer derives them. TCP policy unchanged (full verify 65/65).
- Add a VLAN: a *Standard firewall rules* stage, firewall checks, and a final *Ping test from the WAN* (from
  198.51.100.1): every web, app and db VM and the new VLAN's gateway must answer. Remove a VLAN deletes the rules and
  checks they are gone. Proven: requests #14 (VLAN 45 added, 7/7 targets answer) and #15 (removed). 50 tests.

## [0.17.1] - 2026-11-09

### Added
- Retrieve Device State: *BGP AS paths* — VyOS: every IPv4 path in every VRF (from FRR's JSON) as a table with the AS
  path drawn as a chain of ASNs, best / multipath / invalid, next hop, origin, filters (VRF, best only, text), plus the
  raw table and EVPN type-5 prefixes; load balancers: BIRD's routes with attributes and its sessions (local / neighbour
  AS). 49 tests.

## [0.17.0] - 2026-11-09

### Changed
- Firewalls: prefix-list PUBLIC (what outside, and so the WAN, learns) lists every tier VLAN subnet — web, app, db; rule
  number = VLAN ID — not only the first web VLAN. Routes only: the zone policy is unchanged (verify, full: 65/65; from
  the WAN 35 flows, 2 allowed). Template and lab renderer; firewalls run #5. The WAN now learns 172.16.0-3.0/24,
  172.16.10/20/30/40.0/24.
- Add / Remove a VLAN run the firewalls pipeline after the switches and check both firewalls' PUBLIC and the WAN's
  route (present / gone). Proven: requests #12 (VLAN 45 added) and #13 (removed).

## [0.16.0] - 2026-11-09

### Added
- Fabric Factory: the self-service app's new name and logo (a factory whose smoke is a network fabric) — front end,
  favicon, sign-in, Django admin, API docs, classic pages, the Lab Hub link.
- New Request screen, Tools: *View Device Configurations* (live, VyOS set / hierarchical or the Linux role's files,
  find, copy, .txt download, zip of selected / all devices) and *Retrieve Device State* (interfaces table, routes, BGP
  / BIRD, EVPN, MACs, ARP, BFD, VRRP, bond, services, system; auto-refresh; download). Fixed command list, secrets
  redacted, retrievals logged. API: GET /api/devices, /api/devices/{name}/config, /api/devices/{name}/state,
  /api/devices-configs.zip. 48 tests.

## [0.15.0] - 2026-11-09

### Added
- *Remove a VLAN from the fabric*: pre-flight (not lab.conf's, no hosts, not the VRF's last, phpIPAM holds only the
  gateway — else refused, nothing changed) -> Nautobot (plan entry, prefix, VLAN object) -> switches pipeline (managed
  deletions) -> checks (no trace on any switch) -> phpIPAM last (subnet with its gateway address, VLAN). The form lists
  every VLAN with why it cannot go. Lab model: VLAN names, base (lab.conf) VLANs, hosts per VLAN. Nautobot client:
  delete. Proven with request #10 (VLAN 44 removed; switches run #19). 43 tests.

## [0.14.0] - 2026-11-09

### Changed
- *Add a VLAN* runs phpIPAM first — the source of truth for addressing: 1 reserve the VLAN, subnet and gateway in
  phpIPAM (all checks before the first write: taken since submission = refused, nothing changed), 2 Nautobot from
  phpIPAM's reservation (read back), 3 the switches pipeline, 4 the checks. Stages in the front end follow. Proven with
  request #9 (VLAN 44, 172.16.3.0/24). 38 tests.

## [0.13.0] - 2026-11-09

### Added
- Borders: an outbound BGP policy on the eBGP sessions to the WAN — route-map TO-WAN calling prefix-list
  FABRIC-SUBNETS (every fabric VLAN subnet; rule number = VLAN ID). In the switches pipeline's template (subnets from
  Nautobot) and the lab's renderer. Before, a border sent the WAN everything in VRF outside (9 prefixes: loopbacks,
  transit, /31s, /32s, the default route); now 172.16.10.0/24. Switches run #14, verify 61/61.
- *Add a VLAN*: the new subnet joins FABRIC-SUBNETS (switches pipeline) and the request checks both borders permit it
  to the WAN (prefix-list rule, route-map, export on the WAN sessions). Proven with request #7 (VLAN 42, vlan_42_app,
  172.16.1.0/24).

## [0.12.1] - 2026-11-09

### Changed
- *Add a VLAN*: the name is `vlan_<VLAN ID>_<VRF>` (e.g. `vlan_41_web`) — underscores only.

## [0.12.0] - 2026-11-09

### Changed
- *Add a VLAN*: the VRF comes first; the VLAN ID is no longer typed — *Select the next unused VLAN ID* allocates it
  (lowest free above the tier VLANs; Nautobot, phpIPAM and open requests checked; VNI free) and shows it; the name is
  `<VLAN ID>-<VRF>`; the gateway / prefix comes from phpIPAM (first free /24 under 172.16.0.0/16), the gateway being the
  subnet's first host (enforced). Open requests hold their VLAN and subnet in both workflows' validation.
  New: GET /api/catalog/add-vlan/allocate; form definitions carry `allocate` and `computed` field kinds. Request titles:
  "Add VLAN 41 to VRF app (172.16.0.0/24)". 35 tests.

## [0.11.0] - 2026-11-09

### Added
- A new self-service front end: React 19 + TypeScript + Vite 7 (selfservice/frontend/), on a JSON API (Django Ninja,
  /api/, docs at /api/docs) with live updates (/api/stream, server-sent events). Overview with live progress,
  approvals, your requests and the lab; forms generated from the server's; a request page with a live stepper and a
  card per pipeline run (Gitea steps, the Ansible play and task running now, per-device ok / changed / failed, verify
  X / 61, the live log, any step's whole log); toasts on status changes; light / dark; phone layout.
- Live pipeline progress: the worker follows each Gitea run's job log while it runs (Gitea's web endpoint — the API has
  none) and keeps a progress snapshot on the request (stages, steps, hosts, verify, log tail, estimate from earlier runs).
- The app runs on ASGI (gunicorn + uvicorn workers); the service migrates, builds the front end when needed and collects
  static files at start. tools/install_node.sh: Node 24 LTS (checksum-verified) for the build. 31 tests.
- Proven with request #4: lb2 peering (AS 65320, VLAN 160, 172.16.160.0/29, VIP 172.16.210.1) — watched live end to end.

### Changed
- The server-rendered pages moved to /classic/ (fallback). Request actions (approve / reject / retry / cancel) live in
  workflows/services.py, shared by the API and the classic pages.

## [0.10.0] - 2026-11-09

### Added
- Self-service *Set up a load balancer to peer with the fabric* is automated: the form picks a load balancer from
  Nautobot (switch pair and VRF from its cabling); the worker writes the peering to Nautobot (`lb_peerings`, VLAN,
  prefixes), runs evpn-3tier-switches (peering VLAN + prefix-filtered eBGP on the pair) and evpn-3tier-loadbalancers
  (peering VLAN, VIPs on lo, BIRD 2.17 from bundled apks), records it in phpIPAM, and checks BGP, the VIP route and HTTP
  to the VIP. Proven with request #3: lb1 AS 65310, VLAN 150, 172.16.150.0/29, VIPs 172.16.200.0/28. 26 tests.

### Fixed
- verify: BFD is checked only for the sessions the lab defines (a half-built load balancer peering failed the switches
  pipeline).
- loadbalancers: install bird2-openrc (BIRD's init script is a separate package); loose rp_filter on the peering VLAN
  (requests arrive on it, replies leave by the tier VLAN — strict filtering dropped them).
- phpIPAM: the Admin UI login never worked — the loader passed SQL in double quotes and the shell expanded the `$` in
  the bcrypt hash. Quoted properly; the live password was reset.

## [0.9.0] - 2026-11-08

### Added
- Self-service execution with approval: operators approve / reject / retry; a worker (user unit
  evpn-3tier-selfservice-worker, `manage.py e3t_worker`) carries approved requests out. *Add a VLAN* is automated:
  Nautobot (plan, native VLAN, prefix) -> evpn-3tier-switches pipeline (waited for, linked) -> phpIPAM -> a check on the
  devices. Proven with request #2: VLAN 40 (app-backup, VRF app, 172.16.40.1/24) on leaf1-4, switches run #5.
  *Load balancer peering*: approval works, execution not automated yet.
- The request forms validate against Nautobot (lab.conf as fallback). 25 tests.

### Fixed (found carrying out request #2)
- The worker needs paramiko (the device check); the device check ignores VyOS's quoting of values (`vni '10040'`); the
  worker's method is not called `execute` (that is BaseCommand's).

## [0.8.0] - 2026-11-08

### Added
- Self-service app (selfservice/, Django 6.1.3, gunicorn, SQLite): http://192.168.50.231:8120/ (user unit
  evpn-3tier-selfservice, `./lab.sh selfservice`). Users request workflows — *Add a VLAN to the fabric*, *Set up a load
  balancer to peer with the fabric* — validated against the live lab; requests keep parameters, requester, status and
  history. Django accounts: requesters and operators groups; admin account and secret key in
  ~/.config/evpn-3tier/selfservice.env. 20 tests. Lab Hub link. Execution of the workflows: next phase.

## [0.7.1] - 2026-11-08

### Added
- phpIPAM on the LAN: https://192.168.50.231:8443/ (UI and API), a TLS passthrough (user unit evpn-3tier-ipam, socat)
  accepting 192.168.50.0/24 only; the certificate also names 192.168.50.231; nginx passes X-Forwarded-Port so phpIPAM's
  links keep the port. Lab Hub link.

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
