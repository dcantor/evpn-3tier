# evpn-3tier — a three-tier web application on an EVPN/VXLAN fabric (VyOS + Alpine, libvirt/KVM)

Every node is a VM: VyOS spines, leaves, borders, a firewall pair and `wan`; Alpine load balancers and web / app / db
servers (19 VMs, about 13.5 GiB). Each tier lives in its own VRF and **the firewalls are the only path between VRFs**.
HAProxy (lb1 / lb2) fronts the web tier only.

```
              ┌──────────┐                        ┌──────────┐
              │  spine1  │        AS 65100        │  spine2  │
              └────┬─────┘                        └────┬─────┘
═══════════════════╧═══════════════════════════════════╧══════════════════
      underlay: every leaf and border has one link to each spine
════╤═══════════╤═══════════╤═══════════╤═════════════╤═══════════╤═══════
┌───┴───┐   ┌───┴───┐   ┌───┴───┐   ┌───┴───┐     ┌───┴────┐  ┌───┴────┐
│ leaf1 │   │ leaf2 │   │ leaf3 │   │ leaf4 │     │border1 │  │border2 │
└───┬───┘   └───┬───┘   └───┬───┘   └───┬───┘     └───┬────┘  └───┬────┘
    └─ ESI-LAG ─┘           └─ ESI-LAG ─┘             └─────┬─────┘
 web1 app1 db1 (rack1)    web2 app2 db2 (rack2)    fw1/fw2   lb1/lb2   wan
```

| VRF | VLAN / L2VNI | Subnet | Hosts |
|---|---|---|---|
| web | 10 / 10010 | 172.16.10.0/24 | lb1 .5, lb2 .6, VIP .10, web1 .11, web2 .12 |
| app | 20 / 10020 | 172.16.20.0/24 | app1 .11, app2 .12 |
| db | 30 / 10030 | 172.16.30.0/24 | db1 .11, db2 .12 |
| outside | — | 172.31.n.0/31 to wan | wan, test clients 198.51.100.0/24 |

Firewall transit VLANs (borders only): 110 web, 120 app, 130 db, 199 outside (172.16.1x0.0/28: borders .2/.3, fw1 .11,
fw2 .12). fw1 is preferred; fw2 prepends its AS twice. OOB 10.18.0.0/24 (see `./lab.sh status`).

## Quick start

```bash
tools/build_image.sh    # once: images/e3t-alpine.qcow2
./lab.sh gen            # render nodes/<n>/vyos_config.txt from lab.conf
./lab.sh up             # create and start the 19 VMs
./lab.sh bootstrap      # first boot only: day-0 config over the serial consoles (~5 min, parallel)
./lab.sh configure      # re-apply the VyOS configs; Linux VMs: bond, VLAN, default route
./lab.sh check-wiring   # every link reaches the peer port lab.conf names
./lab.sh services       # the application: PostgreSQL primary + replica, app, nginx, HAProxy + keepalived
./lab.sh verify         # 65 checks: fabric, firewalls, data plane, the application end to end, the policy matrix
./lab.sh fw-failover    # power fw1 off under a live web1 -> app1 connection, and back (~4 min)
./lab.sh chaos lb       # a component fails under load and comes back: lb | web | app | db | leaf | border | fw | all
./lab.sh db status      # PostgreSQL roles (promote / reseed / switchover too)
./lab.sh test           # the Robot suites, ~35 min (--exclude disruptive: ~3 min); results/latest/
./lab.sh status
./lab.sh down
```

Logins: VyOS `vyos`/`vyos`, Linux `lab`/`lab` (doas). `tests/.venv/bin/python tools/host_cmd.py <node> <cmd>` runs a
shell command on any node.

## Plan

0. Image, topology, lab.sh — **done (0.1.0)**
1. Fabric, VRFs, ESI-LAG — **done (0.1.0)**
2. Firewall policy (zones, per-tier rules), conntrack-sync, fw failover — **done (0.2.0)**
3. HAProxy + keepalived (web VIP), nginx, the app tier, PostgreSQL primary + replica — **done (0.3.0)**
4. Failure tests and the Robot suite — **done (0.4.0)**
5. Portal (8119), Lab Hub, NMS — **done (0.5.0)**
6. Optional: L4 load-balancing experiments

## CI/CD: Nautobot -> Ansible, four repos and pipelines on Gitea

Since 0.6.0 the lab is **deployed from Nautobot by Ansible**; since 0.6.1 in four private Gitea repos, each with its own
pipeline (the first, combined repo lab/evpn-3tier-ansible is archived):

| Repo (http://192.168.50.231:3000/lab/...) | Deploys | Workflow |
|---|---|---|
| `evpn-3tier-switches` | spine1-2, leaf1-4, border1-2, wan (VyOS) | deploy-switches |
| `evpn-3tier-firewalls` | fw1, fw2 (VyOS: zones, policy, conntrack-sync) | deploy-firewalls |
| `evpn-3tier-loadbalancers` | lb1, lb2 (HAProxy + keepalived) | deploy-loadbalancers |
| `evpn-3tier-servers` | web1-2, app1-2, db1-2 (nginx, app, PostgreSQL) | deploy-servers |

Each run (push to main, or Run workflow / `tools/pipeline.py run` in the repo): Nautobot GraphQL -> the inventory (only the
repo's devices as hosts; the whole lab model in group_vars) -> **committed to the repo** (`[skip ci]`) -> render + drift
report against lab.conf (VyOS repos) -> check mode with diff -> apply -> `lab.sh verify --quick` (the whole lab).

- **Nautobot** (location / namespace / tenant group `evpn-3tier`): 19 devices `e3t-*` with roles, racks, platforms,
  primary IPs (Ansible's `ansible_host`), interfaces with MACs, cables (label = link kind), loopbacks, the servers' tier
  addresses, VRFs with route targets, VLANs, prefixes; config context `evpn-3tier` for the plan (VNIs, transit / wan /
  sync, VIP, firewall policy, ES). Seeded from lab.conf by `tools/nautobot_seed.py` (idempotent); after that, edit Nautobot.
- **Runner:** `lab-host-ansible` (label `ansible`, user scope: serves all four, one deploy at a time; user unit
  `gitea-runner-ansible`, host mode), toolchain `~/.local/share/e3t-ansible` (ansible-core 2.20, vyos.vyos 6.2,
  ansible.netcommon, ansible.utils, community.general). Every pipeline holds the lab's test lock while it deploys.
- Proven: a value changed only in Nautobot (vrf_max_paths 32 -> 16 -> 32) reached every leaf and border and verified;
  the four repos' first runs changed nothing (changed=0 on all 19 hosts) and verified 61/61.
- **Managed deletions** (VyOS repos, since 0.7.0): after the push, what the templates no longer render is deleted from
  the device (protected: system logins, console, syslog, hardware IDs, the loopback; at most 25 per node unless forced).
  Proven through Nautobot: a firewall rule added to the policy in Nautobot appeared on fw1 and fw2 (firewalls run 3);
  removed from Nautobot, the next run deleted `firewall ipv4 name OUTSIDE-WEB rule 40` from both (run 4), 61/61.

`lab.sh configure` / `services` still work (lab.conf); once Nautobot is edited they would push lab.conf's values back.

## Self-service: request workflows (Django API + React front end, `selfservice/`)

**http://192.168.50.231:8120/** — users **request workflows on the lab**; an operator approves; a worker carries them out
through the lab's own systems — and everyone **watches it happen live**: the stage, each Gitea pipeline's steps, the
Ansible play and task running now, every device's ok / changed / failed results, verify's checks, the log as it is
written, time left (from earlier runs).

```
browser (React, frontend/)  --/api/ JSON, session cookie + CSRF-->  Django + Django Ninja (ASGI: gunicorn + uvicorn)
        ^                                                                  |  SQLite: requests, history, progress
        +----------- /api/stream (server-sent events: "request N changed") +
worker (manage.py e3t_worker): Nautobot -> Gitea pipelines (log followed live) -> phpIPAM -> checks; writes progress
```

- **Front end** — `selfservice/frontend/`, React 19 + TypeScript + Vite 7 (react-router 7; no UI kit: one stylesheet,
  light / dark). Pages: overview (counts, requests in progress with live bars, approvals, your requests, the lab from
  Nautobot), new request (the forms are generated from the server's — validation stays in one place), a request (live
  stepper, a card per pipeline run: Gitea steps with durations, now-running task, a device grid, verify meter, live log,
  any step's full log; history; approve / reject / retry / cancel), all requests and approvals (operators).
  Built by `frontend/build.sh` into `frontend/dist/`, served by Django at `/static/app/` (whitenoise); the service builds
  it when its sources are newer. Node 24 LTS in `~/.local/share/node` (`tools/install_node.sh`, checksum-verified) is
  needed only to build. Same origin as the API: no CORS, no tokens in the browser.
- **API** — `/api/`, Django Ninja, OpenAPI docs at **/api/docs**: session (sign in / out), lab, catalog and form
  definitions, requests (list by scope, counts, create, detail, approve / reject / retry / cancel), a pipeline step's
  whole log (read live from Gitea). **/api/stream** — server-sent events: once a second, the requests that changed;
  the browser fetches them again. No message broker: one indexed query a second per open tab.
- **Live progress** — the worker keeps a snapshot on the request (`progress`, workflows/progress.py): the workflow's
  stages, and per pipeline run its Gitea steps plus what its live log says (Ansible PLAY / TASK lines, per-host
  results, verify PASS / FAIL). Gitea 1.24 has no API for a running job's log: the worker reads the endpoint Gitea's own
  web UI uses (`POST /<repo>/actions/runs/<n>/jobs/0`, log cursors — only new lines), signed in as `lab`.
- **Classic pages** (the first, server-rendered UI) stay at **/classic/** as a fallback.

- **Catalog** (selfservice/workflows/catalog.py, forms.py): *Add a VLAN to the fabric* (VLAN ID, name, VRF, gateway prefix,
  leaves or leaves + borders) and *Set up a load balancer to peer with the fabric* (a load balancer from Nautobot — its
  switch pair and VRF follow from its cabling — ASN, peering VLAN and subnet, VIP prefixes, BFD) — both **automated**.
  Forms are checked against **Nautobot** (the
  source of truth; lab.conf if Nautobot does not answer): VLAN IDs and VNIs free, subnets inside 172.16.0.0/16 and
  overlapping nothing, private ASNs unused — so what the app added counts as used for the next request.
- **Add a VLAN — allocated, not typed** (workflows/allocate.py): the requester picks the **VRF** (first) and the span;
  *Select the next unused VLAN ID* asks the app, which picks the lowest ID above the highest tier VLAN that nothing
  uses (Nautobot's plan, phpIPAM's VLANs, open requests; its VNI free too) and **phpIPAM's first free /24** under
  172.16.0.0/16 that overlaps nothing — the **gateway is its first host**. The **name is `vlan_<VLAN ID>_<VRF>`**
  (e.g. `vlan_41_web`, underscores only). Nothing is reserved until the worker writes it: an open request (submitted / approved / running) holds its
  VLAN and subnet — the next allocation skips them, and submitting re-checks both.
- **The borders' WAN export policy**: the eBGP sessions to the WAN export through **route-map `TO-WAN`**, which calls
  **prefix-list `FABRIC-SUBNETS`** — one exact rule per fabric VLAN subnet, the rule number being the VLAN ID (a new
  VLAN adds a rule, nothing renumbers). Rendered by both the pipeline (from Nautobot) and the lab (tools/render.py).
  The WAN gets the fabric's subnets that reach VRF outside (today 172.16.10.0/24, the web tier) and nothing else — no
  loopbacks, transit, /31s or /32s, nor the default route back. *Add a VLAN* puts its subnet in the list (the switches
  pipeline) and checks both borders carry it.
- **Add a VLAN, executed** (workflows/executors.py, every step idempotent): 1 Nautobot — the VLAN in the evpn-3tier plan
  (config context), the native VLAN and its prefix in the VRF; 2 Gitea — the evpn-3tier-switches pipeline (inventory,
  check, apply, verify), waited for, its run linked; 3 phpIPAM — VLAN, subnet, gateway reservation; 4 the devices — every
  switch in the span carries VLAN -> VNI and the anycast gateway. Proven: request #2, VLAN 40 (app-backup, VRF app,
  172.16.40.1/24) on leaf1-4.
- **Load balancer peering, executed**: 1 Nautobot — the peering in the plan (config context `lb_peerings`), the peering
  VLAN and the prefixes; 2 the evpn-3tier-switches pipeline — on the switch pair, `br0.<VLAN>` (.1/.2) in the VRF and an
  eBGP neighbour (BFD optional) importing only the VIP prefixes, exporting nothing; 3 the evpn-3tier-loadbalancers
  pipeline — `bond0.<VLAN>` (last address, loose rp_filter), the first address of each VIP prefix on `lo` (HAProxy binds
  everything), BIRD 2 announcing the VIP prefixes; 4 phpIPAM — VLAN, peering subnet with its three addresses, VIP
  prefixes; 5 checks — both sessions Established with prefixes, the VIP prefix in the VRF on a leaf (EVPN type-5),
  `http://<VIP>/health` from a server in the VRF. Proven: request #3, lb1 AS 65310, VLAN 150 / VNI 10150,
  172.16.150.0/29, VIPs 172.16.200.0/28 (172.16.200.1 answers `lb1 ok` from web1 / web2).
- **Statuses**: submitted -> approved -> running -> done / failed (an operator can retry: steps are idempotent), or
  rejected / cancelled. A request running when the worker restarts is marked failed. Every step is in its history.
- **Users** are Django accounts: *requesters* submit and see / cancel their own; *operators* see every request and approve,
  reject or retry. Accounts in Django admin (/admin/). Admin account and secret key: `~/.config/evpn-3tier/selfservice.env`.
- **Credentials** for the worker are read at run time (Nautobot token and Gitea password from the NMS, phpIPAM's from
  ~/.config/evpn-3tier/phpipam.env); none in git.
- Tests: `cd selfservice && .venv/bin/python manage.py test workflows` (35: forms, allocation, flows, approval, the log parser,
  progress, the API); the front end type-checks in its build (`tsc -b`, strict).

Note: a VLAN or peering added through the app lives in Nautobot (and on the devices), not in lab.conf: the VyOS pipelines' drift
report shows it, and `lab.sh configure` (lab.conf) would not remove it (additive).

## External IPAM: phpIPAM (node `ipam`)

Since 0.7.0 the lab has an external IPAM: **phpIPAM 1.8.3** on the 20th VM, `ipam` (Alpine + Docker: phpipam-www and
MariaDB 11.8, baked into `images/e3t-ipam.qcow2` by `tools/build_ipam_image.sh`; OOB only, 10.18.0.5, 1 GiB).
nginx in front: **https://10.18.0.5/** (the API: phpIPAM's app-code auth is HTTPS only) and http://10.18.0.5/ (the UI);
phpIPAM itself listens on 127.0.0.1:8080 and trusts nginx's X-Forwarded-Proto.

`./lab.sh ipam` (tools/phpipam_load.py) loads the lab's real addressing from lab.conf — idempotent (`--check`: what
would change): section `evpn-3tier`; VRFs web / app / db / outside (RD = RT 65500:<L3VNI>); VLAN domain `evpn-3tier`
(11 VLANs: tier, transit, L3VNI); **20 subnets** nested under 172.16.0.0/16, 172.31.0.0/16 and 10.255.0.0/16, each with
its VRF and VLAN; **102 addresses** with hostname, description, MAC and gateway flag (every VM's OOB address, loopbacks,
anycast gateways, the switches' VLAN addresses, servers, the web VIP, transit, wan links, sync + VRRP, probes, clients).
The first run bootstraps phpIPAM (schema without the demo data, admin password, API app `e3t`, ssl_code); credentials
are made at random and kept in `~/.config/evpn-3tier/phpipam.env` (mode 600), not in git.

**From the LAN: https://192.168.50.231:8443/** (UI and API, e.g. `https://192.168.50.231:8443/api/e3t/`) — a TLS
passthrough on the lab host (user unit `evpn-3tier-ipam`: socat 192.168.50.231:8443 -> 10.18.0.5:443, **clients from
192.168.50.0/24 only**). TLS is end to end (phpIPAM's API requires HTTPS); phpIPAM's login and API token protect it. The
certificate (self-signed) names 10.18.0.5 and 192.168.50.231. nginx passes the port the client used
(X-Forwarded-Port), so phpIPAM's links keep :8443. The Lab Hub's card links it.

**Nautobot is not synced with it (yet)** — that is a separate, later step; the ipam VM is not in Nautobot either.

## The portal, the Lab Hub, the NMS

**Portal:** http://192.168.50.231:8119 (sign in at the Lab Hub; user unit `evpn-3tier-webapp`, `./lab.sh webapp`).
- *A request, right now*: every 5 s the portal sends one request through the whole application from wan's clients and
  shows which firewall, load balancer, web, app and database server it took.
- The topology, live (BGP sessions, Ethernet Segment bonds, services, the VIP holder, the active firewall, the primary),
  the load balancers and HAProxy's view of the web tier, the firewalls (VRRP, conntrack) and the policy, the fabric.
- Runs (one at a time, streamed, kept in webapp/runs/): verify, a failure scenario under load (with its per-phase
  table), the live connection through a firewall failover, the Robot suites (read-only, all, one), redeploying the app.
- **/shop/** is the Order Desk itself: the portal relays it through wan, which forwards from the test clients' address
  (198.51.100.1) through the firewall to the VIP — the host has no route into the tier VRFs. /haproxy/lb1/ and
  /haproxy/lb2/ are HAProxy's stats pages. /docs is the API.

**Lab Hub:** listed as `evpn-3tier` (19 VMs, 13.5 GiB): power, the last Robot run (results/latest), links to the portal,
the Order Desk, Grafana and GitHub.

**NMS** (lab-portal 0.53.0): Prometheus job `evpn-3tier` scrapes the portal's /metrics (SD /api/sd); alert group
`evpn-3tier` (the application down, nodes, BGP, multihoming legs, services, the active firewall, the database primary and
standby, failed tests — all quiet while a portal run goes); Grafana **evpn-3tier: overview**
(http://192.168.50.231:3001/d/evpn-3tier-overview).

## The application

**Order Desk** (app/): a page and a small order API. Every answer says which load balancer, web server, app server and
database served it, so a failover shows on the page.

```
client ─443─► VIP 172.16.10.10 (keepalived: lb1, else lb2) ─► HAProxy ─round robin, /health every 1 s─► nginx web1 / web2
   nginx ─/api/, across the firewall (8080)─► app1 / app2   (nginx tries the other on error, rests a failed one 10 s)
   app   ─across the firewall (5432)─► host=db1,db2 target_session_attrs=read-write ─► the PostgreSQL primary
   db1 primary ─streaming replication─► db2 hot standby
```

- **lb1 / lb2:** HAProxy 3.2 (:443 TLS with a self-signed lab certificate in nodes/.lb-cert/, :80 redirects; stats on
  the OOB address :8404 — http://10.18.0.51:8404/ from the host). keepalived 2.3 holds the VIP (VRRP unicast, lb1
  priority 150) while HAProxy runs.
- **web1 / web2:** nginx 1.28: the page, `/health`, `/api/` to both app servers.
- **app1 / app2:** app/app.py (OpenRC service `e3t-app`), psycopg 3; a fresh database connection per request.
- **db1 / db2:** PostgreSQL 17, database `shop`; `app` may connect only from the app VLAN, `replicator` only from the
  db VLAN. No VIP, no load balancer: the app's connection string finds the primary.

**See it in a browser:** http://192.168.50.231:8119/shop/ (the portal's relay through wan). From the command line, on
wan: `curl -sk --interface 198.51.100.1 https://172.16.10.10/api/info`.

## The firewalls

lab.conf `FW_POLICY` is the policy; tools/render.py turns it into VyOS zones: WEB, APP, DB, OUTSIDE (each VRF's transit
VLAN), SYNC (fw1 <-> fw2), MGMT (eth0) and LOCAL. Replies always pass; new connections between zones only as listed —
outside -> web VIP 443 / 80 / ping, web -> app 8080, app -> db 5432 — the rest dropped and logged. The firewalls accept
BGP / BFD / ping from the borders.

**Failover.** fw1 is preferred (fw2 prepends its AS twice on everything it sends). conntrackd copies connection states
over the sync link (172.31.255.0/29); VyOS ties it to VRRP (group FWSYNC on the sync link, fw1 priority 200). Measured
(`./lab.sh fw-failover`): fw1 powered off -> the live connection pauses 1.8 s (BFD 1.5 s + BGP) and carries on through
fw2; fw1 back -> 1.8 s again, back through fw1. Not yet covered: after a fail-back the standby learns only connections
whose state changes, so a long-idle connection may be missing on it until then (phase 4: double failover).

## Failures

`./lab.sh chaos <scenario>`: wan's clients send an HTTPS read every 100 ms and an order every second, each on a fresh
connection through the firewall to the VIP; one component is powered off for 20 s and back. Measured (0.4.0):

| Scenario | What takes over | Lost while down | Longest gap | Back |
|---|---|---|---|---|
| lb1 off | keepalived: VIP (virtual MAC) to lb2 | 0-4 (in flight) | 0.1 s | 0 lost |
| web1 off | HAProxy health check + redispatch | 0-1 (in flight) | 0.1 s | 0 lost |
| app1 off | nginx: the other app server | 0 | 0.1 s | 0 lost |
| fw1 off | BFD + BGP: every VRF to fw2; conntrack-sync | 0 | 0.1 s | 0 lost |
| leaf1 off | EVPN multihoming: the servers' other leg | 0 | 0.1 s | 0 lost |
| border1 off | border2 for firewalls, load balancers, wan | 4 reads | 0.3 s | 0 lost |
| db1 off, db2 promoted 5 s later | the app finds the new primary itself | 32 reads, 3 writes | 3.3 s | switched back |

Requests in flight on a server at the instant it loses power cannot be saved (failover reroutes new connections), so
lb1 / web1 lose a few now and then. The Robot suites (tests/suites/01-09) assert these with some room; 06-09 are tagged
`disruptive`. Last full run (0.5.1, from the portal): 14 of 14.

## Things that are not obvious

- **No /32s to the firewalls.** advertise-svi-ip puts the other border's transit address into the VRF as a /32; exported
  to the firewalls it routed their BFD / BGP to that border through this one (TTL - 1, the sessions died). See TO-FW.
- **VLAN IDs stop at 4094.** VyOS dropped VLAN 4099 lines without an error.
- **The VIP needs a MAC that does not move** (`use_vmac`). With the holder's own MAC, lb2's gratuitous ARP reached one
  border only (a bond hashes broadcasts by MAC), and the other kept lb1's MAC for minutes: this kernel rejects
  EVPN-MH neighbour sync, and the fabric keeps neighbours REACHABLE 5 min. Half the flows went to a dead lb.
- **One border's VRF outside never had the web route:** fw1 learns it from a border and the same border dropped it on
  the way back (its own AS in the path). With the other border down, wan had no route at all. `as-override` on the
  firewalls' outside sessions.
- **Segment bonds boot disabled; the protodown watcher raises them** once FRR has no protodown reason (or only its
  startup delay, with EVPN settled 20 s). Up straight from VyOS's config, a rebooting leaf attracted its servers'
  traffic before EVPN had converged (~6 s of loss). `lab.sh configure` refuses to push if the watcher cannot run.
- **A database pause must not evict app servers:** nginx counted the app's 503 (database down) and read timeouts against
  the app server, evicted both for 10 s, and turned 3 s of leaf failover into 13 s of 502s. Now: connection errors and
  timeouts only, 3 in 5 s.
- **keepalived needs its interface at boot:** the network is an OpenRC service (`e3t-net`) ordered before keepalived,
  haproxy, nginx, postgresql and the app (from `local`, which runs last, keepalived exited for good).
- **Load measurements:** a request still in flight when the failure starts belongs to the failure (the baseline ends
  5.5 s before the injection), and the loader's clock (wan's) is offset against the host's.
- **VyOS lists only the deepest line** (`show configuration commands`): a spine's bare `address-family l2vpn-evpn` sits
  under `attribute-unchanged next-hop`, so Ansible's vyos_config (line by line) sent it on every run. The renderer emits
  only the deeper line. And two lines set fw eth3's description (the last won on a commit; Ansible saw the other missing).
- **ansible-core 2.21+ has no paramiko_ssh:** without sshpass on the host, the pipeline pins ansible-core 2.20.
- **phpIPAM's API:** `ssl_token` means *user* tokens (log in first); a static app token is `ssl_code`, and HTTPS only.
  A write that answers 404 is a failure (vlanId 0: "Vlan does not exist"); address descriptions are varchar(64); text is
  stored HTML-escaped and anything tag-like ("<->") is stripped. Docker on Alpine needs the `cgroups` service first.
- **OpenRC can remember a crashed PostgreSQL:** `rc-service postgresql start` then says "already started" and does
  nothing; re-seeding a replica (tools/dbctl.py) zaps the state and stops any leftover postmaster first. And
  `pg_basebackup -R` appends a `primary_conninfo` to the copied postgresql.auto.conf, which already holds the source's:
  dbctl keeps exactly one.
- **BusyBox `pgrep -x haproxy` never matches** (keepalived's check failed, both load balancers sat in FAULT): `pidof`.
- **nginx -t needs /run/nginx,** which only the init script creates; **`$$` in SQL** does not survive two shells — the
  roles are created from a file.
- **`pkill -f` from a script started over SSH:** a pattern that also matches the shell's own command line (the program
  started in the same command) kills the session. Kill in a separate SSH call, with `[e]3t-...` patterns.
- **libvirt `<serial type='file'>` logs are root-only;** the image builder uses a pty with `<log append='on'>`.
- Everything else about the overlay comes from evpn-fabric (docs/phase0-findings.md there).
