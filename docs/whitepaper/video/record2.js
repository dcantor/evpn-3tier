// Records a captioned tour of Fabric Factory with a live Add-a-VLAN run, as JPEG frames + timings (frames/, timing.json).
// node record.js <vrf>
const fs = require("fs");
const path = require("path");
const puppeteer = require("puppeteer-core");
// Fabric Factory's admin login, read at run time from ~/.config/evpn-3tier/selfservice.env (never stored here)
const FF = (() => {
  const t = require("fs").readFileSync(require("os").homedir() + "/.config/evpn-3tier/selfservice.env", "utf8");
  const g = (k) => ((t.match(new RegExp("^" + k + "=(.*)$", "m")) || [])[1] || "").trim().replace(/^["']|["']$/g, "");
  return { user: g("ADMIN_USER") || "admin", pass: g("ADMIN_PASSWORD") };
})();

const { execFileSync } = require("child_process");
const NB = "http://10.0.0.10:8080";
const nbEnv = (k) => execFileSync("ssh", ["-o", "BatchMode=yes", "lab@10.0.0.10", `grep ^${k}= /opt/nautobot/.env | cut -d= -f2-`]).toString().trim();

const BASE = "http://127.0.0.1:8120";
const VRF = process.argv[2] || "web";
const OUT = path.join(__dirname, "part2");
fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT);
const LOGO = "data:image/png;base64," + fs.readFileSync(path.join(__dirname, "../deck/img/logo.png")).toString("base64");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

// ---- overlay injected into every document: caption bar, speed badge, chapter card, fake cursor, highlight box ----------
const OVERLAY = `(() => {
  const mk = () => {
    if (document.getElementById("__demo")) return;
    const st = document.createElement("style");
    st.textContent = \`
      #__demo{position:fixed;inset:0;pointer-events:none;z-index:2147483647;font-family:Inter,Ubuntu,'Noto Sans',sans-serif}
      #__cap{position:absolute;left:50%;bottom:26px;transform:translateX(-50%);max-width:1080px;background:rgba(15,19,48,.92);color:#fff;
        padding:13px 24px;border-radius:14px;font-size:19px;line-height:1.4;box-shadow:0 8px 30px rgba(0,0,0,.25);transition:opacity .35s;text-align:left}
      #__cap b{color:#c7c9ff} #__cap .k{display:block;font-size:12.5px;letter-spacing:.12em;text-transform:uppercase;color:#a5a8ff;margin-bottom:3px;font-weight:700}
      #__spd{position:absolute;right:22px;top:66px;background:#4f46e5;color:#fff;font-weight:700;font-size:15px;padding:7px 14px;border-radius:999px;opacity:0;transition:opacity .3s}
      #__card{position:absolute;inset:0;background:#0f1330;color:#fff;display:flex;flex-direction:column;justify-content:center;padding:0 120px;opacity:0;transition:opacity .5s}
      #__card .brand{display:flex;align-items:center;gap:18px;font-weight:800;letter-spacing:.2em;font-size:20px;margin-bottom:46px;color:#fff}\n      #__card h1{color:#fff}
      #__card h1{font-size:54px;line-height:1.12;margin:0 0 22px;max-width:1050px} #__card p{font-size:24px;color:#c9cdf2;margin:0;max-width:1000px;line-height:1.45}
      #__card .n{color:#8b85ff;font-size:22px;font-weight:800;letter-spacing:.14em;margin-bottom:14px}
      #__cur{position:absolute;left:0;top:0;width:26px;height:26px;transition:transform .75s cubic-bezier(.45,.05,.25,1);filter:drop-shadow(0 2px 3px rgba(0,0,0,.35))}
      #__rip{position:absolute;width:44px;height:44px;margin:-22px 0 0 -22px;border-radius:50%;border:3px solid #4f46e5;opacity:0}
      #__rip.go{animation:rip .55s ease-out}
      @keyframes rip{from{opacity:.9;transform:scale(.3)}to{opacity:0;transform:scale(1.4)}}
      #__hl{position:absolute;border:3px solid #f59e0b;border-radius:8px;box-shadow:0 0 0 9999px rgba(15,19,48,.18);opacity:0;transition:opacity .4s, all .4s}\`;
    document.head.appendChild(st);
    const d = document.createElement("div");
    d.id = "__demo";
    d.innerHTML = '<div id="__hl"></div><div id="__card"></div><div id="__cap" style="opacity:0"></div><div id="__spd"></div><div id="__rip"></div>' +
      '<svg id="__cur" viewBox="0 0 24 24"><path d="M4 2l15 10.5-6.6 1.3 3.9 7.4-2.8 1.5-3.9-7.5L4 20z" fill="#111" stroke="#fff" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    document.body.appendChild(d);
    const p = window.__curPos || [720, 420];
    document.getElementById("__cur").style.transition = "none";
    document.getElementById("__cur").style.transform = "translate(" + p[0] + "px," + p[1] + "px)";
  };
  if (document.body) mk(); else document.addEventListener("DOMContentLoaded", mk);
})();`;

(async () => {
  const browser = await puppeteer.launch({ executablePath: "/usr/bin/google-chrome", headless: "new",
    args: ["--no-sandbox", "--hide-scrollbars", "--force-color-profile=srgb"] });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 810, deviceScaleFactor: 4 / 3 });
  await page.evaluateOnNewDocument(OVERLAY);
  await page.emulateMediaFeatures([{ name: "prefers-color-scheme", value: "light" }]);

  // ---- recorder: CDP screencast frames on a virtual clock (speed > 1 = time-lapse) --------------------------------------
  const cdp = await page.createCDPSession();
  const times = [];
  let speed = 1, vclock = 0, lastTs = null, n = 0;
  cdp.on("Page.screencastFrame", async (f) => {
    const ts = f.metadata.timestamp;
    if (lastTs !== null) vclock += (ts - lastTs) / speed;
    lastTs = ts;
    fs.writeFileSync(path.join(OUT, `${String(n).padStart(6, "0")}.jpg`), Buffer.from(f.data, "base64"));
    times.push(vclock); n++;
    try { await cdp.send("Page.screencastFrameAck", { sessionId: f.sessionId }); } catch {}
  });
  const startRec = () => cdp.send("Page.startScreencast", { format: "jpeg", quality: 88, maxWidth: 1920, maxHeight: 1080, everyNthFrame: 1 });

  // ---- helpers --------------------------------------------------------------------------------------------------------
  let cur = [720, 420];
  const ev = (fn, ...a) => page.evaluate(fn, ...a);
  const ready = async () => { await page.waitForSelector("#__demo", { timeout: 15000 }); await ev((p) => { window.__curPos = p; }, cur); };
  const caption = async (kicker, html) => { await ready(); await ev((k, h) => { const c = document.getElementById("__cap");
    if (!h) { c.style.opacity = 0; return; } c.innerHTML = (k ? '<span class="k">' + k + "</span>" : "") + h; c.style.opacity = 1; }, kicker, html); };
  const badge = async (t) => ev((t) => { const b = document.getElementById("__spd"); b.textContent = t || ""; b.style.opacity = t ? 1 : 0; }, t);
  const card = async (html) => { await ready(); await ev((h) => { const c = document.getElementById("__card"); if (h) c.innerHTML = h; c.style.opacity = h ? 1 : 0; document.getElementById("__cur").style.visibility = h ? "hidden" : "visible"; }, html); };
  const hold = (s) => sleep(s * 1000);
  const box = async (sel) => {
    const h = typeof sel === "string" ? await page.waitForSelector(sel, { visible: true, timeout: 20000 }) : sel;
    await h.evaluate((e) => { const r = e.getBoundingClientRect(); if (r.top < 70 || r.bottom > window.innerHeight - 150) e.scrollIntoView({ block: "center", behavior: "instant" }); });
    return { h, b: await h.boundingBox() };
  };
  const moveTo = async (sel, dx = 0.5, dy = 0.5) => {
    const { h, b } = await box(sel);
    cur = [b.x + b.width * dx, b.y + b.height * dy];
    await ev((p) => { const c = document.getElementById("__cur"); c.style.transition = ""; c.style.transform = `translate(${p[0]}px,${p[1]}px)`; window.__curPos = p; }, cur);
    await sleep(850);
    return h;
  };
  const click = async (sel, dx, dy) => {
    await moveTo(sel, dx, dy);
    await ev((p) => { const r = document.getElementById("__rip"); r.style.left = p[0] + "px"; r.style.top = p[1] + "px"; r.classList.remove("go"); void r.offsetWidth; r.classList.add("go"); }, cur);
    await page.mouse.click(cur[0], cur[1]);
    await sleep(500);
  };
  const type = async (sel, text) => { await click(sel); await page.keyboard.type(text, { delay: 55 }); };
  const highlight = async (sel, pad = 6) => {
    if (!sel) return ev(() => { document.getElementById("__hl").style.opacity = 0; });
    const { b } = await box(sel);
    await ev((b, pad) => { const e = document.getElementById("__hl"); Object.assign(e.style, { left: b.x - pad + "px", top: b.y - pad + "px", width: b.width + 2 * pad + "px", height: b.height + 2 * pad + "px", opacity: 1 }); }, b, pad);
  };
  const scrollTo = async (y) => { await ev((y) => window.scrollTo({ top: y, behavior: "smooth" }), y); await sleep(1100); };
  const scrollToEl = async (sel, offset = 90) => {
    const h = typeof sel === "string" ? await page.waitForSelector(sel) : sel;
    const y = await h.evaluate((e, o) => e.getBoundingClientRect().top + window.scrollY - o, offset);
    await scrollTo(y);
  };
  const byText = (tag, text) => `xpath/(//${tag}[contains(normalize-space(.), ${JSON.stringify(text)})])[last()]`;
  const api = (p) => ev(async (p) => (await fetch(p, { credentials: "same-origin" })).json(), p);

  const VID = 47, SUB = "172.16.6.0/24";
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded" });
  await page.waitForSelector("input[autocomplete=username]");
  await page.type("input[autocomplete=username]", FF.user);
  await page.type("input[type=password]", FF.pass);
  await page.click("button.btn.primary");
  await page.waitForSelector(byText("h1", "Overview"));
  const r = { status: "done" }; const subnet = SUB, vid = VID, id = 18;
  await page.goto(BASE + "/tools/state", { waitUntil: "domcontentloaded" });
  await ready();
  await page.goto(NB + "/login/", { waitUntil: "networkidle2" });
  await page.type("input[name=username]", nbEnv("NAUTOBOT_SUPERUSER_NAME"));
  await page.type("input[name=password]", nbEnv("NAUTOBOT_SUPERUSER_PASSWORD"));
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle2" }), page.click("button[type=submit]")]);
  await page.goto(BASE + "/tools/state", { waitUntil: "domcontentloaded" });
  await ready();
  const r18 = await api(`/api/requests/${id}`);           // Fabric Factory's API: read while on its origin
  await startRec();
  // ---- proof on the devices: the WAN's routing table --------------------------------------------------------------------
  await caption("Retrieve device state", "Now check from outside the workflow. Ask the <b>WAN router</b> for its routing table and BGP AS paths, live.");
  await hold(1);
  for (const lbl of ["Interfaces", "BGP", "MAC addresses", "BGP AS paths"]) {
    const h = await page.$(`xpath/(//button[contains(@class,'chip') and normalize-space(.)=${JSON.stringify(lbl)}])[1]`);
    if (h) { await click(h); await hold(0.3); }
  }
  await click(`xpath/(//button[strong[normalize-space(.)='wan']])[1]`);
  const RT = `xpath/(//section[contains(@class,'card')][.//h2[normalize-space(.)='Routing tables']])[1]`;
  await page.waitForSelector(RT + "//pre", { timeout: 60000 });
  await hold(2);
  const net = subnet.split("/")[0];
  await scrollToEl(RT, 80);
  await type(RT + "//input[@placeholder='Filter lines']", net.split(".").slice(0, 3).join(".") + ".");
  await hold(1);
  await highlight(RT);
  await caption("Retrieve device state", `Filtered to <b>${subnet}</b>: the WAN has learned it over eBGP (<b>B&gt;*</b>) from the border leaves. The firewall zone policy still governs the traffic.`);
  await hold(7);
  await highlight(null);
  const AP = `xpath/(//section[contains(@class,'card')][.//h2[contains(normalize-space(.),'AS path')]])[1]`;
  const row = `${AP}//tr[.//*[contains(normalize-space(.), ${JSON.stringify(net)})]][1]`;
  const hr = await page.$(row) || await page.$(`${AP}//tr[contains(normalize-space(.), ${JSON.stringify(net)})][1]`);
  if (hr) {
    await scrollToEl(hr, 330);
    await highlight(hr);
    await caption("Retrieve device state", "Its <b>BGP AS path</b>, right to left: a leaf (65103), a spine (65100), through the firewalls (65200; as-override also rewrites the border's AS), then border1 (65121). One path through each border.");
    await hold(7);
    await highlight(null);
  } else console.log("no AS path row");

  // ---- proof on the devices: leaf1's configuration ----------------------------------------------------------------------
  await page.goto(BASE + "/tools/configs", { waitUntil: "domcontentloaded" });
  await ready();
  await caption("View device configurations", "Device configurations, redacted, to view or download. Here's <b>leaf1</b>.");
  await click(`xpath/(//button[strong[normalize-space(.)='leaf1']])[1]`);
  await hold(2.5);
  await type("input[placeholder='Find in configuration']", String(vid));
  await hold(1.5);
  await caption("View device configurations", `VLAN ${vid} as the switches pipeline rendered it from Nautobot: VLAN-to-VNI ${10000 + Number(vid)}, the anycast gateway, and the VLAN on the VXLAN device.`);
  await hold(7);

  // ---- back to the overview -----------------------------------------------------------------------------------------------
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded" });
  await ready();
  await page.waitForSelector(byText("h1", "Overview"));
  await hold(1);
  await caption("Overview", `VLAN <b>${vid} ${VRF}</b> is now part of the lab, in Nautobot and phpIPAM and on the devices. Removing it is one more request; phpIPAM is released last.`);
  const chip = await page.$(`xpath/(//span[contains(@class,'tag') and normalize-space(.)=${JSON.stringify(vid + " " + VRF)}])[1]`);
  if (chip) { await highlight(chip); await moveTo(chip); }
  await hold(6);
  await highlight(null);

  // ---- the Fabric Dashboard ------------------------------------------------------------------------------------------------
  const hover = async (sel) => { await moveTo(sel); await page.mouse.move(cur[0], cur[1]); };
  const CARD = (t) => `xpath/(//section[contains(@class,'card')][.//h2[contains(normalize-space(.), ${JSON.stringify(t)})]])[1]`;
  await caption(null);
  await card(`<div class="n">NEW</div><h1>The Fabric Dashboard</h1>
    <p>The whole fabric at a glance: what it's built from, the KPIs its design implies, and a live check that the network still matches it.</p>`);
  await hold(4.5);
  await page.goto(BASE + "/new", { waitUntil: "domcontentloaded" });
  await ready();
  await card(null);
  const fab = await api("/api/fabric");
  const toolCard = `xpath/(//section[contains(@class,'card')][.//h3[normalize-space(.)='Fabric Dashboard']])[1]`;
  await page.waitForSelector(toolCard);
  await highlight(toolCard);
  await caption("New request · Tools", "Under <b>Tools</b> on the New request page: the <b>Fabric Dashboard</b>. Read-only, so no approval is needed.");
  await hold(4);
  await highlight(null);
  await click(toolCard + "//a");
  await page.waitForSelector(".fab-tiles", { timeout: 30000 });
  await ready();
  await hold(1);
  await highlight(".fab-tiles");
  const c = fab.counts;
  await caption("Fabric Dashboard", `Straight from Nautobot: <b>${c.switches} switches</b> (${c.spines} spines, ${c.leaves} leaves, ${c.borders} border leaves), <b>${c.vrfs} VRFs</b>, <b>${c.subnets} subnets</b>, <b>${c.vlans} tier VLANs</b>, <b>${c.devices} devices</b> and <b>${c.links} cabled links</b>.`);
  await hold(7);
  await highlight(null);
  await scrollToEl(CARD("Design KPIs"), 75);
  await highlight(CARD("Design KPIs"));
  const k = Object.fromEntries(fab.kpis.map((x) => [x.key, x.value]));
  await caption("Design KPIs", `Worked out from the cabling: <b>${k.ecmp}</b> leaf-to-spine ECMP, <b>${k.oversub}</b> oversubscription, <b>${k.capacity}</b> of fabric capacity, <b>${k.spine_loss}</b> left if a spine fails, <b>${k.dual}</b> of endpoints dual-homed.`);
  await hold(8);
  await highlight(null);
  const rec = await api("/api/fabric/reconcile");
  await page.waitForSelector(".rec-grid", { timeout: 60000 });
  await scrollToEl(CARD("Sources of truth"), 75);
  await highlight(CARD("Sources of truth"));
  await caption("Sources of truth", `phpIPAM and Nautobot, compared: VRFs, VLANs, every subnet with its VRF and VLAN, gateways, assigned addresses, and the plan against its own objects. <b>${rec.summary.matched}/${rec.summary.total}</b> match, <b>${rec.summary.errors}</b> drift.`);
  await hold(8);
  await caption("Sources of truth", "Read-only: it reports, it repairs nothing. A request still running shows as <b>in flight</b>, not drift: phpIPAM is written first and released last.");
  await hold(5.5);
  await highlight(null);
  await scrollToEl(CARD("Topology"), 75);
  await caption("Topology", "The topology, drawn from Nautobot's cables. Hover over a device to trace its links: <b>leaf1</b> goes up to both spines and down to its rack's servers.");
  await hover(`xpath/(//*[local-name()='g'][contains(@class,'nd')][*[local-name()='text'][normalize-space(.)='leaf1']])[1]`);
  await hold(4);
  await caption("Topology", "<b>border1</b>: both spines, the firewall pair, the load balancers and the WAN.");
  await hover(`xpath/(//*[local-name()='g'][contains(@class,'nd')][*[local-name()='text'][normalize-space(.)='border1']])[1]`);
  await hold(4);
  await page.mouse.move(5, 5);
  await scrollToEl(CARD("Devices by role"), 75);
  const mdl = fab.models.map((m) => `<b>${m.count} ${m.model}</b>`).join(" and ");
  await caption("Roles and models", `Every device by role, the switches tagged, and the device models: ${mdl}, each split by role.`);
  await hold(6.5);
  await scrollToEl(CARD("VRFs"), 75);
  const webRow = `${CARD("VRFs")}//tr[td[1]//strong[normalize-space(.)='${VRF}']]`;
  await highlight(webRow);
  await caption("VRFs and subnets", `Each VRF with its L3 VNI, VLANs, subnets and servers. VLAN <b>${vid}</b> from the request we just ran is already here, in VRF <b>${VRF}</b>.`);
  await hold(7);
  await highlight(null);
  const H = CARD("Live health");
  await page.waitForSelector(".health-wrap", { timeout: 90000 });
  await scrollToEl(H, 75);
  const hk = await ev(() => Object.fromEntries([...document.querySelectorAll(".health-wrap")].length ? [...document.querySelectorAll(".kpi")].map((e) => [e.querySelector(".kpi-l").textContent.trim(), e.querySelector(".kpi-v").textContent.trim()]) : []));
  await highlight(`${H}//div[contains(@class,'kpi-grid')]`);
  await caption("Live health vs. design", `Then it asks the devices, read-only over SSH: <b>${hk["Devices reachable"]}</b> reachable, fabric sessions <b>${hk["Fabric sessions vs. design"]}</b> against the design, BGP <b>${hk["BGP sessions up"]}</b>, BFD <b>${hk["BFD sessions up"]}</b>, the same VNIs on every leaf.`);
  await hold(8);
  await highlight(null);
  await scrollToEl(".health-wrap", 75);
  await caption("Live health vs. design", "Each device's software version, uptime, BGP, BFD and VNIs, with one click to check again.");
  await hold(5);
  await scrollToEl(CARD("Change KPIs"), 75);
  await highlight(CARD("Change KPIs"));
  const ch = fab.changes;
  await caption("Change KPIs", `And how changes are going: <b>${ch.done} completed</b>, <b>${ch.success_rate}%</b> success, median <b>${Math.floor(ch.median_s / 60)}m ${String(Math.round(ch.median_s % 60)).padStart(2, "0")}s</b> from approval to a verified change.`);
  await hold(7);
  await highlight(null);

  // ---- inside Nautobot: where it all lives, what a request writes, how it is read back -------------------------------------
  const nbGo = async (url) => {
    await page.goto(NB + url, { waitUntil: "networkidle2" }); await ready();
    const t = await page.$("button.nb-sidenav-toggler[aria-expanded=true]");
    if (t) { await t.evaluate((e) => e.click()); await sleep(450); }
  };
  const rowOf = (text) => `xpath/(//main//table//tbody/tr[.//*[normalize-space(.)=${JSON.stringify(text)}]])[1]`;
  const boxRect = async (r, pad = 6) => ev((b, pad) => { const e = document.getElementById("__hl"); Object.assign(e.style, { left: b.x - pad + "px", top: b.y - pad + "px", width: b.w + 2 * pad + "px", height: b.h + 2 * pad + "px", opacity: 1 }); }, r, pad);
  await caption(null);
  await card(`<div class="n">THE SOURCE OF TRUTH</div><h1>Inside Nautobot</h1>
    <p>Where the lab lives in Nautobot, what a request writes there, and how the pipelines, the forms and the dashboard read it back.</p>`);
  await hold(4.5);
  await nbGo("/dcim/devices/?location=evpn-3tier&per_page=50");
  await card(null);
  await caption("Nautobot · Devices", "All of the lab is in location <b>evpn-3tier</b>: <b>19 devices</b> named e3t-&lt;node&gt;, each with a role, a device type, a rack and the management address Ansible connects to.");
  await hold(2);
  await highlight(rowOf("e3t-leaf1")); await moveTo(rowOf("e3t-leaf1"), 0.08);
  await hold(5);
  await highlight(null);
  await nbGo("/dcim/devices/afc3f06e-043e-47c0-b451-b1180c0b7fec/interfaces/");
  await page.addStyleTag({ content: [4, 5, 6, 7, 9, 10, 11, 12, 13, 16, 21, 22].map((n) => `table tr > *:nth-child(${n})`).join(",") + "{display:none}" });
  await sleep(300);
  await highlight("xpath/(//main//table)[1]");
  await caption("Nautobot · e3t-leaf1 · Interfaces", "Every interface and cable: leaf1's uplinks to both spines (fabric, MTU 9000) and its three server legs. The pipelines and the dashboard's topology are built from these cables.");
  await hold(8);
  await highlight(null);
  await nbGo("/ipam/vlans/?vlan_group=evpn-3tier&per_page=50");
  await highlight(rowOf(`vlan_${vid}_${VRF}`)); await moveTo(rowOf(`vlan_${vid}_${VRF}`), 0.03);
  await caption("Nautobot · IPAM · VLANs", `VLANs live in group <b>evpn-3tier</b>. <b>vlan_${vid}_${VRF}</b> was created by request #${id}, the Add a VLAN we ran: a POST to /api/ipam/vlans/.`);
  await hold(6.5);
  await highlight(null);
  await nbGo("/ipam/prefixes/?namespace=evpn-3tier&per_page=50");
  await highlight(rowOf(subnet)); await moveTo(rowOf(subnet), 0.06);
  await caption("Nautobot · IPAM · Prefixes", `Its subnet <b>${subnet}</b> in namespace evpn-3tier, tied to VRF <b>${VRF}</b>, tenant e3t-${VRF}, the location and VLAN ${vid}; the description names the request.`);
  await hold(6.5);
  await highlight(null);
  await nbGo("/extras/config-contexts/02c39f3d-2b61-4463-a0f8-ffb52140b4df/");
  const gwText = `"gateway": "${subnet.replace(/0\/24$/, "1/24")}"`;
  const span = async (from, to, scroll) => ev((from, to, scroll) => {
    const pre = [...document.querySelectorAll("pre")].sort((a, b) => b.textContent.length - a.textContent.length)[0];
    const t = pre.textContent; const i = t.indexOf(from); if (i < 0) return null;
    const j = t.indexOf(to, i) + to.length;
    const w = document.createTreeWalker(pre, NodeFilter.SHOW_TEXT); let n, off = 0; const r = document.createRange(); let set = 0;
    while ((n = w.nextNode())) {
      const L = n.textContent.length;
      if (!(set & 1) && i < off + L) { r.setStart(n, i - off); set |= 1; }
      if (!(set & 2) && j <= off + L) { r.setEnd(n, j - off); set |= 2; break; }
      off += L;
    }
    if (scroll) { const b0 = r.getBoundingClientRect(); window.scrollBy({ top: b0.top - window.innerHeight * 0.32, behavior: "instant" }); }
    const b = r.getBoundingClientRect(), pb = pre.getBoundingClientRect();
    return { x: pb.left + 4, y: b.top - 22, w: Math.min(pb.width - 8, 520), h: b.height + 44 };
  }, from, to, scroll);
  await hold(1);
  const r1 = await span(gwText, `"vrf": "${VRF}"`, true);
  if (r1) await boxRect(r1);
  await caption("Nautobot · Config context evpn-3tier", `What Nautobot has no model for is a JSON plan scoped to the location. Request #${id} appended VLAN ${vid} to <b>evpn.vlans</b> (gateway, span, VNI ${10000 + vid}, VRF) with a PATCH.`);
  await hold(7.5);
  await highlight(null);
  const r2 = await span(`"dest": "${subnet}"`, `"vlan": ${vid}`, true);
  if (r2) await boxRect(r2);
  await caption("Nautobot · Config context evpn-3tier", `…and its <b>Standard Firewall Rules</b> to <b>fw_policy</b>: ICMP to ${subnet} from outside, app and db. The firewalls pipeline renders rules from these entries.`);
  await hold(6.5);
  await highlight(null);
  const stg = Object.fromEntries((r18.progress.stages || []).map((x) => [x.key, x]));
  const t0 = new Date(new Date(stg.nautobot.started).getTime() - 5000).toISOString(), t1 = new Date(new Date(stg.rules.finished).getTime() + 5000).toISOString();
  // Nautobot's UI list fails on time filters (its API does not): count the newer changes through the API (this page's
  // session), list enough rows, and frame request #18's: rows n .. n+k-1, newest first
  const cnt = async (q) => (await api(`/api/extras/object-changes/?limit=1&${q}`)).count;
  const nNewer = await cnt(`time__gt=${encodeURIComponent(t1)}`);
  const nMine = await cnt(`time__gte=${encodeURIComponent(t0)}&time__lte=${encodeURIComponent(t1)}`);
  log("change log: newer", nNewer, "request", nMine);
  await nbGo(`/extras/object-changes/?per_page=${nNewer + nMine + 14}`);        // older rows below: room to scroll #18's up
  await page.waitForFunction((k) => document.querySelectorAll("main table tbody tr").length >= k, { timeout: 30000 }, nNewer + nMine);
  await page.evaluate((n) => { const r = document.querySelectorAll("main table tbody tr")[n]; r.scrollIntoView({ block: "start", behavior: "instant" }); window.scrollBy(0, -170); }, nNewer);
  await sleep(300);
  const top8 = await page.evaluate((n, k) => { const rs = [...document.querySelectorAll("main table tbody tr")].slice(n, n + k); const a = rs[0].getBoundingClientRect(), b = rs[rs.length - 1].getBoundingClientRect(); return { x: a.left, y: a.top, w: a.width, h: b.bottom - a.top }; }, nNewer, nMine);
  await boxRect(top8);
  await caption("Nautobot · Change log", `Every write is in Nautobot's change log. Request #${id}, newest first: the plan entry, the VLAN and the prefix created, the prefix tied to its location and VRF, then the firewall rules.`);
  await hold(7.5);
  await highlight(null);
  await nbGo("/graphql/");
  await page.waitForSelector(".graphiql-query-editor .CodeMirror");
  await caption("Nautobot · GraphQL", "Reading it back takes <b>one GraphQL query</b>. The pipelines, the forms and the Fabric Dashboard each ask once for devices, cabling and the plan.");
  const QL = `{
  devices(location: ["evpn-3tier"], role: ["e3t-leaf"]) {
    name rack { name } primary_ip4 { address }
    interfaces(name: ["eth1", "eth2"]) {
      name cable_peer_interface { name device { name } }
    }
  }
  prefixes(namespace: ["evpn-3tier"], q: "${subnet.split(".").slice(0, 3).join(".")}") {
    prefix vrfs { name } vlan { vid name } description
  }
}`.split("\n");
  await moveTo(".graphiql-query-editor", 0.3, 0.2);
  for (let k = 1; k <= QL.length; k++) {
    await ev((q) => document.querySelector(".graphiql-query-editor .CodeMirror").CodeMirror.setValue(q), QL.slice(0, k).join("\n"));
    await sleep(260);
  }
  await hold(1);
  await click(".graphiql-execute-button");
  await hold(5);
  await caption("Nautobot · GraphQL", `One answer: each leaf with its rack, management address and both uplink cables, and the prefix of VLAN ${vid} with its VRF and VLAN.`);
  await ev(() => { const cms = [...document.querySelectorAll(".CodeMirror")]; const r = cms.find((c) => c.closest(".graphiql-response")) || cms[cms.length - 1]; r.CodeMirror.scrollTo(null, 1e6); });
  await hold(6);

  // ---- the change workflows: the catalog, a live custom firewall rule, a finished server move -----------------------------
  await caption(null);
  await card(`<div class="n">NEW</div><h1>Change workflows</h1>
    <p>Change a VLAN's span, add or remove a custom firewall rule, move a server to another VLAN: approved by an operator, built by the pipelines, proven on the devices.</p>`);
  await hold(4.5);
  await page.goto(BASE + "/new", { waitUntil: "domcontentloaded" }); await ready(); await card(null);
  const wfCard = (t) => `xpath/(//section[contains(@class,'card')][.//h3[normalize-space(.)=${JSON.stringify(t)}]])[1]`;
  await page.waitForSelector(wfCard("Move a server to another VLAN"));
  await scrollToEl(wfCard("Change a VLAN's span"), 80);
  await caption("New request", "Four new change workflows next to Add / Remove a VLAN and load balancer peering. Each one lists, in order, what happens once it is approved.");
  for (const t of ["Change a VLAN's span", "Add a custom firewall rule", "Remove a custom firewall rule"]) { await highlight(wfCard(t)); await moveTo(wfCard(t), 0.5, 0.15); await hold(1.6); }
  await highlight(null);
  await click(`xpath/(//a[contains(@href,'/new/fw-rule') and not(contains(@href,'remove'))])[1]`);
  await page.waitForSelector("#f-source"); await ready();
  await caption("Add a custom firewall rule", "Let the <b>web</b> tier reach <b>db1</b> on TCP 6432: from zone, to zone, protocol, destination, port.");
  for (const [sel, v] of [["#f-source", "web"], ["#f-target", "db"], ["#f-proto", "tcp"]]) { await click(sel); await page.select(sel, v); await hold(0.5); }
  await type("#f-dest", "172.16.30.11"); await type("#f-port", "6432");
  await hold(1);
  await caption("Add a custom firewall rule", "Checked against the live plan before an operator sees it: the destination must be in the db zone, and nothing may already allow it.");
  await highlight(`xpath/(//section[contains(@class,'card')][.//h2[normalize-space(.)='When it is approved']])[1]`);
  await hold(5);
  await highlight(null);
  await type("#f-reason", "Demo: a maintenance tool on web reads db1 on 6432");
  await click(byText("button", "Submit for approval"));
  await page.waitForFunction(() => /\/requests\/\d+$/.test(location.pathname), { timeout: 20000 });
  const fwId = Number((await page.url()).match(/requests\/(\d+)/)[1]); log("fw rule request", fwId);
  await ready(); await hold(1.5);
  await caption("Approval", `Request <b>#${fwId}</b>: the operator approves it.`);
  await click(byText("button", "Approve")); await hold(1);
  await click(`xpath/(//footer//button[contains(normalize-space(.), "Approve")])[last()]`); await hold(1);
  const FWCAP = {
    "Nautobot": "<b>Nautobot</b>: the rule joins the firewall policy with its own number (500-899), so no other rule is ever renumbered.",
    "Firewalls pipeline": "<b>evpn-3tier-firewalls</b>: rendered from Nautobot, checked, applied to fw1 and fw2, then the whole lab verified.",
    "Firewall checks": "<b>Checks</b>: the rule on both firewalls, with its protocol, destination and port.",
    "Traffic test": "<b>Traffic test</b>: a listener on db1, a real TCP connection from web1, through the firewall.",
  };
  speed = 10; await badge("⏩ 10× speed");
  let lastSt = "", fr;
  const tw0 = Date.now();
  while (Date.now() - tw0 < 15 * 60 * 1000) {
    fr = await api(`/api/requests/${fwId}`);
    if (["done", "failed"].includes(fr.status)) break;
    const st = fr.stage || "";
    if (st && st !== lastSt) { lastSt = st; await caption(`Running · ${st} · ${fr.percent ?? 0}%`, FWCAP[st] || st); }
    await sleep(2000);
  }
  speed = 1; await badge(null); log("fw rule", fr.status);
  await scrollTo(0); await hold(1.5);
  const tt = `xpath/(//div[contains(@class,'ev-m')][contains(., 'traffic test')])[1]`;
  if (await page.$(tt)) {
    await highlight(tt); await moveTo(tt, 0.1, 0.5);
    await caption("Done", `Request <b>#${fwId}</b> is done: the rule is on both firewalls, and a TCP connection from web1 to db1:6432 <b>got through</b>.`);
    await hold(7); await highlight(null);
  }
  // a finished move: app1 to VLAN 42 (request #26)
  await page.goto(BASE + "/requests/26", { waitUntil: "domcontentloaded" }); await ready();
  await page.waitForSelector("ol.stepper");
  await highlight("ol.stepper");
  await caption("Move a server to another VLAN", "Request <b>#26</b> moved <b>app1</b> to VLAN 42. The lab stays whole: both VLANs on its leaves first, then the address moves, then the old VLAN goes and the whole lab is verified.");
  await hold(8.5);
  await highlight(null);
  const tw = `xpath/(//div[contains(@class,'ev-m')][contains(., 'rule for app1')])[1]`;
  if (await page.$(tw)) {
    await highlight(tw); await moveTo(tw, 0.1, 0.5);
    await caption("Move a server to another VLAN", "Its firewall rule <b>followed it</b>: web → app TCP 8080 was allowed to 172.16.20.0/24, so the move added the same rule for app1's new address.");
    await hold(7.5); await highlight(null);
  }
  const ng = `xpath/(//div[contains(@class,'ev-m')][contains(., 'nginx on web1')])[1]`;
  if (await page.$(ng)) {
    await highlight(ng); await moveTo(ng, 0.1, 0.5);
    await caption("Move a server to another VLAN", "nginx on both web servers was re-rendered to send to app1's new address, and the ping test from the WAN passed.");
    await hold(6); await highlight(null);
  }
  await caption(null);
  await card(`<div class="brand"><img src="${LOGO}" width="64" height="64">FABRIC FACTORY</div>
    <h1>Intent in, verified change out</h1>
    <p>phpIPAM and Nautobot hold the truth · four Gitea pipelines with Ansible configure 20 VyOS and Alpine VMs · every change checked on the devices and from the WAN</p>`);
  await hold(6);
  await cdp.send("Page.stopScreencast");
  times.push(vclock + 0.5);
  fs.writeFileSync(path.join(__dirname, "timing2.json"), JSON.stringify({ times, id, status: r.status, vid, subnet }));
  log("frames", n, "virtual seconds", vclock.toFixed(1));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
