// Fabric Factory / evpn-3tier deck.   node build.js <out.pptx>
const path = require("path");
const fs = require("fs");
const { makeDeck, wrap, iconPng, THEME, NAVY } = require("./kit");
const { applyTheme } = require(process.env.PPTX_SKILL + "/scripts/apply_theme.js");

const IMG = path.join(__dirname, "img");
const SIZE = { platform: [1012, 624], topology: [1012, 662], evpn: [1012, 498], policy: [1012, 496], request: [1012, 266],
  orchestration: [1012, 624], pipeline: [1012, 256], sequence: [1012, 686], states: [1012, 246], form: [1360, 1464],
  dashboard: [2152, 2090], "nb-devices": [2724, 1880], "nb-prefixes": [2724, 1730], "nb-changelog-crop": [1720, 1064], "nb-graphql": [2752, 1192],
  "nb-interfaces": [2724, 1144], "nb-vlans": [2724, 1608],
  "asn-db": [2160, 2546], "vlan-db": [2160, 2566],
  "port-form": [2160, 2046], "port-request": [2122, 574] };
const W = 13.333, L = 0.6, R = W - 0.6, CW = R - L; // margins
const TOP = 1.75, BOT = 6.8;

(async () => {
  const out = process.argv[2] || "deck.pptx";
  const { pres, C, rec } = makeDeck();
  const T = pres.ShapeType;
  const logo = path.join(IMG, "logo.png");

  // ---- layouts ----------------------------------------------------------------------------------------------------------
  const footer = [
    { image: { x: L, y: 7.0, w: 0.24, h: 0.24, path: logo } },
    { text: { text: "Fabric Factory · evpn-3tier lab", options: { x: L + 0.34, y: 7.0, w: 5, h: 0.24, fontSize: 10, color: C.text2, margin: 0, valign: "middle" } } },
  ];
  pres.defineSlideMaster({
    title: "Content", background: { color: C.background1 },
    objects: [...footer,
      { placeholder: { options: { name: "title", type: "title", x: L, y: 0.38, w: CW, h: 0.72, fontSize: 32, bold: true, color: C.text1, valign: "middle", margin: 0 }, text: "" } },
      { placeholder: { options: { name: "lead", type: "body", x: L, y: 1.1, w: CW, h: 0.5, fontSize: 15, color: C.text2, valign: "top", margin: 0, bullet: false }, text: "" } }],
    slideNumber: { x: R - 0.6, y: 7.0, w: 0.6, h: 0.24, fontSize: 10, color: C.text2, align: "right", margin: 0 },
  });
  pres.defineSlideMaster({
    title: "Section", background: { color: NAVY },
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: L + 0.2, y: 3.0, w: CW - 0.4, h: 1.0, fontSize: 40, bold: true, color: C.background1, valign: "top", margin: 0 }, text: "" } },
      { placeholder: { options: { name: "sub", type: "body", x: L + 0.2, y: 4.05, w: 9.5, h: 1.2, fontSize: 18, color: C.background2, valign: "top", margin: 0, bullet: false }, text: "" } }],
    slideNumber: { x: R - 0.6, y: 7.0, w: 0.6, h: 0.24, fontSize: 10, color: C.background2, align: "right", margin: 0 },
  });
  pres.defineSlideMaster({ title: "Cover", background: { color: NAVY }, objects: [] });

  const recSlide = (name) => { const els = []; rec.slides.push({ layout: name, els }); return els; };
  const content = (section, title, lead) => {
    const els = recSlide("Content");
    const s = wrap(pres.addSlide({ masterName: "Content", sectionTitle: section }), els);
    s.ph(title, { placeholder: "title" });
    if (lead) s.ph(lead, { placeholder: "lead" });
    return s;
  };
  const sectionSlide = (section, n, title, sub) => {
    pres.addSection({ title: section });
    const els = recSlide("Section");
    const s = wrap(pres.addSlide({ masterName: "Section", sectionTitle: section }), els);
    s.text(String(n).padStart(2, "0"), { x: L + 0.2, y: 2.05, w: 2, h: 0.8, fontSize: 40, bold: true, color: C.accent1, margin: 0 });
    s.ph(title, { placeholder: "title" });
    s.ph(sub, { placeholder: "sub" });
    return s;
  };
  // fit a diagram into a box, keeping aspect; align left/top
  const diagram = (s, name, x, y, maxW, maxH, alt, align = "left") => {
    const [pw, ph] = SIZE[name];
    let w = maxW, h = (w * ph) / pw;
    if (h > maxH) { h = maxH; w = (h * pw) / ph; }
    const xx = align === "center" ? x + (maxW - w) / 2 : x;
    s.image({ path: path.join(IMG, name + ".png"), x: xx, y, w, h, altText: alt });
    return { x: xx, y, w, h };
  };
  const ICON = {};
  const icon = async (name, color) => (ICON[name + color] ||= await iconPng(name, color));
  // round icon badge (accent circle + white glyph)
  const badge = async (s, x, y, d, glyph, fill) => {
    s.shape(T.ellipse, { x, y, w: d, h: d, fill: { color: fill }, line: { type: "none" } });
    const p = d * 0.25;
    s.image({ data: await icon(glyph, "FFFFFF"), x: x + p, y: y + p, w: d - 2 * p, h: d - 2 * p, altText: "" });
  };
  // tinted card
  const card = (s, x, y, w, h, color, tint = 90) =>
    s.shape(T.roundRect, { x, y, w, h, rectRadius: 0.08, fill: { color, transparency: tint }, line: { type: "none" } });
  // title + body stacked text (one text box)
  const tb = (s, x, y, w, h, head, body, o = {}) => {
    const runs = [{ text: head, options: { bold: true, fontSize: o.hs || 15, color: o.hc || C.text1, breakLine: true, paraSpaceAfter: 3 } }];
    const lines = Array.isArray(body) ? body : [body];
    lines.forEach((l, i) => runs.push({ text: l, options: { fontSize: o.bs || 14, color: o.bc || C.text2, breakLine: i < lines.length - 1, paraSpaceAfter: o.ps || 2 } }));
    s.text(runs, { x, y, w, h, margin: 0, valign: o.valign || "top" });
  };
  const tableOpts = (x, y, w, colW, extra = {}) => ({ x, y, w, colW, fontSize: 12, color: C.text1, margin: [3, 5, 3, 5], border: { type: "solid", pt: 0.75, color: "D6DBE4" }, valign: "middle", autoPage: false, ...extra });
  const th = (t) => ({ text: t, options: { bold: true, color: C.text1, fill: { color: C.background2 } } });

  // ======================================================================================================================
  // 1 Cover
  pres.addSection({ title: "Introduction" });
  {
    const s = wrap(pres.addSlide({ masterName: "Cover", sectionTitle: "Introduction" }), recSlide("Cover"));
    s.image({ path: logo, x: L + 0.2, y: 0.8, w: 0.9, h: 0.9, altText: "Fabric Factory logo" });
    s.text("FABRIC FACTORY", { x: L + 1.3, y: 0.8, w: 6, h: 0.9, fontSize: 20, bold: true, color: C.background1, charSpacing: 4, valign: "middle", margin: 0 });
    s.text("From a web form to a verified change on a 20-VM EVPN/VXLAN data centre", { x: L + 0.2, y: 2.35, w: 10.6, h: 2.0, fontSize: 40, bold: true, color: C.background1, valign: "top", margin: 0 });
    s.text("Architecture, tech stack, and the automation and orchestration behind the evpn-3tier lab", { x: L + 0.2, y: 4.45, w: 10, h: 0.9, fontSize: 20, color: C.background2, valign: "top", margin: 0 });
    s.text([{ text: "evpn-3tier 0.18.0", options: { bold: true } }, { text: "   ·   9 November 2026   ·   Technical overview" }],
      { x: L + 0.2, y: 6.35, w: 10, h: 0.4, fontSize: 14, color: C.background2, margin: 0 });
    s.notes("Fabric Factory is the self-service front end of the evpn-3tier lab. This deck covers the network, the platform, the sources of truth, the pipelines and the orchestration, resilience, and the tech stack. The companion white paper (PDF) has the full detail.");
  }

  // 2 At a glance
  {
    const s = content("Introduction", "The lab at a glance", "A complete data centre in software, changed only through self-service requests that are checked end to end");
    const kpis = [["20", "VMs: 11 VyOS, 9 Alpine Linux", C.accent1], ["4", "VRFs, joined only through the firewalls", C.accent2],
      ["4", "Gitea pipelines, one per device group", C.accent3], ["65", "end-to-end checks in verify, plus 9 test suites", C.accent4]];
    const kw = (CW - 3 * 0.3) / 4;
    kpis.forEach(([n, l, c], i) => {
      const x = L + i * (kw + 0.3);
      card(s, x, TOP, kw, 1.75, c, 92);
      s.text(n, { x: x + 0.25, y: TOP + 0.15, w: kw - 0.5, h: 0.85, fontSize: 48, bold: true, color: c, margin: 0, valign: "middle" });
      s.text(l, { x: x + 0.25, y: TOP + 1.0, w: kw - 0.5, h: 0.65, fontSize: 14, color: C.text2, margin: 0, valign: "top" });
    });
    const ideas = [
      ["LuTarget", "Intent, not commands", "phpIPAM holds the addressing, Nautobot holds the plan, and pipelines render every configuration. Deleting intent deletes configuration.", C.accent1],
      ["LuShieldCheck", "Isolation by routing", "Each tier lives in its own VRF and learns only a default route to the firewalls, so all traffic between tiers or to the WAN crosses the zone firewall.", C.accent6],
      ["LuActivity", "Visible, reversible automation", "Every step is idempotent and shown live. A failed request is fixed and retried, and nothing is called done until it is checked on the devices and from the WAN.", C.accent3]];
    const iw = (CW - 2 * 0.4) / 3, iy = TOP + 2.15;
    for (const [i, [g, h, b, c]] of ideas.entries()) {
      const x = L + i * (iw + 0.4);
      await badge(s, x, iy, 0.6, g, c);
      tb(s, x, iy + 0.8, iw, 2.2, h, b, { hs: 18 });
    }
    s.notes("Three ideas shape the lab. Intent, not commands: nobody configures a device by hand. Isolation by routing: tiers only ever see a default route that points at the firewalls. Visible, reversible automation: every workflow stage is idempotent and the request page shows the pipelines live.");
  }

  // ======================================================================================================================
  sectionSlide("Architecture", 1, "Architecture", "The platform, the EVPN/VXLAN fabric, the routing and security policy, the application, and the sources of truth");

  // 4 Platform
  {
    const s = content("Architecture", "Platform and deployment", "One Ubuntu host runs the 20 VMs and the lab services; a shared NMS runs the source of truth and CI");
    const d = diagram(s, "platform", L, TOP, 8.4, BOT - TOP, "Platform and deployment diagram: lab host with 20 VMs and services, the NMS with Nautobot, Gitea, Prometheus and Grafana");
    const px = d.x + d.w + 0.35, pw = R - px;
    const rows = [[th("Service"), th("Port")], ["Fabric Factory", "8120"], ["Lab portal", "8119"], ["phpIPAM relay", "8443"], ["Nautobot (NMS)", "8080"], ["Gitea (NMS)", "3000"], ["Grafana / Prom.", "3001 / 9090"], ["Lab Hub", "8088"]];
    s.table(rows, tableOpts(px, TOP, pw, [pw * 0.62, pw * 0.38]));
    tb(s, px, TOP + 3.0, pw, 2.0, "Point-to-point cabling", "Every switch port is a QEMU UDP tunnel to exactly one peer port, proved by lab.sh check-wiring. Management stays on the e3t-oob bridge.", { hs: 14, bs: 13 });
    s.notes("KVM guests managed by libvirt. Fabric links are QEMU UDP tunnels, so wiring is point-to-point like real cabling. Every VM has an out-of-band management interface used by automation, device tools and monitoring. The NMS at 10.0.0.10 hosts Nautobot, Gitea, Prometheus and Grafana.");
  }

  // 5 Topology
  {
    const s = content("Architecture", "Fabric topology", "A two-tier Clos of VyOS routers: 2 spines, 4 leaves, 2 border leaves; eBGP unnumbered + BFD underlay, EVPN/VXLAN overlay");
    const d = diagram(s, "topology", L, TOP, 8.4, BOT - TOP, "Fabric topology: WAN, border pair with firewalls and load balancers, spines, leaves and six dual-homed servers");
    const px = d.x + d.w + 0.4, pw = R - px;
    const facts = [["Underlay", "BGP over IPv6 link-local; BFD 300 ms × 5; ECMP 4; MTU 9000; one AS per leaf, spines share 65100"],
      ["Overlay", "EVPN on the same sessions; explicit RTs 65500:VNI; L2VNI = 10000 + VLAN; one L3VNI per VRF"],
      ["Multihoming", "Every server and border appliance is dual-homed with LACP as one Ethernet segment (ESI)"]];
    facts.forEach(([h, b], i) => tb(s, px, TOP + i * 1.65, pw, 1.55, h, b, { hs: 15, bs: 13 }));
    s.notes("Every leaf and border has one link to each spine. BGP runs unnumbered, so no point-to-point subnets. Each switch's loopback in 10.61.0.0/24 is router ID and VTEP.");
  }

  // 6 Inside a leaf
  {
    const s = content("Architecture", "EVPN/VXLAN inside a leaf", "Symmetric IRB with an anycast gateway: every leaf routes locally and bridges over a single VXLAN device");
    const d = diagram(s, "evpn", L, TOP, CW, 3.75, "Inside leaf1 for VLAN 10: bond, VLAN-aware bridge, anycast gateway, VRF and VXLAN device", "center");
    const items = [["LuRoute", "Symmetric IRB", "The ingress leaf routes in the VRF and sends over the L3VNI; the egress leaf routes again into the subnet", C.accent1],
      ["LuCrosshair", "Anycast gateway", "Every leaf answers for the same gateway IP and virtual MAC 00:00:5e:00:01:01", C.accent2],
      ["LuGitMerge", "ESI multihoming", "A designated forwarder per segment; a failed leg is withdrawn within a second", C.accent3]];
    const iw = (CW - 2 * 0.4) / 3, y = d.y + d.h + 0.2;
    for (const [i, [g, h, b, c]] of items.entries()) {
      const x = L + i * (iw + 0.4);
      await badge(s, x, y, 0.5, g, c);
      tb(s, x + 0.65, y, iw - 0.65, BOT - y, h, b, { hs: 15, bs: 13 });
    }
    s.notes("Segment bonds boot disabled and a watcher raises them only once EVPN has converged, so a rebooting leaf does not attract traffic it cannot forward yet.");
  }

  // 7 Routing & policy
  {
    const s = content("Architecture", "Routing and security policy", "Routing makes the firewalls the only way out of a tier; the zone firewall decides which connections may start");
    const d = diagram(s, "policy", L, TOP, 8.2, BOT - TOP, "Routing and policy: route exchange between tier VRFs, the firewalls, the outside VRF and the WAN");
    const px = d.x + d.w + 0.35, pw = R - px;
    const pol = [["TO-FW", "borders → firewalls", "Tier subnets only, no /32s or transit"],
      ["DEFAULT-ONLY", "firewalls → tier VRFs", "0.0.0.0/0, so a tier can only leave via the firewall"],
      ["PUBLIC", "firewalls → outside", "Every tier subnet; rule = VLAN ID"],
      ["TO-WAN", "borders → WAN", "Prefix-list FABRIC-SUBNETS: VLAN subnets only"]];
    const ch = 1.12, gap = 0.15;
    pol.forEach(([n, where, what], i) => {
      const y = TOP + i * (ch + gap);
      card(s, px, y, pw, ch, C.accent1, 93);
      s.text([{ text: n, options: { bold: true, fontSize: 14, color: C.accent1 } }, { text: "  " + where, options: { fontSize: 12, color: C.text2, breakLine: true } },
        { text: what, options: { fontSize: 13, color: C.text1 } }], { x: px + 0.15, y: y + 0.1, w: pw - 0.3, h: ch - 0.2, margin: 0, valign: "top" });
    });
    s.notes("The borders carry one transit VLAN per VRF to each firewall. Each firewall keeps all VRFs in one routing table, so inter-tier routing happens inside the firewall where the zone policy applies.");
  }

  // 8 Firewall
  {
    const s = content("Architecture", "Zone firewall and high availability", "Replies always pass; new connections pass only as listed in Nautobot's fw_policy; everything else is dropped and logged");
    const cw = (CW - 0.4) / 2;
    card(s, L, TOP, cw, 2.3, C.accent6, 93);
    await badge(s, L + 0.3, TOP + 0.3, 0.55, "LuShield", C.accent6);
    tb(s, L + 1.05, TOP + 0.3, cw - 1.3, 1.85, "Standard Firewall Rules", ["ICMP to every tier VLAN from every other zone.",
      "Rule number = 1000 + VLAN ID, so adding or removing a VLAN never renumbers anything."], { hs: 16, ps: 6 });
    card(s, L + cw + 0.4, TOP, cw, 2.3, C.accent5, 93);
    await badge(s, L + cw + 0.7, TOP + 0.3, 0.55, "LuRefreshCw", C.accent5);
    tb(s, L + cw + 1.45, TOP + 0.3, cw - 1.3, 1.2, "Active / standby pair", "fw2 prepends its AS twice, so fw1 is preferred; conntrackd syncs connection state to fw2.", { hs: 16 });
    s.text([{ text: "1.8 s", options: { bold: true, fontSize: 28, color: C.accent5 } }, { text: "  pause when fw1 powers off", options: { fontSize: 14, color: C.text2 } }],
      { x: L + cw + 1.45, y: TOP + 1.5, w: cw - 1.7, h: 0.6, margin: 0, valign: "middle" });
    // VRF table
    const rows = [[th("VRF"), th("L3VNI / VLAN"), th("Tier VLANs (L2VNI = 10000 + VLAN)"), th("Firewall transit")],
      [{ text: "web", options: { bold: true, color: C.accent2 } }, "50010 / 4010", "10 (lab design); 41, 43, 44 via Fabric Factory", "VLAN 110 · 172.16.110.0/28"],
      [{ text: "app", options: { bold: true, color: C.accent3 } }, "50020 / 4020", "20 (lab design); 40, 42 via Fabric Factory", "VLAN 120 · 172.16.120.0/28"],
      [{ text: "db", options: { bold: true, color: C.accent4 } }, "50030 / 4030", "30 (lab design)", "VLAN 130 · 172.16.130.0/28"],
      [{ text: "outside", options: { bold: true, color: C.accent5 } }, "50099 / 4090", "none; WAN links 172.31.1.0/31, 172.31.2.0/31", "VLAN 199 · 172.16.199.0/28"]];
    s.table(rows, tableOpts(L, TOP + 2.65, CW, [1.3, 1.9, 5.5, CW - 8.7], { fontSize: 13, rowH: 0.42 }));
    s.notes("Powering off fw1 pauses a live connection for 1.8 seconds (BFD plus BGP) before it continues through fw2.");
  }

  // 9 Application
  {
    const s = content("Architecture", "The application: Order Desk", "Every answer names the load balancer, web, app and database server that served it, so failovers show on the page");
    const d = diagram(s, "request", L, TOP, CW, 3.3, "One order from a WAN client through the firewall, VIP, web, app and database tiers", "center");
    const tiers = [["LuScale", "HAProxy 3.2 + keepalived", "TLS on VIP 172.16.10.10; VRRP with a virtual MAC", C.accent1],
      ["LuGlobe", "nginx 1.28", "Page, /health, /api/ proxy with retry", C.accent2],
      ["LuCode", "Python 3.12 API", "psycopg 3; asks for the read-write database", C.accent3],
      ["LuDatabase", "PostgreSQL 17", "Streaming replication to a hot standby", C.accent4]];
    const iw = (CW - 3 * 0.3) / 4, y = d.y + d.h + 0.35;
    for (const [i, [g, h, b, c]] of tiers.entries()) {
      const x = L + i * (iw + 0.3);
      await badge(s, x, y, 0.5, g, c);
      tb(s, x, y + 0.65, iw, BOT - y - 0.65, h, b, { hs: 15, bs: 13 });
    }
    s.notes("The load balancers can also announce their own VIP prefixes over BGP: lb1 (AS 65310) and lb2 (AS 65320) run BIRD 2.17 to both borders on peering VLANs 150 and 160 with BFD. Both peerings were requested and built through Fabric Factory.");
  }

  // 10 Sources of truth
  {
    const s = content("Architecture", "Two sources of truth, one direction of flow", "Each system owns one kind of fact; everything on the devices is derived from them");
    const sots = [["LuNetwork", "phpIPAM 1.8.3", "Addressing", "Subnets under 172.16.0.0/16, gateway and host reservations, VLAN numbers, VRFs", "Allocator reads free /24s and used VLANs", C.accent3],
      ["LuBookOpen", "Nautobot 3.2.4", "The plan", "Devices, cabling, VLAN and prefix objects, and the evpn-3tier config context", "Every pipeline reads it over GraphQL", C.accent1],
      ["LuGitBranch", "Gitea 1.24.7", "Code and history", "Four repositories: templates, playbooks, each run's rendered inventory", "The runner executes them", C.accent4]];
    const cw = (CW - 2 * 0.35) / 3, ch = 3.55;
    for (const [i, [g, n, role, holds, read, c]] of sots.entries()) {
      const x = L + i * (cw + 0.35);
      card(s, x, TOP, cw, ch, c, 92);
      await badge(s, x + 0.3, TOP + 0.3, 0.6, g, c);
      s.text([{ text: n, options: { bold: true, fontSize: 18, color: C.text1, breakLine: true } }, { text: role, options: { fontSize: 14, color: c, bold: true } }],
        { x: x + 1.1, y: TOP + 0.25, w: cw - 1.3, h: 0.75, margin: 0, valign: "middle" });
      tb(s, x + 0.3, TOP + 1.2, cw - 0.6, ch - 1.35, "Holds", holds, { hs: 13, bs: 14 });
      tb(s, x + 0.3, TOP + 2.55, cw - 0.6, 0.9, "Read by", read, { hs: 13, bs: 14 });
    }
    card(s, L, TOP + ch + 0.3, CW, 1.15, C.accent1, 88);
    s.text([{ text: "phpIPAM first when adding, last when removing.  ", options: { bold: true, color: C.text1 } },
      { text: "Addresses are reserved before anything uses them and released only after nothing does. The pipelines read only Nautobot.", options: { color: C.text1 } }],
      { x: L + 0.3, y: TOP + ch + 0.3, w: CW - 0.6, h: 1.15, fontSize: 15, margin: 0, valign: "middle" });
    s.notes("Fabric Factory writes phpIPAM before Nautobot when adding and releases phpIPAM after the devices are clean when removing, so an address is never handed out while still in use.");
  }

  // 10b Where the lab lives in Nautobot
  {
    const s = content("Architecture", "Where the lab lives in Nautobot", "Everything of this lab sits under one name, evpn-3tier, on a Nautobot shared with other labs; devices are named e3t-<node>");
    const rows = [[th("Object"), th("Where in Nautobot"), th("Count")],
      ["Location, racks", "Organization → Locations → evpn-3tier (Site); racks spine, rack1, rack2, border, edge, wan", "1, 6"],
      ["Devices", "Devices, location evpn-3tier: role e3t-<role>, type VyOS / Alpine host, primary IPv4 = OOB (ansible_host)", "19"],
      ["Interfaces, cables", "eth0–7, loopback dum0, server bonds; cables labelled fabric / access / wan / sync", "117, 35"],
      ["VRFs, tenants", "IPAM → VRFs, namespace evpn-3tier: web, app, db, outside; route target 65500:<L3VNI>; tenant e3t-<VRF>", "4, 4"],
      ["VLANs", "IPAM → VLANs, group evpn-3tier: tier, transit, peering, L3VNI", "21"],
      ["Prefixes, IPs", "IPAM, namespace evpn-3tier: each prefix with its VRF, VLAN, tenant, location", "25, 38"],
      ["Config context", "Extensibility → Config Contexts → evpn-3tier: evpn plan (VRFs, VLANs, VNIs, gateways), fw_policy, lb_peerings, WAN, MTU", "1"]];
    const tw = 6.9;
    s.table(rows, tableOpts(L, TOP, tw, [1.45, tw - 2.15, 0.7], { fontSize: 11.5, rowH: 0.5 }));
    const d = diagram(s, "nb-devices", L + tw + 0.35, TOP, R - (L + tw + 0.35), BOT - TOP - 0.85, "Nautobot's device list filtered to location evpn-3tier: 19 devices with role, type, rack and management address");
    s.text("Devices → Devices, filtered to location evpn-3tier", { x: d.x, y: d.y + d.h + 0.08, w: d.w, h: 0.3, fontSize: 11, color: C.text2, margin: 0 });
    card(s, d.x, BOT - 0.62, R - d.x, 0.62, C.accent1, 88);
    s.text([{ text: "Seeded once ", options: { bold: true } }, { text: "from lab.conf by tools/nautobot_seed.py (idempotent); changed afterwards only by Fabric Factory's workflows." }],
      { x: d.x + 0.15, y: BOT - 0.62, w: R - d.x - 0.3, h: 0.62, fontSize: 12, color: C.text1, margin: 0, valign: "middle" });
    s.notes("Five names keep the lab apart on the shared NMS: location, namespace, VLAN group, tenant group and config context are all evpn-3tier, and every device is e3t-<node> because Nautobot names are global. The config context holds what Nautobot has no model for: VNIs, the anycast MAC, the route-target AS, transit and WAN plans, the firewall policy and load balancer peerings. Each device's local config context carries its index, ASN and console port.");
  }

  // 10c How the workflows write to Nautobot
  {
    const s = content("Architecture", "How each request writes to Nautobot", "REST calls, each after a read of the same object, so a retry skips what already exists");
    const rows = [[th("Workflow"), th("REST calls, in order")],
      [{ text: "Add a VLAN", options: { bold: true } }, "PATCH config-contexts/{id}: append the VLAN to evpn.vlans\nPOST ipam/vlans: vlan_<ID>_<VRF>, group evpn-3tier, tenant\nPOST ipam/prefixes, PATCH its location, POST vrf-prefix-assignments\nPATCH config-contexts/{id}: three standard ICMP rules in fw_policy"],
      [{ text: "Remove a VLAN", options: { bold: true } }, "PATCH config context: out of evpn.vlans, its rules out of fw_policy\nDELETE ipam/prefixes/{id}, DELETE ipam/vlans/{id}"],
      [{ text: "LB peering", options: { bold: true } }, "PATCH config context: append to lb_peerings\nPOST ipam/vlans peering-<LB>; POST prefixes (peering /29, VIPs) + location + VRF"]];
    const tw = 6.6;
    s.table(rows, tableOpts(L, TOP, tw, [1.5, tw - 1.5], { fontSize: 12, valign: "top", margin: [5, 6, 5, 6] }));
    const x = L + tw + 0.35, w = R - x;
    const c1 = diagram(s, "nb-changelog-crop", x, TOP, w, BOT - TOP - 0.4, "Nautobot's change log for request 18: config context updated, VLAN vlan_47_web created, prefix 172.16.6.0/24 created and updated, location and VRF updated");
    s.text("Nautobot's change log: request #18 (Add VLAN 47), newest first", { x, y: c1.y + c1.h + 0.06, w, h: 0.28, fontSize: 11, color: C.text2, margin: 0 });
    const cy = BOT - 1.35;
    card(s, L, cy, tw, 1.35, C.accent1, 88);
    await badge(s, L + 0.22, cy + 0.38, 0.6, "LuHistory", C.accent1);
    tb(s, L + 1.0, cy + 0.1, tw - 1.2, 1.15, "Every write is traceable",
      "Nautobot's change log records each call; every VLAN and prefix a request creates names the request in its description (\u201cself-service request #18\u201d).", { hs: 14, bs: 12.5, valign: "middle" });
    s.notes("The worker uses Nautobot's REST API with a token read from the NMS at run time. Before each POST it GETs the object by name, VID or prefix; a retry therefore finds what an earlier attempt created. Nautobot's change log records every write: for request #18 the plan entry, the VLAN object, the prefix and its location and VRF assignment, and finally the Standard Firewall Rules.");
  }

  // 10d How it is read back
  {
    const s = content("Architecture", "How it is read back: one GraphQL query per reader", "Each reader asks /api/graphql/ once and gets devices, cabling and the config context in a single answer");
    const code = `query {
  config_contexts(name: ["evpn-3tier"]) { data }
  devices(location: ["evpn-3tier"]) {
    name local_config_context_data
    role { name } rack { name }
    platform { name } primary_ip4 { address }
    interfaces {
      name description mac_address mtu
      vrf { name }
      ip_addresses { address }
      cable { label }
      cable_peer_interface {
        name device { name }
      }
    }
  }
}`;
    const cw = 4.9;
    s.shape(T.roundRect, { x: L, y: TOP, w: cw, h: BOT - TOP, rectRadius: 0.08, fill: { color: C.text1 }, line: { type: "none" } });
    s.text("The pipelines' inventory query", { x: L + 0.25, y: TOP + 0.15, w: cw - 0.5, h: 0.35, fontSize: 13, bold: true, color: C.background1, margin: 0 });
    s.text(code, { x: L + 0.25, y: TOP + 0.6, w: cw - 0.5, h: BOT - TOP - 0.75, fontFace: "Courier New", fontSize: 11.5, color: C.background2, margin: 0, valign: "top" });
    const x = L + cw + 0.35, w = R - x;
    const readers = [["Pipelines", "nautobot_inventory.py, every run → hosts.yml, group_vars, host_vars, committed to Gitea"],
      ["Fabric Factory forms", "labmodel.py, cached 30 s → used VLANs, VNIs, subnets, ASNs; switch pairs; removable VLANs"],
      ["Fabric Dashboard", "fabric.py, cached 30 s → counts, design KPIs, topology"]];
    const rh = 0.62;
    readers.forEach(([h, b], i) => {
      const y = TOP + i * (rh + 0.08);
      card(s, x, y, w, rh, C.accent1, 90);
      s.text([{ text: h + "   ", options: { bold: true, color: C.text1 } }, { text: b, options: { color: C.text2 } }], { x: x + 0.2, y, w: w - 0.35, h: rh, fontSize: 12.5, margin: 0, valign: "middle" });
    });
    const y = TOP + 3 * (rh + 0.08) + 0.05;
    const g = diagram(s, "nb-graphql", x, y, w, BOT - y - 0.33, "Nautobot's GraphiQL: the leaves with racks, management addresses and uplink cables, and prefix 172.16.6.0/24 with VRF and VLAN", "center");
    s.text("The same kind of query in Nautobot's GraphiQL", { x: g.x, y: g.y + g.h + 0.05, w: g.w, h: 0.28, fontSize: 11, color: C.text2, margin: 0 });
    s.notes("Nothing walks the REST API object by object to rebuild the plan. The pipelines' query returns the config context and every device with its role, rack, platform, primary IPv4, local context and every interface's description, MAC, MTU, VRF, addresses, cable label and cable peer; the inventory script turns that into the same lab model lab.conf produces. Fabric Factory's lab model and the Fabric Dashboard send similar queries; the executors' guards use plain REST GETs before each write.");
  }

  // ======================================================================================================================
  sectionSlide("Automation and orchestration", 2, "Automation and orchestration", "How Fabric Factory sequences phpIPAM, Nautobot and four Gitea pipelines, and how Ansible renders, applies and verifies");

  // 12 Orchestration architecture
  {
    const s = content("Automation and orchestration", "Orchestration architecture", "Fabric Factory orchestrates in order; the pipelines are the only thing that configures a device");
    const d = diagram(s, "orchestration", L, TOP, 8.4, BOT - TOP, "Automation and orchestration: browser, Fabric Factory API and worker, phpIPAM, Nautobot, Gitea, runner, Ansible and the devices");
    const px = d.x + d.w + 0.35, pw = R - px;
    const pts = [["Solid arrows", "requests and writes"], ["Dashed arrows", "reads and observation"],
      ["One worker", "carries out one approved request at a time; restarts mark it failed and retryable"],
      ["Device tools", "a fixed command list over SSH, with redaction and an audit log"]];
    pts.forEach(([h, b], i) => tb(s, px, TOP + i * 1.25, pw, 1.15, h, b, { hs: 14, bs: 13 }));
    s.notes("The worker talks to phpIPAM over REST with a static app token, to Nautobot over REST and GraphQL, to Gitea to dispatch workflows and follow job logs, and to devices over SSH for checks and the ping test.");
  }

  // 13 Interfaces
  {
    const s = content("Automation and orchestration", "Interfaces between the components", "Every hop, how it authenticates, and what it carries");
    const rows = [[th("From → to"), th("Protocol and authentication"), th("Carries")],
      ["Browser → Fabric Factory", "HTTP; Django session cookie, CSRF token on every change", "JSON API calls; server-sent events (1 s)"],
      ["Worker → phpIPAM", "REST over TLS, static application token", "Free subnets and VLANs; reserve and release"],
      ["Worker → Nautobot", "REST (writes) and GraphQL (reads), API token", "Config context edits; VLAN and prefix objects"],
      ["Worker → Gitea", "REST dispatch + the web job endpoint with log cursors", "Run start, status, and the live log"],
      ["Pipeline → Nautobot", "GraphQL, token from a Gitea Actions secret", "Devices, cables, config context → inventory"],
      ["Pipeline → Gitea", "git over HTTP, the job's own token", "The rendered inventory, committed [skip ci]"],
      ["Ansible → devices", "SSH on management; vyos.vyos (paramiko), doas on Linux", "Configuration lines, files, services"],
      ["Worker / tools → devices", "SSH (paramiko) on management", "Post-change checks, ping test, configs, state"]];
    s.table(rows, tableOpts(L, TOP, CW, [3.0, 5.0, CW - 8.0], { fontSize: 13, rowH: 0.52 }));
    s.notes("Secrets: the Nautobot token is a Gitea Actions secret; the worker reads Gitea and Nautobot credentials from the NMS at run time and phpIPAM's from a mode-600 file. None is stored in a repository.");
  }

  // 14 Pipeline anatomy
  {
    const s = content("Automation and orchestration", "Anatomy of a pipeline run", "The same five steps in all four repositories: switches, firewalls, load balancers, servers");
    const d = diagram(s, "pipeline", L, TOP, CW, 3.0, "Pipeline run: inventory, render, check, apply, verify", "center");
    const pr = [["LuDatabase", "Inventory from Nautobot", "Committed back every run, so each run's input is versioned"],
      ["LuEye", "Check before apply", "--check --diff records exactly what will change"],
      ["LuTrash2", "Managed deletions", "Lines the templates no longer produce are deleted; more than 25 on one device stops for a human"],
      ["LuLock", "One at a time", "Runner capacity 1 and a shared file lock with the lab's tests"],
      ["LuShieldCheck", "Protected paths", "Logins, console, syslog, NTP, loopbacks, NIC settings are never deleted"],
      ["LuListChecks", "Verify gates the run", "61 quick checks after every apply; a red verify fails it"]];
    const cols = 3, iw = (CW - 2 * 0.35) / cols, rh = 1.05, y0 = d.y + d.h + 0.3;
    for (const [i, [g, h, b]] of pr.entries()) {
      const x = L + (i % cols) * (iw + 0.35), y = y0 + Math.floor(i / cols) * (rh + 0.12);
      await badge(s, x, y, 0.45, g, C.accent1);
      tb(s, x + 0.6, y - 0.02, iw - 0.6, rh, h, b, { hs: 14, bs: 12 });
    }
    s.notes("On VyOS only lines missing from the running configuration are committed, so runs are idempotent and a run with nothing to do reports changed=0. Managed deletions are what make removal by intent safe.");
  }

  // 15 How Fabric Factory orchestrates
  {
    const s = content("Automation and orchestration", "How Fabric Factory orchestrates", "Ordered, idempotent stages; guards before writes; the pipelines followed live; proof before done");
    const rows = [["LuListOrdered", "Ordered, idempotent stages", "Each stage checks what already exists, so a retry skips completed work. A reservation tagged with the request number is recognised as its own."],
      ["LuShieldAlert", "Guards before writes", "Every check runs before the first write to a system, so a refusal changes nothing."],
      ["LuRadio", "Following the pipelines live", "Gitea 1.24 has no API for a running job's log, so the worker polls the web UI's job endpoint with log cursors every 3 s and parses PLAY, TASK and verify lines."],
      ["LuCircleCheck", "Proof, not assumption", "Each workflow ends with checks on the devices and, for VLANs, a ping test from the WAN."]];
    const lw = 6.6, rh = 1.2;
    for (const [i, [g, h, b]] of rows.entries()) {
      const y = TOP + i * (rh + 0.07);
      await badge(s, L, y, 0.55, g, C.accent1);
      tb(s, L + 0.75, y - 0.02, lw - 0.75, rh, h, b, { hs: 15, bs: 13 });
    }
    const cx = L + lw + 0.4, cw = R - cx;
    s.text("Progress snapshot on a running request (abridged)", { x: cx, y: TOP, w: cw, h: 0.3, fontSize: 11, color: C.text2, margin: 0 });
    const code = `{
  "stage": "switches",
  "pipeline": {
    "repo": "evpn-3tier-switches",
    "run": 23,
    "status": "running",
    "steps": [
      {"name": "inventory", "status": "success"},
      {"name": "render",    "status": "success"},
      {"name": "check",     "status": "success"},
      {"name": "apply",     "status": "running",
       "play": "Configure VyOS",
       "task": "Commit missing lines",
       "hosts": {"leaf1": "changed",
                 "leaf2": "ok"}}
    ]
  },
  "updated": "2026-11-09T04:33:41Z"
}`;
    s.shape(T.roundRect, { x: cx, y: TOP + 0.4, w: cw, h: BOT - TOP - 0.4, rectRadius: 0.08, fill: { color: C.text1 }, line: { type: "none" } });
    s.text(code, { x: cx + 0.2, y: TOP + 0.55, w: cw - 0.4, h: BOT - TOP - 0.7, fontFace: "Courier New", fontSize: 11, color: C.background2, margin: 0, valign: "top" });
    s.notes("The browser receives the snapshot through server-sent events; each request page shows the stage list, the pipeline's steps, the current Ansible play and task and per-host results, with the full step log one click away.");
  }

  // 16 Workflows + lifecycle
  {
    const s = content("Automation and orchestration", "Fabric Factory workflows", "Users choose little; the app works out the rest from phpIPAM and Nautobot, and an operator approves");
    const wf = [["Add a VLAN", "Choose the VRF and span", "Next unused VLAN and VNI, a free /24, gateway = first host, name vlan_ID_VRF", ["phpIPAM", "Nautobot", "FW rules", "switches", "firewalls", "checks", "ping"], C.accent3],
      ["Remove a VLAN", "Choose the VLAN and confirm", "Removable? Not the lab's design, no hosts, not the VRF's last VLAN", ["pre-flight", "Nautobot", "switches", "firewalls", "checks", "phpIPAM"], C.accent4],
      ["Load balancer peering", "ASN, peering VLAN, VIPs, BFD", "The border pair and VRF, from the load balancer's cabling in Nautobot", ["Nautobot", "switches", "LBs", "phpIPAM", "checks"], C.accent1]];
    const cw = (CW - 2 * 0.3) / 3, ch = 2.85;
    wf.forEach(([n, choose, works, stages, c], i) => {
      const x = L + i * (cw + 0.3);
      card(s, x, TOP, cw, ch, c, 92);
      s.text(n, { x: x + 0.25, y: TOP + 0.18, w: cw - 0.5, h: 0.4, fontSize: 17, bold: true, color: c, margin: 0 });
      s.text([{ text: "You choose: ", options: { bold: true, color: C.text1 } }, { text: choose, options: { color: C.text2, breakLine: true } },
        { text: "App works out: ", options: { bold: true, color: C.text1 } }, { text: works, options: { color: C.text2 } }],
        { x: x + 0.25, y: TOP + 0.65, w: cw - 0.5, h: 1.3, fontSize: 13, margin: 0, valign: "top", paraSpaceAfter: 4 });
      // stage chips
      let cx = x + 0.25, cy = TOP + 2.0;
      stages.forEach((st) => {
        const w = 0.13 + st.length * 0.085;
        if (cx + w > x + cw - 0.2) { cx = x + 0.25; cy += 0.38; }
        s.shape(T.roundRect, { x: cx, y: cy, w, h: 0.3, rectRadius: 0.15, fill: { color: C.background1 }, line: { color: c, width: 0.75 } });
        s.text(st, { x: cx, y: cy, w, h: 0.3, fontSize: 10, color: C.text1, align: "center", valign: "middle", margin: 0 });
        cx += w + 0.08;
      });
    });
    diagram(s, "states", L, TOP + ch + 0.3, CW, BOT - TOP - ch - 0.3, "Request lifecycle: submitted, approved, running, done or failed; retry and cancel", "center");
    s.notes("Forms are validated against a live model of the lab built from Nautobot: VLAN IDs, VNIs, ASNs and overlapping subnets already in use are refused, including those held by other open requests. Values are re-checked at submission and at execution.");
  }

  // 16b Auto-filled fields
  {
    const s = content("Automation and orchestration", "Most fields fill themselves, by design", "When a user requests a VLAN, the system picks the next available VLAN ID and subnet, without being asked");
    const d = diagram(s, "form", L, TOP, 4.9, BOT - TOP, "The Add a VLAN form after one click: VLAN 48, VNI 10048, vlan_48_web, gateway 172.16.7.1/24 from phpIPAM");
    const x = d.x + d.w + 0.45, w = R - x;
    s.text("THE USER CHOOSES", { x, y: TOP, w, h: 0.3, fontSize: 12, bold: true, color: C.text2, charSpacing: 2, margin: 0 });
    let cx = x;
    for (const t of ["VRF", "Span", "Reason"]) {
      const cw = 0.35 + t.length * 0.13;
      s.shape(T.roundRect, { x: cx, y: TOP + 0.38, w: cw, h: 0.42, rectRadius: 0.21, fill: { color: C.background1 }, line: { color: C.text2, width: 1 } });
      s.text(t, { x: cx, y: TOP + 0.38, w: cw, h: 0.42, fontSize: 14, bold: true, color: C.text1, align: "center", valign: "middle", margin: 0 });
      cx += cw + 0.15;
    }
    s.text("FILLED IN AUTOMATICALLY", { x, y: TOP + 1.05, w, h: 0.3, fontSize: 12, bold: true, color: C.accent3, charSpacing: 2, margin: 0 });
    const auto = [["VLAN ID", "the next unused ID: Nautobot, phpIPAM and open requests"], ["L2VNI", "10000 + VLAN ID"], ["Name", "vlan_<ID>_<VRF>"],
      ["Subnet", "phpIPAM's first free /24 that overlaps nothing"], ["Gateway", "always the subnet's first host"]];
    const rh = 0.42;
    auto.forEach(([f, how], i) => {
      const y = TOP + 1.42 + i * (rh + 0.05);
      card(s, x, y, w, rh, C.accent3, 90);
      s.text([{ text: f, options: { bold: true, color: C.text1 } }, { text: "   " + how, options: { color: C.text2 } }], { x: x + 0.2, y, w: w - 0.3, h: rh, fontSize: 14, margin: 0, valign: "middle" });
    });
    const cy = TOP + 1.42 + 5 * (rh + 0.05) + 0.15;
    card(s, x, cy, w, BOT - cy, C.accent1, 88);
    await badge(s, x + 0.25, cy + 0.27, 0.5, "LuShieldCheck", C.accent1);
    tb(s, x + 0.95, cy + 0.14, w - 1.15, BOT - cy - 0.2, "Why: fewer fields, fewer mistakes",
      "No typos, duplicate IDs or overlapping subnets; re-checked at submission and at execution.", { hs: 15, bs: 13, valign: "middle" });
    s.notes("This is by design. Hand-typed VLAN IDs, VNIs and subnets are where network changes most often go wrong. The requester presses one button, Select the next unused VLAN ID; the allocator takes the lowest unused ID above the highest tier VLAN, skipping anything in Nautobot, phpIPAM or an open request, and asks phpIPAM for its first free /24 under 172.16.0.0/16. The gateway is always the first host. Values are checked again when the request is submitted and when the worker carries it out.");
  }

  // 17 App architecture
  {
    const s = content("Automation and orchestration", "Inside Fabric Factory", "A React front end, a Django Ninja API, one worker and a live event stream, on one origin with one session cookie");
    const layers = [["LuLayoutDashboard", "Front end", "React 19 + TypeScript, built with Vite; served by Django from the same origin, so no CORS"],
      ["LuServer", "API", "Django 6.1 + Django Ninja, OpenAPI at /api/docs; gunicorn with uvicorn workers (ASGI)"],
      ["LuCog", "Worker", "A management command as its own service; one approved request at a time"],
      ["LuZap", "Live updates", "/api/stream sends a server-sent event per changed request each second; no broker"]];
    const lw = 7.2, rh = 1.2;
    for (const [i, [g, h, b]] of layers.entries()) {
      const y = TOP + i * (rh + 0.07);
      card(s, L, y, lw, rh, C.accent1, 94);
      await badge(s, L + 0.25, y + 0.3, 0.6, g, C.accent1);
      tb(s, L + 1.1, y + 0.13, lw - 1.3, rh - 0.2, h, b, { hs: 15, bs: 13, valign: "middle" });
    }
    const x = L + lw + 0.4, w = R - x;
    await badge(s, x, TOP, 0.6, "LuLock", C.accent6);
    tb(s, x, TOP + 0.8, w, 4.1, "Security model", [
      "Requesters submit and see their own requests; operators see all and approve, reject and retry.",
      "Every change needs the session's CSRF token.",
      "Device tools run only commands from a fixed server-side list, redact hashes and keys, and log who asked.",
      "Credentials never live in a repository."], { hs: 18, bs: 14, ps: 9 });
    s.notes("Read-only tools: View Device Configurations (view and download, a zip of all), Retrieve Device State (interfaces, routes, BGP including AS paths, EVPN, MAC tables and more) and the Fabric Dashboard (the fabric at a glance, design KPIs and a live check).");
  }

  // 17b Fabric Dashboard
  {
    const s = content("Automation and orchestration", "The Fabric Dashboard", "The whole fabric at a glance, from Nautobot, with the KPIs its design implies and a live check against it");
    const d = diagram(s, "dashboard", L, TOP, 6.0, BOT - TOP, "The Fabric Dashboard: tiles for switches, VRFs, subnets, VLANs, devices, links, ASNs and firewall rules; design KPIs; the topology drawn from Nautobot's cables");
    const x = d.x + d.w + 0.4, w = R - x;
    const rows = [
      ["LuLayoutGrid", C.accent1, "At a glance, from Nautobot", "8 switches by role, 19 devices by model, 4 VRFs, 25 subnets, 11 tier VLANs, a topology drawn from the 35 cables"],
      ["LuGauge", C.accent3, "Design KPIs", "2-way ECMP · 1.5:1 oversubscription · 120 Gbps fabric · 50% left after a spine fails · 100% dual-homed · 1 single point of failure (wan)"],
      ["LuActivity", C.accent4, "Live health vs. design", "Read-only SSH: 19/19 reachable · 12/12 fabric sessions · BGP 64/64 · BFD 64/64 · the same 14 VNIs on every leaf · one VyOS version"],
      ["LuHistory", C.accent6, "Change KPIs", "16 changes completed · 100% success · median 5m 22s from approval to a verified change"]];
    const gap = 0.12, rh = (BOT - TOP - 3 * gap) / 4;
    for (const [i, [g, c, h, b]] of rows.entries()) {
      const y = TOP + i * (rh + gap);
      card(s, x, y, w, rh, c, 90);
      await badge(s, x + 0.22, y + (rh - 0.55) / 2, 0.55, g, c);
      tb(s, x + 0.95, y + 0.1, w - 1.1, rh - 0.2, h, b, { hs: 15, bs: 12.5, valign: "middle" });
    }
    s.notes("A Tools card on the New request page; read-only, so no approval. The design half is one GraphQL query to Nautobot: devices with roles, models, racks and cabling; the plan's VRFs, VLANs, transit and peerings. KPIs are computed from it: ECMP width is the uplinks per leaf, oversubscription is server-facing over spine-facing bandwidth, dual-homing counts endpoints cabled to two switches. The live half asks every device over SSH with fixed commands (BGP and BFD summaries, EVPN VNIs, show version) and compares: one spine EVPN session per spine link, every leaf with the same VNIs. Both are cached for 30 seconds; the page reloads its design when a request finishes.");
  }

  // 17c ASN and VLAN databases
  {
    const s = content("Automation and orchestration", "The ASN and VLAN databases", "Every AS and every VLAN of the fabric, from Nautobot, searchable, and downloadable as an Excel workbook");
    const h = BOT - TOP - 0.32;
    const a = diagram(s, "asn-db", L, TOP, 3.9, h, "The ASN Database: 12 ASNs and 34 BGP sessions, a table of every ASN with devices, router IDs, peers and VRFs");
    s.text("ASN Database", { x: a.x, y: a.y + a.h + 0.05, w: a.w, h: 0.26, fontSize: 11, color: C.text2, margin: 0 });
    const v = diagram(s, "vlan-db", a.x + a.w + 0.25, TOP, 3.9, h, "The VLAN Database: 20 VLANs with name, type, VRF, VNI, subnet, gateway, span, hosts and origin");
    s.text("VLAN Database", { x: v.x, y: v.y + v.h + 0.05, w: v.w, h: 0.26, fontSize: 11, color: C.text2, margin: 0 });
    const x = v.x + v.w + 0.4, w = R - x;
    const rows = [["LuNetwork", C.accent1, "ASN Database", "12 ASNs — spines, leaves, borders, firewalls, WAN, load balancers, route target — with devices, router IDs, peers, VRFs; all 34 BGP sessions"],
      ["LuTable", C.accent3, "VLAN Database", "20 VLANs: tier, firewall transit, LB peering, L3VNI — subnet, gateway, VRF, VNI, where it lives, hosts, and the request that made it"],
      ["LuDownload", C.accent4, "Excel download", "Built on the server: header row frozen, a filter on every column, numbers as numbers, an About sheet with the source and time"]];
    const gap = 0.12, rh = (BOT - TOP - 2 * gap) / 3;
    for (const [i, [g, c, hd, b]] of rows.entries()) {
      const y = TOP + i * (rh + gap);
      card(s, x, y, w, rh, c, 90);
      await badge(s, x + 0.2, y + 0.22, 0.5, g, c);
      tb(s, x + 0.85, y + 0.12, w - 1.0, rh - 0.2, hd, b, { hs: 14, bs: 11.5, valign: "top" });
    }
    s.notes("Two read-only tools next to the Fabric Dashboard. Both read Nautobot's model, the plan the pipelines deploy from, so they are never out of date. The ASN Database groups devices by AS and derives every BGP session of the design from the cabling and the plan: underlay and EVPN on the spine links, firewall transit per VRF, the WAN and the load balancer peerings. The VLAN Database lists every VLAN in the evpn-3tier group, with the self-service request that created it where there is one. Both download as Excel workbooks built with openpyxl.");
  }

  // 17d Port Provisioning
  {
    const s = content("Automation and orchestration", "Port Provisioning", "Enable or disable a leaf port; set a spare port up as access or trunk. Uplinks to the spines are blocked");
    const f = diagram(s, "port-form", L, TOP, 5.2, BOT - TOP - 0.3, "The Port Provisioning form: leaf1 eth6 enabled as a trunk with VLANs 41 and 47 chosen as chips");
    s.text("The form: a spare port as a trunk", { x: f.x, y: f.y + f.h + 0.04, w: f.w, h: 0.26, fontSize: 11, color: C.text2, margin: 0 });
    const x = f.x + f.w + 0.4, w = R - x;
    const rows = [["LuBan", C.accent4, "Uplinks to the spines", "Blocked: listed as such, refused by the form and by the worker"],
      ["LuServer", C.accent1, "A server's bond leg", "Enable or disable — disabled only while its other leg is up (Nautobot and live); its VLAN follows the server"],
      ["LuPlug", C.accent3, "Spare ports eth6, eth7", "Access (one VLAN, untagged), trunk (chosen VLANs, tagged) or no VLANs; only VLANs the leaves carry"]];
    const rh = 0.86, gap = 0.08;
    for (const [i, [g, c, hd, b]] of rows.entries()) {
      const y = TOP + i * (rh + gap);
      card(s, x, y, w, rh, c, 90);
      await badge(s, x + 0.18, y + 0.18, 0.5, g, c);
      tb(s, x + 0.82, y + 0.08, w - 0.95, rh - 0.12, hd, b, { hs: 13.5, bs: 11.5, valign: "middle" });
    }
    const y2 = TOP + 3 * (rh + gap) + 0.12;
    s.text("Intent in Nautobot's interface fields → switches pipeline (verify) → the port's state and bridge VLANs checked → ping from the WAN", { x, y: y2, w, h: 0.5, fontSize: 11.5, color: C.text1, margin: 0 });
    const r = diagram(s, "port-request", x, y2 + 0.58, w, BOT - y2 - 0.9, "Request #34 done: pre-flight, Nautobot, switches pipeline, port checks, ping test");
    s.text("Request #34: leaf1 eth6 a trunk for VLANs 41 and 47, about three minutes", { x, y: r.y + r.h + 0.04, w, h: 0.26, fontSize: 11, color: C.text2, margin: 0 });
    s.notes("Every leaf got two spare ports, eth6 and eth7, for this workflow; they start disabled in Nautobot. The intent lives in Nautobot's native interface fields: enabled, mode, untagged VLAN, tagged VLANs and description. The switches pipeline renders disable, native-vlan for access or allowed-vlan per VLAN for a trunk, and its managed deletions remove whatever no longer applies. Tested live: a trunk, then access, then disabled; web1's leg disabled and re-enabled with web1 serving throughout; an uplink change refused.");
  }

  // 18 Sequence
  {
    const s = content("Automation and orchestration", "Add a VLAN, end to end", "Seven systems, about six minutes, almost all of it in the two pipeline runs");
    const d = diagram(s, "sequence", L, TOP, 7.6, BOT - TOP, "Add a VLAN sequence across requester, operator, Fabric Factory, phpIPAM, Nautobot, Gitea and Ansible, devices and WAN");
    const px = d.x + d.w + 0.35, pw = R - px;
    const steps = ["Pick a VRF; the app fills in VLAN 45, vlan_45_app and 172.16.4.0/24 itself", "Operator approves", "phpIPAM reserves VLAN, subnet, gateway",
      "Nautobot: plan entry, VLAN and prefix objects", "Standard Firewall Rules added", "Switches pipeline: VNI, gateway, FABRIC-SUBNETS",
      "Firewalls pipeline: PUBLIC + ICMP rules", "Checks on devices and WAN; ping 7/7 from the WAN"];
    const rh = (BOT - TOP) / steps.length;
    steps.forEach((t, i) => {
      const y = TOP + i * rh;
      s.shape(T.ellipse, { x: px, y: y + 0.05, w: 0.38, h: 0.38, fill: { color: C.accent1 }, line: { type: "none" } });
      s.text(String(i + 1), { x: px, y: y + 0.05, w: 0.38, h: 0.38, fontSize: 12, bold: true, color: C.background1, align: "center", valign: "middle", margin: 0 });
      s.text(t, { x: px + 0.5, y: y, w: pw - 0.5, h: rh, fontSize: 13, color: C.text1, margin: 0, valign: "top" });
    });
    s.notes("Remove a VLAN mirrors this with phpIPAM last: pre-flight; Nautobot (plan entry, prefix, VLAN object, its standard rules); both pipelines, whose managed deletions remove every line; checks that no trace remains and the WAN dropped the route; then phpIPAM releases the subnet, gateway and VLAN. Requests 14 and 15 did exactly this for VLAN 45.");
  }

  // ======================================================================================================================
  sectionSlide("Resilience, stack and operations", 3, "Resilience, stack and operations", "Measured failure behaviour, the full tech stack with versions, design decisions, and what is still missing");

  // 20 Resilience
  {
    const s = content("Resilience, stack and operations", "Every single failure is survived", "Measured under live load: an HTTPS read every 100 ms and an order every second; one component off for 20 s");
    const comps = ["lb1", "web1", "app1", "fw1", "leaf1", "border1", "db1"];
    const gaps = [0.1, 0.1, 0.1, 0.1, 0.1, 0.3, 3.3];
    s.chart(pres.ChartType.bar, [{ name: "Longest gap (s)", labels: comps, values: gaps }], {
      x: L, y: TOP, w: 6.3, h: BOT - TOP, barDir: "bar", catAxisOrientation: "maxMin",
      showTitle: true, title: "Longest gap in service (seconds)", titleFontSize: 14, titleColor: THEME.colors.dk1, titleFontFace: "+mn-lt",
      chartColors: [THEME.colors.accent1], showValue: true, dataLabelPosition: "outEnd", dataLabelFontSize: 12, dataLabelColor: THEME.colors.dk1, dataLabelFontFace: "+mn-lt",
      catAxisLabelColor: THEME.colors.dk2, catAxisLabelFontSize: 13, catAxisLabelFontFace: "+mn-lt", valAxisHidden: true,
      valGridLine: { style: "none" }, catGridLine: { style: "none" }, showLegend: false, valAxisMaxVal: 4, barGapWidthPct: 60,
    });
    const rows = [[th("Off"), th("What takes over")], ["lb1", "keepalived moves the VIP to lb2"], ["web1", "HAProxy health check, redispatch"], ["app1", "nginx uses the other app server"],
      ["fw1", "BFD + BGP to fw2; conntrack-sync keeps sessions"], ["leaf1", "Each server's other EVPN leg"], ["border1", "border2 for firewalls, LBs, WAN"], ["db1", "db2 promoted after 5 s; the app finds it"]];
    const x = L + 6.6, w = R - x;
    s.table(rows, tableOpts(x, TOP + 0.1, w, [1.1, w - 1.1], { rowH: 0.42 }));
    s.text([{ text: "0 lost after restore in every case.  ", options: { bold: true, color: C.text1 } }, { text: "verify runs 65 checks; 9 Robot Framework suites assert all of this.", options: { color: C.text2 } }],
      { x, y: TOP + 3.75, w, h: 1.0, fontSize: 14, margin: 0, valign: "top" });
    s.notes("Lost while down: lb1 0-4 in flight, web1 0-1, border1 4 reads, db1 32 reads and 3 writes; all others zero. The policy matrix tries every TCP flow from every tier and the WAN: 35 flows from the WAN, exactly 2 allowed.");
  }

  // 21-22 Tech stack
  const stack = (title, lead, rows, notes) => {
    const s = content("Resilience, stack and operations", title, lead);
    const body = [[th("Layer"), th("Component"), th("Version"), th("Runs on"), th("Role")], ...rows];
    s.table(body, tableOpts(L, TOP, CW, [1.7, 3.1, 2.3, 1.9, CW - 9.0], { rowH: 0.36 }));
    s.notes(notes);
  };
  stack("Tech stack: infrastructure and application", "Entirely open source; versions as deployed on 9 November 2026", [
    ["Compute", "Ubuntu Server", "26.04.1 LTS, kernel 7.0", "Lab host", "12 cores, 57 GB RAM"],
    ["Compute", "KVM / QEMU, libvirt", "10.2.1, 12.0.0", "Lab host", "VMs; UDP tunnels as cables"],
    ["Network OS", "VyOS (rolling)", "2026.09.09", "11 VMs", "Switches, firewalls, WAN"],
    ["Routing", "FRRouting", "10.6.1", "In VyOS", "BGP, EVPN, BFD, VRFs"],
    ["Firewall", "VyOS zones, conntrackd", "in VyOS", "fw1, fw2", "Zone policy, state sync, VRRP"],
    ["Server OS", "Alpine Linux", "3.22.4", "9 VMs", "OpenRC services"],
    ["Load balancing", "HAProxy, keepalived", "3.2.26, 2.3.1", "lb1, lb2", "TLS, health checks, VIP"],
    ["Load balancing", "BIRD", "2.17.2", "lb1, lb2", "eBGP VIP routes, BFD"],
    ["Web", "nginx", "1.28.3", "web1, web2", "Page, reverse proxy"],
    ["Application", "Python, psycopg", "3.12.13, psycopg 3", "app1, app2", "Order Desk API"],
    ["Database", "PostgreSQL", "17.11", "db1, db2", "Primary + hot standby"],
    ["Monitoring", "Prometheus, Grafana", "3.14.0, 13.0.2", "NMS (Docker)", "Metrics, alerts, dashboard"]],
    "Twenty VMs: 11 VyOS (2 spines, 4 leaves, 2 borders, 2 firewalls, the WAN) and 9 Alpine Linux (2 load balancers, 2 web, 2 app, 2 db, IPAM).");
  stack("Tech stack: automation and self-service", "The sources of truth, the pipelines, and Fabric Factory", [
    ["IPAM", "phpIPAM, MariaDB", "1.8.3, 11.8.8", "IPAM VM", "Addressing; REST API"],
    ["Source of truth", "Nautobot", "3.2.4", "NMS (Docker)", "Plan; REST and GraphQL"],
    ["CI/CD", "Gitea, Actions runner", "1.24.7, 3.5.0", "NMS; host", "Repos, pipelines, history"],
    ["Automation", "ansible-core", "2.20.11", "Runner venv", "Playbooks, Jinja, check mode"],
    ["Automation", "vyos.vyos + netcommon, utils", "6.2.0", "Runner venv", "VyOS modules, network_cli"],
    ["Self-service", "Django, Django Ninja", "6.1.3, 1.8.0", "Lab host", "Requests, worker, API"],
    ["Self-service", "gunicorn, uvicorn", "26.2.0, 0.54.0", "Lab host", "ASGI for the event stream"],
    ["Self-service", "SQLite", "with Python", "Lab host", "Requests, history, progress"],
    ["Front end", "React, TypeScript, Vite", "19.2.0, 5.9.3, 7.2.2", "Browser", "The Fabric Factory UI"],
    ["Build", "Node.js", "24.11.1 LTS", "Lab host", "Front-end build only"],
    ["SSH tooling", "paramiko", "4.0.0", "Lab host", "Device tools, checks, ping"],
    ["Testing", "Robot Framework", "7.5.1", "Lab host", "Nine test suites"]],
    "ansible-core is pinned to 2.20 because 2.21 removed the paramiko connection plugin the VyOS modules use on this host.");

  // 23 Design decisions
  {
    const s = content("Resilience, stack and operations", "Design decisions, learned the hard way", "Most of the design follows from failures found while building the lab; each is now guarded by a check or a test");
    const rows = [[th("Decision"), th("Why")],
      ["No /32s or transit addresses to the firewalls", "Forwarded, a /32 sent the firewalls' BFD through the wrong border and the sessions died"],
      ["Segment bonds raised only after EVPN converges", "A rebooting leaf attracted traffic about 6 s before it could forward it"],
      ["A virtual MAC for the web VIP", "With the holder's own MAC, half the flows went to a dead load balancer for minutes"],
      ["nginx evicts app servers only on connection errors", "Counting database-down answers turned a 3 s failover into 13 s of errors"],
      ["phpIPAM first when adding, last when removing", "Addresses are reserved before use and freed only after nothing uses them"],
      ["Rule numbers keyed to VLAN IDs", "Adding or removing a VLAN never renumbers other rules"],
      ["Inventory committed on every run", "Each run's exact input is versioned and diffable"],
      ["The worker follows Gitea's web job endpoint", "Without it, a pipeline run is minutes of silence"]];
    s.table(rows, tableOpts(L, TOP, CW, [5.2, CW - 5.2], { fontSize: 13, rowH: 0.52 }));
    s.notes("Other decisions: as-override on the firewalls' outside sessions; ansible-core pinned to 2.20; device tools with a fixed command list and redaction.");
  }

  // 24 Closing: limitations & roadmap
  {
    const s = wrap(pres.addSlide({ masterName: "Cover", sectionTitle: "Resilience, stack and operations" }), recSlide("Cover"));
    s.text("Limitations and roadmap", { x: L + 0.2, y: 0.6, w: CW, h: 0.9, fontSize: 36, bold: true, color: C.background1, margin: 0, valign: "middle" });
    const lim = [["Not reconciled", "phpIPAM and Nautobot are written in a fixed order, but nothing repairs a manual difference"],
      ["lab.conf lags the plan", "VLANs and peerings added through Fabric Factory live only in Nautobot (reported as drift)"],
      ["LB peering order", "Load balancer peering still writes phpIPAM last, unlike Add a VLAN"],
      ["Scale and security", "One host, SQLite, one worker, one pipeline at a time; self-signed certificates, plain HTTP"]];
    const cw = (CW - 0.4) / 2, ch = 1.5;
    lim.forEach(([h, b], i) => {
      const x = L + 0.2 + (i % 2) * (cw + 0.2), y = 1.9 + Math.floor(i / 2) * (ch + 0.25);
      s.shape(T.roundRect, { x, y, w: cw - 0.2, h: ch, rectRadius: 0.08, fill: { color: C.background1, transparency: 92 }, line: { type: "none" } });
      tb(s, x + 0.25, y + 0.2, cw - 0.7, ch - 0.4, h, b, { hs: 16, hc: C.background1, bc: C.background2 });
    });
    s.text([{ text: "Next:  ", options: { bold: true, color: C.accent5 } }, { text: "reconcile phpIPAM and Nautobot · layer-4 load-balancing experiments · more self-service workflows", options: { color: C.background1 } }],
      { x: L + 0.2, y: 5.6, w: CW - 0.4, h: 0.8, fontSize: 18, margin: 0, valign: "middle" });
    s.notes("Also known: after a firewall fail-back, the standby learns only connections whose state changes, so a long-idle connection can be missing on it.");
  }

  await pres.writeFile({ fileName: out });
  await applyTheme(out, THEME);
  fs.writeFileSync(out.replace(/\.pptx$/, ".rec.json"), JSON.stringify(rec));
  console.log("wrote", out, rec.slides.length, "slides");
})().catch((e) => { console.error(e); process.exit(1); });
