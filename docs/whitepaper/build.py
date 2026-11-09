#!/usr/bin/env python3
"""Builds the Fabric Factory / evpn-3tier white paper: whitepaper.html (print-styled, inline SVG diagrams), then
whitepaper.pdf with headless Chrome.   python3 docs/whitepaper/build.py"""
import html
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
E = html.escape

# ---- palette (print: light only) ------------------------------------------------------------------------------------------
INK, MUTE, LINE, GRID = "#1f2937", "#5b6474", "#8a94a6", "#d6dbe4"
ACC, ACC_T = "#4f46e5", "#eef0ff"
WEB, WEB_T = "#2563eb", "#e8f0fe"
APP, APP_T = "#059669", "#e6f6ef"
DB, DB_T = "#c2410c", "#fdf0e8"
OUT, OUT_T = "#7c3aed", "#f3edfe"
FW, FW_T = "#b91c1c", "#fdecec"
TINT = "#f4f6fa"


# ---- SVG helpers -----------------------------------------------------------------------------------------------------------
def svg(w, h, body, title):
    return (f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{E(title)}" '
            f'font-family="Inter, Ubuntu, \'Noto Sans\', \'DejaVu Sans\', sans-serif">'
            f'<defs>{"".join(_marker(n, c) for n, c in (("a", LINE), ("ak", INK), ("aw", WEB), ("aa", APP), ("ad", DB), ("ao", OUT), ("af", FW), ("ac", ACC)))}</defs>'
            f'{body}</svg>')


def _marker(name, color):
    return (f'<marker id="{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M0 0L10 5L0 10z" fill="{color}"/></marker>')


def box(x, y, w, h, title, lines=(), stroke=LINE, fill="#fff", tsize=13.5, lsize=11.5, anchor="middle", sw=1.3, dash=None, tcolor=INK, lcolor=MUTE, ty=None):
    tx = x + w / 2 if anchor == "middle" else x + 12
    ty = ty if ty is not None else y + 21
    d = f' stroke-dasharray="{dash}"' if dash else ""
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>']
    if title:
        out.append(f'<text x="{tx}" y="{ty}" text-anchor="{anchor}" font-size="{tsize}" font-weight="650" fill="{tcolor}">{E(title)}</text>')
    for i, l in enumerate(lines):
        out.append(f'<text x="{tx}" y="{ty + 17 + i * 15.5}" text-anchor="{anchor}" font-size="{lsize}" fill="{lcolor}">{E(l)}</text>')
    return "".join(out)


def text(x, y, s, size=11.5, color=MUTE, anchor="start", weight=400, italic=False):
    st = ' font-style="italic"' if italic else ""
    return f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-size="{size}" font-weight="{weight}" fill="{color}"{st}>{E(s)}</text>'


def path(d, color=LINE, arrow=None, width=1.4, dash=None, start=False):
    m = f' marker-end="url(#{arrow})"' if arrow else ""
    if start and arrow: m += f' marker-start="url(#{arrow})"'
    da = f' stroke-dasharray="{dash}"' if dash else ""
    return f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}"{m}{da}/>'


def title(s, sub=None, w=1000):
    out = text(0, 20, s, 16, INK, weight=700)
    if sub: out += text(0, 40, sub, 12, MUTE)
    return out


# ---- the diagrams -------------------------------------------------------------------------------------------------------------
def d_platform():
    b = [title("One Ubuntu host runs the 20 VMs; a shared NMS holds the source of truth and CI",
               "Platform and deployment: where every component runs, and how they reach each other")]
    b.append(box(0, 70, 170, 118, "People", ["browsers on the home LAN", "192.168.50.0/24", "", "Fabric Factory, portal,", "Gitea, Grafana, phpIPAM"]))
    # lab host
    b.append(box(200, 56, 800, 400, "", stroke=INK, fill=TINT, sw=1.6))
    b.append(text(216, 78, "Lab host · Ubuntu 26.04.1 LTS · kernel 7.0 · KVM / QEMU 10.2.1 + libvirt 12.0 · 12 cores, 57 GB RAM", 12.5, INK, weight=650))
    svc = [("Fabric Factory :8120", ["Django 6.1 + Ninja API", "React 19 SPA, worker,", "SQLite"], ACC, ACC_T),
           ("Lab portal :8119", ["live topology, runs,", "Order Desk relay,", "Prometheus /metrics"], LINE, "#fff"),
           ("Gitea Actions runner", ["host mode, label ansible,", "capacity 1; Ansible 2.20", "in its own venv"], LINE, "#fff"),
           ("phpIPAM relay :8443", ["socat TLS passthrough", "to the IPAM VM's API", "and UI"], LINE, "#fff")]
    for i, (t, ls, st, f) in enumerate(svc):
        b.append(box(216 + i * 196, 94, 180, 82, t, ls, stroke=st, fill=f))
    b.append(box(216, 198, 768, 30, "", stroke=LINE, fill="#fff"))
    b.append(text(600, 218, "e3t-oob bridge · 10.18.0.0/24 · management of every VM (host 10.18.0.1) — Ansible, SSH tools, monitoring", 11.5, INK, "middle"))
    for i in range(4): b.append(path(f"M{306 + i * 196} 176V198", LINE))
    b.append(box(216, 252, 376, 126, "VyOS VMs · 11", ["2 spines · 4 leaves · 2 border leaves", "2 firewalls (fw1, fw2) · 1 WAN router", "VyOS 2026.09.09 rolling · FRRouting 10.6.1", "1 vCPU, 1 GiB RAM each"], stroke=INK))
    b.append(box(608, 252, 376, 126, "Alpine Linux VMs · 9", ["2 load balancers · 2 web · 2 app · 2 db", "1 IPAM VM (phpIPAM + MariaDB in Docker)", "Alpine 3.22.4 · OpenRC services", "1 vCPU each (256 MiB for most)"], stroke=INK))
    b.append(path("M404 228V252", LINE)); b.append(path("M796 228V252", LINE))
    b.append(box(216, 398, 768, 42, "", stroke=LINE, fill="#fff", dash="5 4"))
    b.append(text(600, 424, "Fabric links: 35 point-to-point QEMU UDP tunnels on 127.0.0.1 — each switch port is a wire to exactly one peer", 11.5, INK, "middle"))
    b.append(path("M404 378V398", LINE)); b.append(path("M796 378V398", LINE))
    # NMS
    b.append(box(200, 492, 800, 168, "", stroke=INK, fill=TINT, sw=1.6))
    b.append(text(216, 514, "NMS · 10.0.0.10 · Docker · shared by the home labs · reached over virbr-oob (host 10.0.0.1)", 12.5, INK, weight=650))
    nms = [("Nautobot 3.2.4 :8080", ["source of truth: REST", "+ GraphQL; PostgreSQL 16,", "Redis 7, Celery"]),
           ("Gitea 1.24.7 :3000", ["4 pipeline repositories,", "Actions, inventory", "commits per run"]),
           ("Prometheus 3.14", ["scrapes the portal's", "/metrics; alert group", "evpn-3tier"]),
           ("Grafana 13.0 :3001", ["dashboard", "evpn-3tier: overview", ""])]
    for i, (t, ls) in enumerate(nms):
        b.append(box(216 + i * 196, 530, 180, 82, t, ls, stroke=LINE))
    b.append(text(216, 640, "Also on the NMS (not used by this lab): gnmic collectors for other labs.", 11, MUTE, italic=True))
    b.append(path("M600 456V492", INK, "ak", dash="4 3", start=True))
    b.append(text(610, 478, "virbr-oob: the worker and the runner call Nautobot and Gitea", 11, INK))
    b.append(path("M170 130H200", INK, "ak"))
    return svg(1000, 668, "".join(b), "Platform and deployment")


def node(x, y, w, nm, l1, l2, st=LINE, fill="#fff"):
    return box(x, y, w, 62, nm, [l1, l2], stroke=st, fill=fill, tsize=13)


def d_topology():
    b = [title("Every server is dual-homed; every leaf and border has one link to each spine",
               "Fabric topology: AS numbers, loopbacks (VTEP / router ID), management addresses, Ethernet segments (ES)")]
    b.append(node(425, 60, 150, "wan", "AS 65000 · lo 10.61.0.41", "clients 198.51.100.0/24", st=OUT, fill=OUT_T))
    b.append(box(270, 160, 460, 100, "", stroke=LINE, fill=TINT, dash="5 4"))
    b.append(text(282, 176, "border leaf pair", 11, MUTE, weight=600))
    b.append(node(290, 184, 190, "border1", "AS 65121 · lo 10.61.0.21", "oob 10.18.0.31"))
    b.append(node(520, 184, 190, "border2", "AS 65122 · lo 10.61.0.22", "oob 10.18.0.32"))
    b.append(path("M470 122L400 184", OUT)); b.append(path("M530 122L600 184", OUT))
    b.append(text(398, 150, "172.31.1.0/31", 10.5, OUT, "end")); b.append(text(604, 150, "172.31.2.0/31", 10.5, OUT))
    b.append(box(0, 150, 240, 120, "fw1 / fw2", ["AS 65200 · lo 10.61.0.31 / .32", "ES 11, 12: LACP to both borders", "transit VLANs 110 · 120 · 130 · 199", "sync link 172.31.255.0/29"], stroke=FW, fill=FW_T))
    b.append(box(760, 150, 240, 120, "lb1 / lb2", ["ES 13, 14: LACP to both borders", "web VIP 172.16.10.10 (VRRP)", "BGP peering VLANs 150 / 160", "AS 65310 / 65320 (BIRD)"], stroke=WEB, fill=WEB_T))
    b.append(path("M240 210H270", FW, "af", start=True)); b.append(path("M760 210H730", WEB, "aw", start=True))
    b.append(node(290, 316, 190, "spine1", "AS 65100 · lo 10.61.0.1", "oob 10.18.0.11"))
    b.append(node(520, 316, 190, "spine2", "AS 65100 · lo 10.61.0.2", "oob 10.18.0.12"))
    for d in ("M385 246V316", "M615 246V316", "M420 246L580 316", "M580 246L420 316"): b.append(path(d))
    b.append(path("M385 378V410M615 378V410M140 410H860"))
    b.append(text(630, 402, "eBGP unnumbered + BFD · EVPN overlay · MTU 9000", 10.5, MUTE))
    for x in (140, 370, 630, 860): b.append(path(f"M{x} 410V448"))
    for rx, rack, lv in ((0, "rack1", (("leaf1", "AS 65101 · lo 10.61.0.11", "oob 10.18.0.21", 40), ("leaf2", "AS 65102 · lo 10.61.0.12", "oob 10.18.0.22", 270))),
                         (510, "rack2", (("leaf3", "AS 65103 · lo 10.61.0.13", "oob 10.18.0.23", 530), ("leaf4", "AS 65104 · lo 10.61.0.14", "oob 10.18.0.24", 760)))):
        b.append(box(rx, 430, 490, 268, "", stroke=LINE, fill=TINT))
        b.append(text(rx + 12, 446, rack, 11, MUTE, weight=600))
        for nm, l1, l2, x in lv: b.append(node(x, 452, 200, nm, l1, l2))
    srv = (("web1", "172.16.10.11", "VRF web", "ES 1", WEB, WEB_T, 20), ("app1", "172.16.20.11", "VRF app", "ES 2", APP, APP_T, 180), ("db1", "172.16.30.11", "VRF db", "ES 3", DB, DB_T, 340),
           ("web2", "172.16.10.12", "VRF web", "ES 4", WEB, WEB_T, 530), ("app2", "172.16.20.12", "VRF app", "ES 5", APP, APP_T, 690), ("db2", "172.16.30.12", "VRF db", "ES 6", DB, DB_T, 850))
    for nm, ip, vrf, es, st, f, x in srv:
        b.append(box(x, 600, 130, 82, nm, [ip, f"{vrf} · {es}", "LACP bond0"], stroke=st, fill=f, tsize=13))
        leaves = (140, 370) if x < 500 else (630, 860)
        for lx in leaves: b.append(path(f"M{lx} 514L{x + 65} 600", "#b8c0cd", width=1.1))
    return svg(1000, 706, "".join(b), "Fabric topology")


def d_policy():
    b = [title("Tiers reach each other and the WAN only through the firewalls",
               "Routing and policy: which routes cross each boundary (prefix-lists, route-map) and what the zone firewall lets start")]
    tiers = (("VRF web", ["VLANs 10, 41, 43, 44", "L3VNI 50010 · transit VLAN 110"], WEB, WEB_T, 80),
             ("VRF app", ["VLANs 20, 40, 42", "L3VNI 50020 · transit VLAN 120"], APP, APP_T, 210),
             ("VRF db", ["VLAN 30", "L3VNI 50030 · transit VLAN 130"], DB, DB_T, 340))
    for t, ls, st, f, y in tiers:
        b.append(box(0, y, 210, 82, t, ls, stroke=st, fill=f, sw=1.8))
        my = y + 41
        b.append(path(f"M210 {my - 9}H360", st, {WEB: "aw", APP: "aa", DB: "ad"}[st]))
        b.append(path(f"M360 {my + 9}H210", MUTE, "a"))
        b.append(text(220, my - 15, "TO-FW: tier subnets", 10.5, st))
        b.append(text(220, my + 25, "DEFAULT-ONLY (0/0)", 10.5, MUTE))
    b.append(box(360, 66, 300, 370, "fw1 / fw2", ["one routing table joins the VRFs", "fw2 prepends its AS twice (standby)", "conntrack-sync over the sync link"], stroke=FW, fill=FW_T, sw=1.8))
    b.append(text(376, 158, "Zone policy (new connections)", 12, INK, weight=650))
    rules = ["OUTSIDE → WEB  the VIP: TCP 443, 80, ICMP", "WEB → APP  172.16.20.0/24: TCP 8080", "APP → DB  172.16.30.0/24: TCP 5432",
             "any zone → any tier VLAN: ICMP", "  (Standard Firewall Rules, rule 1000 + VLAN)", "replies: established / related pass",
             "everything else: dropped and logged", "", "to the firewall itself: BGP, BFD, ping", "from the borders; MGMT and SYNC zones"]
    for i, r in enumerate(rules): b.append(text(376, 182 + i * 18, r, 11, INK if i < 7 else MUTE))
    b.append(box(712, 150, 136, 120, "VRF outside", ["border1 / border2", "transit VLAN 199", "L3VNI 50099", "WAN links /31"], stroke=OUT, fill=OUT_T, sw=1.8))
    b.append(path("M660 196H712", OUT, "ao")); b.append(path("M712 226H660", MUTE, "a"))
    b.append(text(664, 188, "PUBLIC", 10.5, OUT, weight=650)); b.append(text(664, 244, "TO-FW", 10.5, MUTE, weight=650))
    b.append(box(896, 150, 104, 120, "wan", ["AS 65000", "clients", "198.51.100.0/24"], stroke=OUT))
    b.append(path("M848 196H896", OUT, "ao")); b.append(path("M896 226H848", MUTE, "a"))
    b.append(text(851, 188, "TO-WAN", 10.5, OUT, weight=650)); b.append(text(851, 244, "default", 10.5, MUTE))
    notes = [("PUBLIC (firewalls → outside): every tier VLAN subnet, one rule per VLAN, rule number = VLAN ID", OUT),
             ("TO-WAN (borders → WAN): route-map calling prefix-list FABRIC-SUBNETS — fabric subnets only", OUT),
             ("From the WAN: the default route and the client range 198.51.100.0/24", MUTE),
             ("Routes are not permission: the WAN can route to app and db, the zone policy still drops TCP to them", INK)]
    for i, (n, c) in enumerate(notes): b.append(text(0, 470 + i * 19, n, 11, c))
    return svg(1000, 540, "".join(b), "Routing and policy")


def d_evpn():
    b = [title("Inside a leaf: bridging on the L2VNI, routing on the L3VNI, one gateway everywhere",
               "EVPN/VXLAN data and control plane on leaf1 for VLAN 10 (VRF web) — symmetric IRB with an anycast gateway")]
    b.append(box(0, 190, 130, 92, "web1", ["bond0 · LACP", "to leaf1 + leaf2", "172.16.10.11"], stroke=WEB, fill=WEB_T))
    b.append(box(0, 60, 140, 92, "spine1 / spine2", ["eBGP unnumbered", "+ BFD; carry the", "EVPN routes"], stroke=INK))
    b.append(box(160, 60, 600, 430, "", stroke=INK, fill=TINT, sw=1.6))
    b.append(text(340, 82, "leaf1 · VyOS · AS 65101 · VTEP 10.61.0.11", 12.5, INK, weight=650))
    b.append(box(180, 96, 140, 80, "FRR bgpd", ["EVPN to both spines,", "route targets", "65500:VNI"], stroke=INK))
    b.append(path("M180 120H140", INK, "ak", dash="5 4", start=True))
    b.append(box(180, 190, 130, 92, "bond1", ["ESI-LAG, ES 1", "boots disabled;", "watcher raises it"], stroke=INK))
    b.append(box(340, 190, 140, 92, "br0", ["VLAN-aware bridge", "VLAN 10 (and every", "tier / transit VLAN)"], stroke=INK))
    b.append(box(510, 96, 230, 92, "peth10 on br0.10", ["anycast gateway 172.16.10.1", "MAC 00:00:5e:00:01:01", "same on every leaf"], stroke=WEB, fill=WEB_T))
    b.append(box(510, 220, 230, 92, "VRF web · table 1010", ["connected subnets into BGP,", "advertised as EVPN type-5", "RT 65500:50010"], stroke=WEB, fill="#fff"))
    b.append(box(180, 370, 560, 100, "vxlan0 · one VXLAN device", ["VLAN 10 ↔ L2VNI 10010: bridging to the other leaves", "VLAN 4010 ↔ L3VNI 50010: routing between subnets (symmetric IRB)", "source 10.61.0.11 · UDP 4789"], stroke=ACC, fill=ACC_T))
    b.append(path("M130 236H180", WEB, "aw")); b.append(path("M310 236H340", WEB, "aw"))
    b.append(path("M480 222L510 168", WEB, "aw")); b.append(path("M625 188V220", WEB, "aw"))
    b.append(path("M410 282V370", ACC, "ac")); b.append(text(418, 330, "bridged (L2VNI)", 10.5, ACC))
    b.append(path("M625 312V370", ACC, "ac")); b.append(text(633, 340, "routed via br0.4010", 10.5, ACC))
    b.append(box(800, 370, 200, 100, "remote VTEPs", ["leaf2–4, border1–2", "VXLAN over the underlay", "MTU 9000"], stroke=ACC))
    b.append(path("M740 420H800", ACC, "ac", start=True)); b.append(text(770, 412, "data", 10.5, ACC, "middle"))
    b.append(text(0, 516, "Dashed: control plane (BGP EVPN to the spines). Route types: 1 and 4 Ethernet segments (multihoming, designated forwarder) ·", 11, MUTE))
    b.append(text(0, 532, "2 MAC and MAC/IP of every host · 3 VTEP membership per VNI · 5 IP prefixes per VRF.", 11, MUTE))
    return svg(1000, 542, "".join(b), "EVPN on a leaf")


def d_request():
    b = [title("An order from the WAN crosses the firewall three times",
               "The path of one request: WAN client → VIP → web → app → database, with the zone each firewall crossing passes")]
    row1 = (("1 · client on wan", ["198.51.100.1", "HTTPS to the VIP"], OUT, OUT_T), ("2 · border leaf", ["VRF outside", "route via fw1"], OUT, OUT_T),
            ("3 · fw1", ["OUTSIDE → WEB", "TCP 443 to the VIP"], FW, FW_T), ("4 · lb1 (VIP holder)", ["HAProxy: TLS, round", "robin, health 1 s"], WEB, WEB_T),
            ("5 · web1", ["nginx: the page;", "/api/ to the app tier"], WEB, WEB_T))
    for i, (t, ls, st, f) in enumerate(row1):
        b.append(box(i * 205, 64, 180, 76, t, ls, stroke=st, fill=f))
        if i < 4: b.append(path(f"M{i * 205 + 180} 102H{i * 205 + 205}", INK, "ak"))
    row2 = (("6 · fw1", ["WEB → APP", "TCP 8080"], FW, FW_T, 820), ("7 · app1", ["Python API; a fresh", "DB connection"], APP, APP_T, 615),
            ("8 · fw1", ["APP → DB", "TCP 5432"], FW, FW_T, 410), ("9 · db1 (primary)", ["PostgreSQL 17", "writes the order"], DB, DB_T, 205),
            ("10 · db2 (standby)", ["streaming replica;", "promoted if db1 fails"], DB, "#fff", 0))
    for t, ls, st, f, x in row2: b.append(box(x, 196, 180, 76, t, ls, stroke=st, fill=f))
    b.append(path("M910 140V196", INK, "ak"))
    for x in (820, 615, 410): b.append(path(f"M{x} 234H{x - 25}", INK, "ak"))
    b.append(path("M205 234H180", DB, "ad", dash="5 4"))
    b.append(text(0, 300, "Every answer names the load balancer, web server, app server and database that served it, so a failover shows on the page.", 11, MUTE))
    return svg(1000, 310, "".join(b), "Request path")


def d_orchestration():
    b = [title("Fabric Factory orchestrates; the pipelines configure; nothing writes to a device by hand",
               "Automation and orchestration: components, the interfaces between them, and what each call carries")]
    b.append(box(0, 64, 200, 92, "Browser", ["React 19 SPA (TypeScript,", "Vite build); JSON API +", "server-sent events"], stroke=ACC))
    b.append(box(236, 52, 480, 262, "", stroke=ACC, fill=ACC_T, sw=1.6))
    b.append(text(250, 74, "Fabric Factory · lab host :8120", 12.5, ACC, weight=700))
    b.append(box(250, 88, 210, 106, "API", ["Django 6.1 + Django Ninja", "gunicorn + uvicorn (ASGI)", "/api/… and /api/stream (SSE)", "sessions, CSRF, groups"], stroke=INK))
    b.append(box(250, 210, 210, 90, "SQLite", ["requests, history,", "progress snapshots,", "users and groups"], stroke=INK))
    b.append(box(480, 88, 222, 212, "Worker · e3t_worker", ["one approved request at a time", "stages in order; stops at the", "first failure; every stage", "idempotent (retry from the top)", "", "follows each pipeline's live", "log; writes a progress", "snapshot (stages, steps, hosts,", "verify, log tail, estimate)"], stroke=INK))
    b.append(path("M200 110H250", ACC, "ac", start=True))
    b.append(path("M355 194V210", INK, "ak", start=True)); b.append(path("M480 255H460", INK, "ak", start=True))
    ext = (("phpIPAM 1.8.3 · IPAM VM", ["REST API, static app token, TLS", "VLANs, subnets, addresses"], 52),
           ("Nautobot 3.2.4 · NMS", ["REST (writes) + GraphQL (reads)", "plan, VLANs, prefixes, policy"], 142),
           ("Gitea 1.24.7 · NMS", ["REST: dispatch runs, task status", "web endpoint: live job logs"], 232))
    for t, ls, y in ext: b.append(box(770, y, 230, 76, t, ls, stroke=INK))
    for y, lab in ((90, "1 reserve"), (180, "2 plan"), (270, "3 dispatch")):
        b.append(path(f"M702 {y}H770", INK, "ak")); b.append(text(736, y - 6, lab, 10, MUTE, "middle"))
    b.append(box(770, 346, 230, 104, "Gitea Actions runner", ["lab host, host mode, label ansible", "capacity 1; holds the lab lock", "reads Nautobot over GraphQL;", "commits the rendered inventory"], stroke=INK))
    b.append(path("M885 308V346", INK, "ak")); b.append(text(893, 332, "jobs", 10.5, MUTE))
    b.append(box(770, 478, 230, 104, "Ansible 2.20.11", ["vyos.vyos 6.2 (SSH, paramiko)", "Linux: SSH + doas", "Jinja render, check, apply,", "managed deletions"], stroke=INK))
    b.append(path("M885 450V478", INK, "ak"))
    b.append(box(236, 612, 764, 46, "20 lab VMs · over the management network 10.18.0.0/24", (), stroke=INK, fill=TINT, ty=640))
    b.append(path("M885 582V612", INK, "ak"))
    b.append(box(236, 346, 330, 236, "Stages of Add a VLAN", ["1  phpIPAM: reserve VLAN, subnet, gateway", "2  Nautobot: plan entry, VLAN, prefix", "3  Nautobot: Standard Firewall Rules", "4  Gitea: the switches pipeline", "5  Gitea: the firewalls pipeline", "6  SSH: checks on every device", "7  SSH: ping from the WAN", "", "Remove a VLAN runs the reverse;", "phpIPAM is released last."], stroke=LINE, anchor="start", ty=368))
    b.append(path("M620 300V612", ACC, "ac", dash="5 4")); b.append(text(628, 470, "SSH (paramiko):", 10.5, ACC)); b.append(text(628, 484, "checks, ping test,", 10.5, ACC)); b.append(text(628, 498, "device tools", 10.5, ACC))
    return svg(1000, 668, "".join(b), "Automation and orchestration")


def d_pipeline():
    b = [title("Every pipeline run: the same five steps, and a run that would break the lab fails",
               "Anatomy of a pipeline run (all four repositories: switches, firewalls, loadbalancers, servers)")]
    steps = (("1 Inventory", ["Nautobot GraphQL", "→ hosts (this repo's", "roles) + group vars", "(the whole plan);", "committed [skip ci]"]),
             ("2 Render", ["Jinja → each device's", "configuration; drift", "against the lab's", "renderer (reported);", "syntax check"]),
             ("3 Check", ["waits for the lab lock;", "ansible-playbook", "--check --diff:", "every change shown", "before it is made"]),
             ("4 Apply", ["only missing lines,", "commit + save;", "managed deletions:", "rendered no more →", "deleted (cap 25)"]),
             ("5 Verify", ["lab.sh verify --quick:", "61 checks across", "fabric, firewalls,", "data plane and the", "application"]))
    for i, (t, ls) in enumerate(steps):
        x = i * 204
        b.append(box(x, 60, 184, 132, t, ls, stroke=ACC if i == 3 else INK, fill=ACC_T if i == 3 else "#fff"))
        if i < 4: b.append(path(f"M{x + 184} 126H{x + 204}", INK, "ak"))
    b.append(box(300, 220, 400, 52, "Gate between 2 and 3: is the lab up?", ["all 20 VMs running — else check, apply and verify are skipped"], stroke=LINE, fill=TINT, tsize=12))
    b.append(box(0, 220, 270, 70, "Protected paths", ["never deleted: logins, console, syslog,", "NTP, loopbacks, NIC hardware settings"], stroke=LINE, tsize=12))
    b.append(box(730, 220, 270, 70, "Outcome", ["success / failure in Gitea; the worker", "reads it and fails the request if red"], stroke=LINE, tsize=12))
    return svg(1000, 300, "".join(b), "Pipeline anatomy")


def d_sequence():
    lanes = (("Requester", 60), ("Operator", 180), ("Fabric Factory", 305), ("phpIPAM", 430), ("Nautobot", 555), ("Gitea + Ansible", 685), ("Switches, firewalls", 820), ("wan", 945))
    b = [title("Add a VLAN: reserve in phpIPAM, plan in Nautobot, build with the pipelines, prove from the WAN",
               "Sequence of one Add a VLAN request (about 6 minutes, most of it in the two pipeline runs)")]
    for nm, x in lanes:
        b.append(box(x - 60, 56, 120, 34, nm, (), stroke=INK, fill=TINT, tsize=11.5, ty=78))
        b.append(path(f"M{x} 90V720", GRID, width=1.2, dash="3 4"))
    X = dict(lanes)
    msgs = (("Requester", "Fabric Factory", "submit: VRF, span (the app allocates VLAN + /24)", ACC, False),
            ("Fabric Factory", "phpIPAM", "next free /24 and VLAN numbers (allocation)", MUTE, False),
            ("Operator", "Fabric Factory", "approve", ACC, False),
            ("Fabric Factory", "phpIPAM", "1 checks, then reserve VLAN, subnet, gateway", ACC, False),
            ("Fabric Factory", "Nautobot", "2 plan entry, VLAN, prefix (read back from phpIPAM)", ACC, False),
            ("Fabric Factory", "Nautobot", "3 Standard Firewall Rules (ICMP)", ACC, False),
            ("Fabric Factory", "Gitea + Ansible", "4 dispatch evpn-3tier-switches", ACC, False),
            ("Gitea + Ansible", "Nautobot", "inventory over GraphQL", MUTE, True),
            ("Gitea + Ansible", "Switches, firewalls", "check, apply: VLAN, gateway, FABRIC-SUBNETS", INK, False),
            ("Gitea + Ansible", "Fabric Factory", "live log, then success (followed every 3 s)", MUTE, True),
            ("Fabric Factory", "Gitea + Ansible", "5 dispatch evpn-3tier-firewalls", ACC, False),
            ("Gitea + Ansible", "Switches, firewalls", "apply: PUBLIC rule, ICMP rules 1000 + VLAN", INK, False),
            ("Fabric Factory", "Switches, firewalls", "6 checks: VLAN, gateway, policies on every device", ACC, False),
            ("Fabric Factory", "wan", "7 route learned? ping every VM + the gateway", ACC, False),
            ("Fabric Factory", "Requester", "done — every step shown live (SSE)", ACC, True))
    y = 120
    for a, c, label, col, dashed in msgs:
        xa, xc = X[a], X[c]
        arrow = {ACC: "ac", MUTE: "a", INK: "ak"}[col]
        b.append(path(f"M{xa} {y}H{xc + (-6 if xc > xa else 6)}", col, arrow, dash="5 4" if dashed else None))
        lx = (xa + xc) / 2
        b.append(text(lx, y - 6, label, 10.5, col if col != MUTE else MUTE, "middle"))
        y += 40
    return svg(1000, 730, "".join(b), "Add a VLAN sequence")


def d_states():
    b = [title("A request is carried out only after approval, and a failed one can be retried",
               "Request lifecycle in Fabric Factory")]
    st = (("submitted", 0, 80, LINE), ("approved", 280, 80, ACC), ("running", 560, 80, ACC), ("done", 840, 80, APP),
          ("rejected", 0, 210, LINE), ("cancelled", 280, 210, LINE), ("failed", 560, 210, FW))
    for nm, x, y, c in st: b.append(box(x, y, 140, 50, nm, (), stroke=c, fill="#fff", sw=1.8, ty=y + 30))
    for x0, lab in ((140, "operator approves"), (420, "worker picks it up"), (700, "every stage passes")):
        b.append(path(f"M{x0} 105H{x0 + 140}", INK, "ak")); b.append(text(x0 + 70, 97, lab, 10.5, MUTE, "middle"))
    b.append(path("M630 130V210", INK, "ak")); b.append(text(638, 175, "a stage fails", 10.5, MUTE))
    b.append(path("M600 260V276H470V150H400V130", INK, "ak")); b.append(text(478, 226, "operator", 10.5, MUTE)); b.append(text(478, 240, "retries", 10.5, MUTE))
    b.append(path("M50 130V210", INK, "ak")); b.append(text(58, 175, "operator rejects", 10.5, MUTE))
    b.append(path("M110 130V160H320V210", INK, "ak")); b.append(path("M350 130V210", INK, "ak"))
    b.append(text(358, 175, "requester", 10.5, MUTE)); b.append(text(358, 189, "cancels", 10.5, MUTE))
    return svg(1000, 290, "".join(b), "Request lifecycle")


def logo(size=64):
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#6366f1"/><stop offset="1" stop-color="#a855f7"/></linearGradient></defs><rect width="48" height="48" rx="12" fill="url(#lg)"/>'
            '<path d="M33.5 15.5 38.5 10.5 43.5 6.5M38.5 10.5 43 14.5M43.5 6.5 43 14.5" fill="none" stroke="#fff" stroke-width="1.5" stroke-linecap="round" opacity=".85"/>'
            '<g fill="#fff"><circle cx="33.5" cy="15.5" r="2.3"/><circle cx="38.5" cy="10.5" r="1.9"/><circle cx="43.5" cy="6.5" r="1.6"/><circle cx="43" cy="14.5" r="1.4"/></g>'
            '<path d="M6 40V27l8-5v5l8-5v5l8-5v-6.5h7V40z" fill="#fff"/><g fill="#7c5cf0"><rect x="9.5" y="31" width="4" height="4" rx="1"/><rect x="16.5" y="31" width="4" height="4" rx="1"/>'
            '<rect x="23.5" y="31" width="4" height="4" rx="1"/><rect x="31" y="26" width="3" height="9" rx="1"/></g></svg>')


def fig(n, svgcode, caption):
    return f'<figure>{svgcode}<figcaption><b>Figure {n}.</b> {caption}</figcaption></figure>'


def table(head, rows, cls=""):
    h = "".join(f"<th>{c}</th>" for c in head)
    r = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f'<table class="{cls}"><thead><tr>{h}</tr></thead><tbody>{r}</tbody></table>'


def code(s):
    return f"<pre><code>{E(s.strip())}</code></pre>"


if __name__ == "__main__":
    from content import body
    page = (HERE / "template.html").read_text().replace("{{LOGO}}", logo(84)).replace("{{BODY}}", body(sys.modules[__name__]))
    (HERE / "whitepaper.html").write_text(page)
    pdf = HERE / "fabric-factory-whitepaper.pdf"
    subprocess.run(["google-chrome", "--headless=new", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf}", (HERE / "whitepaper.html").as_uri()], check=True, capture_output=True, timeout=120)
    print(pdf, pdf.stat().st_size, "bytes")
