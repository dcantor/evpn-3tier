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


const BASE = "http://127.0.0.1:8120";
const VRF = process.argv[2] || "web";
const OUT = path.join(__dirname, "frames");
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

  // ===================================================================================================================
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded" });
  await ready();
  await card(`<div class="brand"><img src="${LOGO}" width="64" height="64">FABRIC FACTORY</div>
    <h1>Self-service for an EVPN/VXLAN data centre</h1>
    <p>A tour of Fabric Factory on the evpn-3tier lab, then a live <b style="color:#fff">Add a VLAN</b> request: from a web form to a verified change on 20 virtual devices.</p>`);
  await startRec();
  await hold(6.5);
  await card(null);
  await hold(0.6);

  // ---- sign in --------------------------------------------------------------------------------------------------------
  await caption("Sign in", "Requesters submit changes; <b>operators</b> approve them. We sign in as <b>admin</b>, an operator.");
  await hold(1.2);
  await type("input[autocomplete=username]", FF.user);
  await type("input[type=password]", FF.pass);
  await click(byText("button", "Sign in"));
  await page.waitForSelector(byText("h1", "Overview"), { timeout: 15000 });
  await ready();

  // ---- overview -------------------------------------------------------------------------------------------------------
  await caption("Overview", "Every request on the lab, <b>updated live</b> as the automation works: running, awaiting approval, done, failed.");
  await moveTo(byText("div", "Awaiting approval"));
  await hold(3.5);
  await caption("Overview", "<b>The lab</b>, read live from Nautobot: the four VRFs, the tier VLANs, the switch pairs, and the load balancers peering with the fabric.");
  await highlight(`xpath/(//section[contains(@class,'card')][.//h2[normalize-space(.)='The lab']])[1]`);
  await moveTo(`xpath/(//section[contains(@class,'card')][.//h2[normalize-space(.)='The lab']])[1]`, 0.4, 0.3);
  await hold(4.5);
  await highlight(null);
  await caption("Overview", "My requests: earlier VLAN adds and removals and load balancer peerings, each with its full history.");
  await moveTo(byText("a", "Add VLAN 46"));
  await hold(3.5);

  // ---- catalog --------------------------------------------------------------------------------------------------------
  await click(byText("a", "New request"));
  await page.waitForSelector(byText("a", "Start"));
  await ready();
  await caption("New request", "Three automated workflows: <b>Add a VLAN</b>, <b>Remove a VLAN</b>, and <b>load balancer peering</b>.");
  await moveTo(byText("a", "Start"));
  await hold(3);
  await scrollTo(10000);
  await caption("New request", "And two read-only tools: <b>View device configurations</b> and <b>Retrieve device state</b>. These don't need approval.");
  await hold(3.5);
  await scrollTo(0);

  // ---- the demo: Add a VLAN -------------------------------------------------------------------------------------------
  await card(`<div class="n">LIVE DEMO</div><h1>Add a VLAN to the fabric</h1>
    <p>phpIPAM → Nautobot → Standard Firewall Rules → switches pipeline → firewalls pipeline → checks on the devices → a ping test from the WAN</p>`);
  await caption(null);
  await hold(5);
  await card(null);
  await click(`xpath/(//a[contains(@href,'/new/add-vlan')])[1]`);
  await page.waitForSelector("#f-vrf");
  await ready();
  await caption("Add a VLAN", "The requester picks only <b>the VRF</b>. Everything else comes from the sources of truth.");
  await click("#f-vrf");
  await page.select("#f-vrf", VRF);
  await hold(1.2);
  await caption("Add a VLAN", "Press <b>Select the next unused VLAN ID</b>. The app checks Nautobot, phpIPAM and every open request.");
  await click(byText("button", "Select the next unused VLAN ID"));
  await hold(2.5);
  const alloc = await api(`/api/catalog/add-vlan/allocate?vrf=${VRF}`).catch(() => ({}));
  log("allocation", JSON.stringify(alloc).slice(0, 300));
  await caption("Add a VLAN", "Allocated: the VLAN ID and VNI, the name <b>vlan_&lt;ID&gt;_" + VRF + "</b>, and a free /24 from <b>phpIPAM</b>. The gateway is always the subnet's first host.");
  await highlight(`xpath/(//label[starts-with(normalize-space(.),'VLAN ID')]/parent::div)[1]`);
  await hold(4.5);
  await highlight(null);
  await moveTo(`xpath/(//label[starts-with(normalize-space(.),'Gateway')]/parent::div)[1]`, 0.3, 0.6);
  await hold(2.5);
  await caption("Add a VLAN", "The panel on the right lists what happens once it's approved, <b>in order</b>.");
  await highlight(`xpath/(//section[contains(@class,'card')][.//h2[normalize-space(.)='When it is approved']])[1]`);
  await moveTo(byText("h2", "When it is approved"));
  await hold(5);
  await highlight(null);
  await caption("Add a VLAN", "Give the operator a reason, then submit. The form is checked against the live lab again on submission.");
  await type("textarea", "Demo: a new " + VRF + " segment for the tour video");
  await hold(0.8);
  if (process.env.DRY) { await cdp.send("Page.stopScreencast"); times.push(vclock + 0.5);
    fs.writeFileSync(path.join(__dirname, "timing.json"), JSON.stringify({ times })); log("dry run frames", n); await browser.close(); return; }
  await click(byText("button", "Submit for approval"));
  await page.waitForFunction(() => /\/requests\/\d+$/.test(location.pathname), { timeout: 20000 });
  const id = Number((await page.url()).match(/requests\/(\d+)/)[1]);
  log("request", id);
  await ready();
  await hold(1);
  await caption("Approval", `Request <b>#${id}</b> is waiting for an operator. Nothing has changed anywhere yet.`);
  await hold(3);

  // ---- approve --------------------------------------------------------------------------------------------------------
  await caption("Approval", "As the operator: <b>Approve</b>. The worker picks it up straight away.");
  await click(byText("button", "Approve"));
  await hold(1.2);
  await click(`xpath/(//footer//button[contains(normalize-space(.), "Approve")])[last()]`);
  await hold(1.5);

  // ---- running: time-lapse, captions follow the stage -----------------------------------------------------------------
  const CAP = {
    "phpIPAM reservation": "<b>phpIPAM first</b>: all checks run, then the VLAN, the subnet and its gateway address are reserved and tagged with the request number.",
    "Nautobot": "<b>Nautobot</b>: the VLAN joins the evpn-3tier plan (VNI, gateway, span), and VLAN and prefix objects are created in the VRF.",
    "Standard firewall rules": "<b>Standard Firewall Rules</b>: ICMP to the new subnet from every other zone, rule 1000 + VLAN ID.",
    "Switches pipeline": "<b>evpn-3tier-switches</b> in Gitea: inventory from Nautobot → render → check mode → apply → verify (61 checks). The worker follows the job's log live.",
    "Firewalls pipeline": "<b>evpn-3tier-firewalls</b>: the subnet goes into prefix-list PUBLIC (the WAN learns it), plus the new ICMP rules.",
    "Device and WAN checks": "<b>Checks on the devices</b>: VNI and gateway on every leaf, the borders' WAN export, the firewalls' routes and rules, and the WAN's routing table.",
    "Ping test from the WAN": "<b>Ping test from the WAN</b>: every web, app and db VM, plus the new gateway, must answer.",
  };
  speed = 10;
  await badge("⏩ 10× speed");
  let last = "", r;
  const t0 = Date.now();
  while (Date.now() - t0 < 20 * 60 * 1000) {
    r = await api(`/api/requests/${id}`);
    if (["done", "failed", "rejected", "cancelled"].includes(r.status)) break;
    const st = r.stage || "";
    if (st && st !== last) {
      log("stage", st, r.percent);
      last = st;
      await caption(`Running · ${st} · ${r.percent ?? 0}%`, CAP[st] || st);
      if (st === "Firewalls pipeline") {
        const h = await page.$(byText("h2", "evpn-3tier-firewalls")) || await page.$(byText("*[self::h2 or self::span or self::div]", "evpn-3tier-firewalls"));
        if (h) await scrollToEl(h, 120);
      } else if (st === "Device and WAN checks" || st === "Ping test from the WAN") {
        const h = await page.$(byText("h2", "History"));
        if (h) await scrollToEl(h, 60);
      } else await scrollTo(0);
    } else if (st) {
      await ev((k, p) => { const e = document.querySelector("#__cap .k"); if (e) e.textContent = `Running · ${k} · ${p}%`; }, st, r.percent ?? 0);
    }
    await sleep(2000);
  }
  log("finished", r.status);
  speed = 1;
  await badge(null);
  await scrollTo(0);
  await caption("Done", r.status === "done"
    ? `Request <b>#${id}</b> is done: every stage passed, in the time shown under each one.`
    : `Request #${id} ended as <b>${r.status}</b>.`);
  await hold(4.5);
  const hist = await page.$(byText("h2", "History"));
  if (hist) { await scrollToEl(hist, 60); }
  await caption("Done", "The history records every step: the reservations, the two pipeline runs, the checks, and the ping results from the WAN.");
  await hold(6);
  await ev(() => window.scrollBy({ top: 330, behavior: "smooth" }));
  await hold(4);
  const det = r.parameters || {};
  const subnet = det.subnet || (alloc && alloc.subnet) || "";
  const vid = det.vlan_id || (alloc && alloc.vlan_id) || "";
  log("params", JSON.stringify(det));

  // ---- proof on the devices: the WAN's routing table --------------------------------------------------------------------
  await page.goto(BASE + "/tools/state", { waitUntil: "domcontentloaded" });
  await ready();
  await caption("Retrieve device state", `Now check from outside the workflow. Open the <b>WAN router</b> and read its routes and BGP AS paths live.`);
  await click(`xpath//button[strong[normalize-space(.)='wan']]`);
  await hold(1.5);
  for (const lbl of ["Interfaces", "MAC addresses"]) { const h = await page.$(`xpath/(//button[contains(@class,'chip') and contains(@class,'on') and normalize-space(.)=${JSON.stringify(lbl)}])`); if (h) await click(h); }
  { const h = await page.$(`xpath/(//button[contains(@class,'chip') and normalize-space(.)="BGP AS paths"])`); if (h) await click(h); }
  await click(byText("button", "Retrieve"));
  await hold(4);
  if (subnet) {
    const net = subnet.split("/")[0];
    const hit = await page.waitForSelector(`xpath/(//*[not(*) and contains(normalize-space(.), ${JSON.stringify(net)})])[1]`, { timeout: 30000 }).catch(() => null);
    if (hit) {
      await scrollToEl(hit, 300);
      await highlight(`xpath/(//*[not(*) and contains(normalize-space(.), ${JSON.stringify(net)})])[1]/ancestor::tr[1]|(//*[not(*) and contains(normalize-space(.), ${JSON.stringify(net)})])[1]`);
      await caption("Retrieve device state", `The WAN has learned <b>${subnet}</b> over eBGP from both border leaves. The firewall zone policy still governs the traffic.`);
      await hold(6);
      await highlight(null);
      const ap = await page.$(`xpath/(//*[not(*) and contains(normalize-space(.), ${JSON.stringify(net)})])[last()]`);
      if (ap) { await scrollToEl(ap, 300); await highlight(`xpath/(//*[not(*) and contains(normalize-space(.), ${JSON.stringify(net)})])[last()]/ancestor::tr[1]`).catch(() => {});
        await caption("Retrieve device state", "And its <b>AS path</b>: in through a border leaf, out through the active firewall (fw1).");
        await hold(5); await highlight(null); }
    }
  }

  // ---- proof on the devices: leaf1's configuration ----------------------------------------------------------------------
  await page.goto(BASE + "/tools/configs", { waitUntil: "domcontentloaded" });
  await ready();
  await caption("View device configurations", "Device configurations, redacted, to view or download. Here's <b>leaf1</b>.");
  await click(`xpath//button[strong[normalize-space(.)='leaf1']]`);
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
  await caption(null);
  await card(`<div class="brand"><img src="${LOGO}" width="64" height="64">FABRIC FACTORY</div>
    <h1>Intent in, verified change out</h1>
    <p>phpIPAM and Nautobot hold the truth · four Gitea pipelines with Ansible configure 20 VyOS and Alpine VMs · every change checked on the devices and from the WAN</p>`);
  await hold(6);
  await cdp.send("Page.stopScreencast");
  times.push(vclock + 0.5);
  fs.writeFileSync(path.join(__dirname, "timing.json"), JSON.stringify({ times, id, status: r.status, vid, subnet }));
  log("frames", n, "virtual seconds", vclock.toFixed(1));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
