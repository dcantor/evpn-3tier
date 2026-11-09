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
const OUT = path.join(__dirname, "part3");
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

  // ---- the ASN and VLAN databases ----------------------------------------------------------------------------------------
  const { execFileSync } = require("child_process");
  const DL = path.join(__dirname, "downloads"); fs.rmSync(DL, { recursive: true, force: true }); fs.mkdirSync(DL);
  await cdp.send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: DL }).catch(() =>
    cdp.send("Page.setDownloadBehavior", { behavior: "allow", downloadPath: DL }));
  const newest = async (prefix) => {                     // wait for the browser to finish writing the download
    for (let i = 0; i < 40; i++) {
      const f = fs.readdirSync(DL).filter((x) => x.startsWith(prefix) && x.endsWith(".xlsx"));
      if (f.length) return path.join(DL, f.sort().pop());
      await sleep(250);
    }
    return null;
  };
  const preview = async (file, caps) => {                 // the downloaded workbook, as a spreadsheet grid
    const out = path.join(__dirname, path.basename(file) + ".html");
    execFileSync("/home/dcantor/evpn-3tier/selfservice/.venv/bin/python", [path.join(__dirname, "xlsx2html.py"), file, out]);
    await page.goto("file://" + out, { waitUntil: "load" }); await ready();
    for (const [k, c, s] of caps) { await caption(k, c); await hold(s); }
  };
  await caption(null);
  await card(`<div class="n">NEW</div><h1>The ASN and VLAN databases</h1>
    <p>Every AS and every VLAN of the fabric, read from Nautobot, searchable, and downloadable as an Excel workbook.</p>`);
  await hold(4.5);
  await page.goto(BASE + "/new", { waitUntil: "domcontentloaded" }); await ready(); await card(null);
  const toolCard = (t) => `xpath/(//section[contains(@class,'card')][.//h3[normalize-space(.)=${JSON.stringify(t)}]])[1]`;
  await page.waitForSelector(toolCard("ASN Database"));
  await scrollToEl(toolCard("ASN Database"), 120);
  await caption("New request · Tools", "Two new tools next to the Fabric Dashboard: the <b>ASN Database</b> and the <b>VLAN Database</b>. Read-only, no approval.");
  for (const t of ["ASN Database", "VLAN Database"]) { await highlight(toolCard(t)); await moveTo(toolCard(t), 0.5, 0.2); await hold(2); }
  await highlight(null);
  // ASN Database
  await click(`xpath/(//a[@href='/tools/asns'])[1]`);
  await page.waitForSelector(".db-table"); await ready(); await hold(0.8);
  const asn = await api("/api/tools/asns");
  await highlight(".tiles");
  await caption("ASN Database", `<b>${asn.summary.asns} ASNs</b>, <b>${asn.summary.sessions} BGP sessions</b> in the design, ${asn.summary.private_used} of ${asn.summary.private_pool} private ASNs used.`);
  await hold(5); await highlight(null);
  const T = (title) => `xpath/(//section[contains(@class,'card')][.//h2[normalize-space(.)=${JSON.stringify(title)}]])[1]`;
  await scrollToEl(T("ASNs"), 75);
  await caption("ASN Database", "Each AS with its devices, router IDs, the ASNs it peers with and its VRFs: the spines share <b>65100</b>, every leaf and border has its own.");
  await hold(5.5);
  const chip = (card, label) => `${T(card)}//button[contains(@class,'chip') and normalize-space(.)=${JSON.stringify(label)}]`;
  await click(chip("ASNs", "Firewall")); await hold(0.6);
  await highlight(`xpath/(${T("ASNs").slice(6)}//tbody/tr)[1]`);
  await caption("ASN Database", "Filter by kind: the firewall pair shares <b>65200</b> and peers with both border leaves in every VRF — 16 sessions.");
  await hold(5); await highlight(null);
  await click(chip("ASNs", "Load balancer")); await hold(0.6);
  await highlight(`xpath/(${T("ASNs").slice(6)}//table)[1]`);
  await caption("ASN Database", "The load balancers: <b>65310</b> and <b>65320</b>, each peering with the border pair over its own VLAN — added by self-service requests.");
  await hold(5); await highlight(null);
  await click(chip("ASNs", "All")); await hold(0.4);
  await scrollToEl(T("BGP sessions"), 75);
  await click(chip("BGP sessions", "Firewall transit")); await hold(0.6);
  await caption("ASN Database · BGP sessions", "Every BGP session of the design, both ends: here the firewall transit sessions, one per border, firewall and VRF, all with BFD.");
  await hold(5.5);
  await click(chip("BGP sessions", "All")); await hold(0.3);
  await scrollTo(0); await hold(0.6);
  await caption("ASN Database", "<b>Download Excel</b>: a workbook built on the server, with the ASNs, the BGP sessions and an About sheet.");
  await click(`xpath/(//a[contains(@href,'/api/tools/asns.xlsx')])[1]`);
  const fa = await newest("evpn-3tier-asns"); log("download", fa);
  await hold(2.5);
  if (fa) await preview(fa, [["The downloaded workbook", "The workbook itself: a frozen header row, a filter on every column, numbers as numbers — and its sheets: ASNs, BGP sessions, About.", 6.5]]);
  // VLAN Database
  await page.goto(BASE + "/tools/vlans", { waitUntil: "domcontentloaded" }); await page.waitForSelector(".db-table"); await ready(); await hold(0.6);
  const vl = await api("/api/tools/vlans");
  await highlight(".tiles");
  await caption("VLAN Database", `<b>${vl.summary.vlans} VLANs</b>: ${Object.entries(vl.summary.by_type).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(", ")}; <b>${vl.summary.self_service}</b> made by self-service requests.`);
  await hold(5.5); await highlight(null);
  await scrollToEl(T("VLANs"), 75);
  await caption("VLAN Database", "Each VLAN with its name, VRF, VNI, subnet, gateway, where it lives, its hosts — and its origin: the lab's design, or the request that made it.");
  await hold(5.5);
  await click(chip("VLANs", "Tier")); await hold(0.4);
  await click(`${T("VLANs")}//button[contains(@class,'chip') and normalize-space(.)='web']`); await hold(0.6);
  const v47 = `${T("VLANs")}//tbody/tr[td[1][normalize-space(.)='47']]`;
  const capAt = (top) => ev((top) => { const c = document.getElementById("__cap"); c.style.top = top ? "78px" : ""; c.style.bottom = top ? "auto" : ""; }, top);
  if (await page.$(v47)) { await highlight(v47); await moveTo(`${v47}//a`, 0.5, 0.5); }
  await capAt(true);                                   // the row is at the page's bottom: the caption goes to the top
  await caption("VLAN Database", "Filtered to the web tier: VLAN <b>47</b>, from the Add a VLAN in this video — its origin links to request <b>#18</b>.");
  await hold(5.5); await highlight(null); await caption(null); await capAt(false);
  await click(chip("VLANs", "All")); await hold(0.3);
  await click(`${T("VLANs")}//button[contains(@class,'chip') and normalize-space(.)='any']`); await hold(0.3);
  await click(chip("VLANs", "Firewall transit")); await hold(0.6);
  await caption("VLAN Database", "The firewall transit VLANs: a /28 per VRF between the border leaves and the firewall pair.");
  await hold(4.5);
  await click(chip("VLANs", "All")); await hold(0.3);
  await scrollTo(0); await hold(0.5);
  await caption("VLAN Database", "<b>Download Excel</b> again: every VLAN, one row each.");
  await click(`xpath/(//a[contains(@href,'/api/tools/vlans.xlsx')])[1]`);
  const fv = await newest("evpn-3tier-vlans"); log("download", fv);
  await hold(2);
  if (fv) await preview(fv, [["The downloaded workbook", "The VLAN workbook: ID, name, type, VRF, VNI, subnet, gateway, usable addresses, where, hosts, origin — ready to filter and share.", 6.5]]);
  await caption(null);
  await card(`<div class="brand"><img src="${LOGO}" width="64" height="64">FABRIC FACTORY</div>
    <h1>Intent in, verified change out</h1>
    <p>phpIPAM and Nautobot hold the truth · four Gitea pipelines with Ansible configure 20 VyOS and Alpine VMs · every change checked on the devices and from the WAN</p>`);
  await hold(6);
  await cdp.send("Page.stopScreencast");
  times.push(vclock + 0.5);
  fs.writeFileSync(path.join(__dirname, "timing3.json"), JSON.stringify({ times, id, status: r.status, vid, subnet }));
  log("frames", n, "virtual seconds", vclock.toFixed(1));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
