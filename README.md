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
5. Portal (8119), Lab Hub, NMS
6. Optional: L4 load-balancing experiments

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

**See it in a browser.** The host has no route into the tier VRFs (only the OOB network). Tunnel through wan, whose
traffic enters as "outside" like any client: `ssh -L 8443:172.16.10.10:443 vyos@10.18.0.45` (password vyos), then
https://localhost:8443/ (accept the lab certificate). From the command line, on wan:
`curl -sk --interface 198.51.100.1 https://172.16.10.10/api/info`.

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
| lb1 off | keepalived: VIP (virtual MAC) to lb2 | 0 | 0.1 s | 0 lost |
| web1 off | HAProxy health check + redispatch | 0 | 0.1 s | 0 lost |
| app1 off | nginx: the other app server | 0 | 0.1 s | 0 lost |
| fw1 off | BFD + BGP: every VRF to fw2; conntrack-sync | 0 | 0.1 s | 0 lost |
| leaf1 off | EVPN multihoming: the servers' other leg | 0 | 0.1 s | 0 lost |
| border1 off | border2 for firewalls, load balancers, wan | 4 reads | 0.3 s | 0 lost |
| db1 off, db2 promoted 5 s later | the app finds the new primary itself | 32 reads, 3 writes | 3.3 s | switched back |

The Robot suites (tests/suites/01-09) assert these with some room; 06-09 are tagged `disruptive`.

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
- **BusyBox `pgrep -x haproxy` never matches** (keepalived's check failed, both load balancers sat in FAULT): `pidof`.
- **nginx -t needs /run/nginx,** which only the init script creates; **`$$` in SQL** does not survive two shells — the
  roles are created from a file.
- **`pkill -f` from a script started over SSH:** a pattern that also matches the shell's own command line (the program
  started in the same command) kills the session. Kill in a separate SSH call, with `[e]3t-...` patterns.
- **libvirt `<serial type='file'>` logs are root-only;** the image builder uses a pty with `<log append='on'>`.
- Everything else about the overlay comes from evpn-fabric (docs/phase0-findings.md there).
