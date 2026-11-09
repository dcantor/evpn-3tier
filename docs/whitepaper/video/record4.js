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
const OUT = path.join(__dirname, "part4");
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

  // ---- Port Provisioning ---------------------------------------------------------------------------------------------------
  await caption(null);
  await card(`<div class="n">NEW</div><h1>Port Provisioning</h1>
    <p>Enable or disable a leaf port, and set a spare port up as an access port or a trunk — with the uplinks to the spines blocked.</p>`);
  await hold(4.5);
  await page.goto(BASE + "/new", { waitUntil: "domcontentloaded" }); await ready(); await card(null);
  const wfC = `xpath/(//section[contains(@class,'card')][.//h3[normalize-space(.)='Port Provisioning']])[1]`;
  await page.waitForSelector(wfC); await scrollToEl(wfC, 140);
  await highlight(wfC); await moveTo(wfC, 0.5, 0.2);
  await caption("New request", "<b>Port Provisioning</b>: a change request like the others, approved by an operator and built by the switches pipeline.");
  await hold(4.5); await highlight(null);
  await click(`xpath/(//a[@href='/new/port'])[1]`);
  await page.waitForSelector("#f-port"); await ready(); await hold(0.6);
  await caption("Port Provisioning", "Every leaf port is listed: the uplinks to the spines as <b>blocked</b>, the servers' bond legs, and two <b>spare ports</b> per leaf (eth6, eth7).");
  await moveTo("#f-port"); await hold(4.5);
  // an uplink: refused
  await click("#f-port"); await page.select("#f-port", "leaf1|eth1"); await hold(0.6);
  await click("#f-state"); await page.select("#f-state", "disabled"); await hold(0.5);
  await type("#f-reason", "Maintenance on the uplink");
  await caption("Port Provisioning", "Try to shut <b>leaf1 eth1</b>, an uplink to spine1…");
  await hold(1.5);
  await click(byText("button", "Submit for approval"));
  await page.waitForSelector(".note.bad", { timeout: 15000 }); await hold(0.5);
  await scrollToEl(".note.bad", 140); await highlight(".note.bad");
  await caption("Blocked", "…refused before it reaches anyone: <b>uplinks to the spines carry the whole fabric</b> and cannot be changed here. The worker checks it again.");
  await hold(6.5); await highlight(null);
  // a spare port as a trunk, live
  await click("#f-port"); await page.select("#f-port", "leaf1|eth6"); await hold(0.5);
  await click("#f-state"); await page.select("#f-state", "enabled"); await hold(0.4);
  await click("#f-mode"); await page.select("#f-mode", "tagged"); await hold(0.5);
  await caption("Port Provisioning", "Now a spare port: <b>leaf1 eth6</b>, enabled, as a <b>trunk</b> — pick the VLANs it carries, tagged. Only VLANs the leaves carry are offered.");
  for (const label of ["41 ", "47 "]) await click(`xpath/(//div[contains(@class,'multi')]//button[starts-with(normalize-space(.), ${JSON.stringify(label)})])[1]`);
  await hold(1);
  await highlight(`xpath/(//div[contains(@class,'multi')])[1]`); await hold(3.5); await highlight(null);
  await type("#f-description", "backup appliance");
  await ev(() => { const t = document.querySelector("#f-reason"); t.focus(); t.select(); });
  await page.keyboard.press("Backspace");
  await type("#f-reason", "A backup appliance on leaf1 needs the web VLANs 41 and 47");
  await click(byText("button", "Submit for approval"));
  await page.waitForFunction(() => /\/requests\/\d+$/.test(location.pathname), { timeout: 20000 });
  const pid = Number((await page.url()).match(/requests\/(\d+)/)[1]); log("port request", pid);
  await ready(); await hold(1.5);
  await caption("Approval", `Request <b>#${pid}</b>: the operator approves it.`);
  await click(byText("button", "Approve")); await hold(1);
  await click(`xpath/(//footer//button[contains(normalize-space(.), "Approve")])[last()]`); await hold(1);
  const PCAP = {
    "Pre-flight checks": "<b>Pre-flight</b>: a leaf port, not an uplink; for a server's leg, its other leg must be up.",
    "Nautobot": "<b>Nautobot</b>: the port's native interface fields — enabled, mode tagged, VLANs 41 and 47, the description.",
    "Switches pipeline": "<b>evpn-3tier-switches</b>: renders the port's bridge membership from Nautobot, applies it to leaf1, then verifies the whole lab.",
    "Port checks": "<b>Checks on leaf1</b>: the port's admin state from VyOS, and its VLANs straight from the kernel bridge.",
    "Ping test from the WAN": "<b>Ping test from the WAN</b>: every web, app and db server still answers.",
  };
  speed = 10; await badge("⏩ 10× speed");
  let lastS = "", pr;
  const tp0 = Date.now();
  while (Date.now() - tp0 < 15 * 60 * 1000) {
    pr = await api(`/api/requests/${pid}`);
    if (["done", "failed"].includes(pr.status)) break;
    const st = pr.stage || "";
    if (st && st !== lastS) { lastS = st; await caption(`Running · ${st} · ${pr.percent ?? 0}%`, PCAP[st] || st); }
    await sleep(2000);
  }
  speed = 1; await badge(null); log("port request", pr.status);
  await scrollTo(0); await hold(1.5);
  const ol = `xpath/(//div[contains(@class,'ev-m')][contains(., 'on the leaf')])[1]`;
  if (await page.$(ol)) {
    await highlight(ol); await moveTo(ol, 0.1, 0.5);
    await caption("Done", `Request <b>#${pid}</b> is done: leaf1 eth6 is up and its bridge carries exactly <b>VLANs 41 and 47, tagged</b> — and every server still answers.`);
    await hold(7); await highlight(null);
  }
  await caption(null);
  await card(`<div class="brand"><img src="${LOGO}" width="64" height="64">FABRIC FACTORY</div>
    <h1>Intent in, verified change out</h1>
    <p>phpIPAM and Nautobot hold the truth · four Gitea pipelines with Ansible configure 20 VyOS and Alpine VMs · every change checked on the devices and from the WAN</p>`);
  await hold(6);
  await cdp.send("Page.stopScreencast");
  times.push(vclock + 0.5);
  fs.writeFileSync(path.join(__dirname, "timing4.json"), JSON.stringify({ times, id, status: r.status, vid, subnet }));
  log("frames", n, "virtual seconds", vclock.toFixed(1));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
