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
const OUT = path.join(__dirname, "part6");
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

  // ---- grow the fabric: a leaf pair, a VRF -----------------------------------------------------------------------------------------
  const Tc = (t) => `xpath/(//section[contains(@class,'card')][.//h2[normalize-space(.)=${JSON.stringify(t)}]])[1]`;
  const waitDone = async (id, CAP) => {
    speed = 10; await badge("⏩ 10× speed");
    let last = "", r; const t0 = Date.now();
    while (Date.now() - t0 < 30 * 60 * 1000) {
      r = await api(`/api/requests/${id}`);
      if (["done", "failed"].includes(r.status)) break;
      const st = r.stage || "";
      if (st && st !== last) { last = st; await caption(`Running · ${st} · ${r.percent ?? 0}%`, CAP[st] || st); }
      await sleep(1500);
    }
    speed = 1; await badge(null); log("request", id, r.status); return r;
  };
  const submitApprove = async (who) => {
    await click(byText("button", "Submit for approval"));
    await page.waitForFunction(() => /\/requests\/\d+$/.test(location.pathname), { timeout: 30000 });
    const id = Number((await page.url()).match(/requests\/(\d+)/)[1]); log(who, id);
    await ready(); await hold(1);
    await caption("Approval", `Request <b>#${id}</b>: the operator approves it.`);
    await click(byText("button", "Approve")); await hold(0.8);
    await click(`xpath/(//footer//button[contains(normalize-space(.), "Approve")])[last()]`); await hold(0.8);
    return id;
  };
  const showEvent = async (text, title, body, secs) => {
    const x = `xpath/(//div[contains(@class,'ev-m')][contains(., ${JSON.stringify(text)})])[1]`;
    if (await page.$(x)) { await highlight(x); await moveTo(x, 0.1, 0.5); await caption(title, body); await hold(secs); await highlight(null); }
  };
  await caption(null);
  await card(`<div class="n">NEW</div><h1>Grow the fabric</h1>
    <p><b>Add a leaf pair</b>: a new rack of two leaves, cabled, configured and verified. <b>Add a VRF</b>: a new tier zone behind the firewalls. One request each.</p>`);
  await hold(4.5);
  // ---- a leaf pair
  await page.goto(BASE + "/new/add-leaf-pair", { waitUntil: "domcontentloaded" }); await page.waitForSelector("#f-pair"); await ready(); await card(null); await hold(0.6);
  await click("#f-pair"); await page.select("#f-pair", "5,6"); await hold(0.6);
  await highlight(`xpath/(//label[@for='f-pair']/parent::div)[1]`);
  await caption("Add a leaf pair", "Nothing to fill in: the app allocates the next pair — <b>leaf5 + leaf6 in rack3</b>, their AS numbers, management and loopback addresses, and the spines' next spare ports.");
  await hold(6.5); await highlight(null);
  await highlight(Tc("When it is approved"));
  await caption("Add a leaf pair", "phpIPAM, Nautobot, the VMs racked and cabled, day-0 over the serial console, the switches pipeline, then checks.");
  await hold(5); await highlight(null);
  await type("#f-reason", "Rack 3: room for the next applications");
  const p1 = await submitApprove("leaf pair request");
  await waitDone(p1, {
    "Pre-flight checks": "<b>Pre-flight</b>: host memory, the spines' spare ports, every address and AS number still free.",
    "phpIPAM reservation": "<b>phpIPAM</b>: 30 addresses — management, loopbacks, each leaf's address in every leaf VLAN, probes.",
    "Nautobot": "<b>Nautobot</b>: rack3, both leaves with their interfaces and addresses, four cables to the spines.",
    "Rack and cable": "<b>Rack and cable</b>: the leaves' VMs cabled to spine ports eth7 / eth8, then day-0 over the serial console — the lab's zero-touch provisioning.",
    "Switches pipeline": "<b>evpn-3tier-switches</b>: the leaves' full configuration and the spines' new neighbours, from Nautobot — then the whole lab verified.",
    "Fabric checks": "<b>Checks</b>: BGP and BFD to both spines, the same VNIs and anycast gateways as leaf1, the loopbacks reachable.",
    "Ping test from the WAN": "<b>Ping test from the WAN</b>: every server still answers.",
  });
  await scrollTo(0); await hold(1.2);
  await showEvent("on leaf5:", "Done", "leaf5 and leaf6 are in the fabric: <b>BGP + BFD to both spines</b>, the same VNIs and anycast gateways as leaf1. About twelve minutes.", 6);
  await showEvent("rack and cable:", "Done", "Racked and cabled: their VMs started, day-0 pushed over the serial console.", 4);
  await page.goto(BASE + "/tools/fabric", { waitUntil: "domcontentloaded" }); await page.waitForSelector(Tc("Devices by role")); await ready(); await hold(0.8);
  await highlight(Tc("Devices by role"));
  await caption("Fabric Dashboard", "The dashboard counts <b>six leaves</b> now — read straight from Nautobot.");
  await hold(5.5); await highlight(null);
  // ---- a VRF
  await page.goto(BASE + "/new/add-vrf", { waitUntil: "domcontentloaded" }); await page.waitForSelector("#f-vrf"); await ready(); await hold(0.6);
  await type("#f-vrf", "pay");
  if (!(await page.$eval("#f-first_vlan", (e) => e.checked))) await click("#f-first_vlan");
  await highlight(`xpath/(//label[@for='f-vrf']/parent::div)[1]`);
  await caption("Add a VRF", "A new <b>tier zone</b>, like web, app and db: you name it — <b>pay</b>. Its L3VNI, transit VLAN and subnet to the firewalls are allocated.");
  await hold(6); await highlight(null);
  await highlight(`xpath/(//input[@id='f-first_vlan']/ancestor::div[contains(@class,'field')][1])[1]`);
  await caption("Add a VRF", "With its <b>first VLAN</b> in the same request, the zone is usable at once. The firewall zone <b>denies by default</b>, except the standard ICMP rules.");
  await hold(6); await highlight(null);
  await type("#f-reason", "A zone for the payments service");
  const p2 = await submitApprove("vrf request");
  await waitDone(p2, {
    "Pre-flight checks": "<b>Pre-flight</b>: the name, and every allocated number still free.",
    "phpIPAM reservation": "<b>phpIPAM</b>: VRF pay, its VLANs, the transit /28 with the borders' and firewalls' addresses — and the first VLAN's /24.",
    "Nautobot": "<b>Nautobot</b>: tenant, route target, VRF, VLANs and prefixes; the VRF and its transit in the evpn-3tier plan.",
    "Standard firewall rules": "<b>Standard Firewall Rules</b>: ICMP from pay to every VLAN, and to pay's VLAN from every zone.",
    "Switches pipeline": "<b>evpn-3tier-switches</b>: the VRF and its L3VNI on the borders and every leaf — the new ones too.",
    "Firewalls pipeline": "<b>evpn-3tier-firewalls</b>: the transit VLAN, eBGP to both borders, zone PAY — then the whole lab verified.",
    "Device checks": "<b>Checks</b>: the VRF on every switch, the firewall sessions, zone PAY, the default route, the first VLAN.",
    "Ping test from the WAN": "<b>Ping test from the WAN</b>: every server, and pay's new gateway.",
  });
  await scrollTo(0); await hold(1.2);
  await showEvent("on the devices: VRF pay", "Done", "VRF <b>pay</b> on both borders and all six leaves; both firewalls peer with the borders in it; zone <b>PAY</b> on both.", 6);
  await showEvent("Standard Firewall Rules: 10", "Done", "Deny by default — only the standard ICMP rules, from pay to every other VLAN.", 4.5);
  await page.goto(BASE + "/tools/fabric", { waitUntil: "domcontentloaded" }); await page.waitForSelector(Tc("VRFs")); await ready(); await hold(0.8);
  await highlight(Tc("VRFs"));
  await caption("Fabric Dashboard", "Five VRFs: web, app, db, outside — and <b>pay</b>.");
  await hold(5); await highlight(null);
  // ---- delete refused while in use
  await page.goto(BASE + "/new/delete-vrf", { waitUntil: "domcontentloaded" }); await page.waitForSelector("#f-vrf"); await ready(); await hold(0.5);
  await click("#f-vrf"); await page.select("#f-vrf", "pay"); await hold(0.4);
  await type("#f-confirm", "pay"); await type("#f-reason", "No longer needed");
  await click(byText("button", "Submit for approval")); await hold(1.5);
  const err = `xpath/(//*[contains(., 'is in use')][not(*[contains(., 'is in use')])])[1]`;
  if (await page.$(err)) await highlight(err);
  await caption("Delete a VRF", "Deleting works the same way in reverse — and is <b>refused</b> while the VRF holds anything: here its VLAN. Remove a leaf pair is refused while a server or a provisioned port uses it.");
  await hold(7); await highlight(null);
  await caption(null);
  await card(`<div class="brand"><img src="${LOGO}" width="64" height="64">FABRIC FACTORY</div>
    <h1>Intent in, verified change out</h1>
    <p>phpIPAM and Nautobot hold the truth · four Gitea pipelines with Ansible configure 20 VyOS and Alpine VMs · every change checked on the devices and from the WAN</p>`);
  await hold(6);
  await cdp.send("Page.stopScreencast");
  times.push(vclock + 0.5);
  fs.writeFileSync(path.join(__dirname, "timing6.json"), JSON.stringify({ times, id, status: r.status, vid, subnet }));
  log("frames", n, "virtual seconds", vclock.toFixed(1));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
