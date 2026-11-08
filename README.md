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
./lab.sh status
./lab.sh down
```

Logins: VyOS `vyos`/`vyos`, Linux `lab`/`lab` (doas). `tests/.venv/bin/python tools/host_cmd.py <node> <cmd>` runs a
shell command on any node.

## Plan

0. Image, topology, lab.sh — **done (0.1.0)**
1. Fabric, VRFs, ESI-LAG — **done (0.1.0)**; `verify` still to write
2. Firewall policy (zones, per-tier rules), conntrack-sync, fw failover
3. HAProxy + keepalived (web VIP), nginx, the app tier, PostgreSQL primary + replica
4. Failure tests and the Robot suite
5. Portal (8119), Lab Hub, NMS
6. Optional: L4 load-balancing experiments

## Things that are not obvious

- **No /32s to the firewalls.** advertise-svi-ip puts the other border's transit address into the VRF as a /32; exported
  to the firewalls it routed their BFD / BGP to that border through this one (TTL - 1, the sessions died). See TO-FW.
- **VLAN IDs stop at 4094.** VyOS dropped VLAN 4099 lines without an error.
- **libvirt `<serial type='file'>` logs are root-only;** the image builder uses a pty with `<log append='on'>`.
- Everything else about the overlay comes from evpn-fabric (docs/phase0-findings.md there).
