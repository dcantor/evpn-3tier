// Screenshots of Add a leaf pair and Add a VRF for the deck and the white paper (read-only pages).
// node grow_screenshot.js <out-dir> <leaf-pair request> <vrf request>  ->  lp-request.png, vrf-request.png, grow-dash.png, vrf-form.png
const path = require("path");
const puppeteer = require("puppeteer-core");
// Fabric Factory's admin login, read at run time from ~/.config/evpn-3tier/selfservice.env (never stored here)
const FF = (() => {
  const t = require("fs").readFileSync(require("os").homedir() + "/.config/evpn-3tier/selfservice.env", "utf8");
  const g = (k) => ((t.match(new RegExp("^" + k + "=(.*)$", "m")) || [])[1] || "").trim().replace(/^["']|["']$/g, "");
  return { user: g("ADMIN_USER") || "admin", pass: g("ADMIN_PASSWORD") };
})();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const [out, lp, vr] = [process.argv[2] || ".", process.argv[3], process.argv[4]];
  const b = await puppeteer.launch({ executablePath: "/usr/bin/google-chrome", headless: "new", args: ["--no-sandbox", "--hide-scrollbars"] });
  const p = await b.newPage();
  await p.emulateMediaFeatures([{ name: "prefers-color-scheme", value: "light" }]);
  await p.setViewport({ width: 1340, height: 1000, deviceScaleFactor: 2 });
  await p.goto("http://127.0.0.1:8120/", { waitUntil: "domcontentloaded" });
  await p.waitForSelector("input[autocomplete=username]");
  await p.type("input[autocomplete=username]", FF.user); await p.type("input[type=password]", FF.pass);
  await p.click("button.btn.primary"); await sleep(2500);
  const clip = async (file, top, bottom) => {
    const r = await p.evaluate((t, bt) => { const a = document.querySelector(t).getBoundingClientRect(), z = document.querySelector(bt).getBoundingClientRect();
      const right = Math.max(...[...document.querySelectorAll("main section.card")].map((s) => s.getBoundingClientRect().right), a.right);
      return { x: a.left + scrollX - 14, y: a.top + scrollY - 14, w: right - a.left + 28, h: z.bottom - a.top + 28 }; }, top, bottom);
    await p.screenshot({ path: path.join(out, file), captureBeyondViewport: true, clip: { x: r.x, y: r.y, width: r.w, height: r.h } });
  };
  const mark = () => p.evaluate(() => { document.querySelector("main h1").id = "__t"; const c = [...document.querySelectorAll("main section.card")]; c[0].id = "__c0"; });
  const Tc = (t) => `xpath/(//section[contains(@class,'card')][.//h2[normalize-space(.)=${JSON.stringify(t)}]][not(.//section[contains(@class,'card')][.//h2[normalize-space(.)=${JSON.stringify(t)}]])])[last()]`;
  const shot = async (file, sel) => { const e = await p.waitForSelector(sel); await e.evaluate((x) => x.scrollIntoView()); await sleep(400);
    const bx = await e.boundingBox(); await p.screenshot({ path: path.join(out, file), clip: { x: bx.x - 8, y: bx.y - 8, width: bx.width + 16, height: bx.height + 16 } }); };
  for (const [file, id] of [["lp-request.png", lp], ["vrf-request.png", vr]]) {
    await p.goto(`http://127.0.0.1:8120/requests/${id}`, { waitUntil: "domcontentloaded" }); await p.waitForSelector("ol.stepper"); await sleep(900);
    await mark(); await clip(file, "#__t", "ol.stepper");
  }
  await p.goto("http://127.0.0.1:8120/tools/fabric", { waitUntil: "domcontentloaded" }); await p.waitForSelector(Tc("VRFs")); await sleep(1500);
  const r = await p.evaluate(() => { const h = document.querySelector("main h1").getBoundingClientRect();
    const k = [...document.querySelectorAll("main h2, main h3")].find((e) => e.textContent.includes("Design KPIs")).getBoundingClientRect();
    const m = document.querySelector("main").getBoundingClientRect();
    return { x: m.left + 10, y: h.top + scrollY - 16, w: m.width - 20, h: k.top - h.top - 10 }; });
  await p.screenshot({ path: path.join(out, "grow-dash.png"), clip: { x: r.x, y: r.y, width: r.w, height: r.h } });
  await p.goto("http://127.0.0.1:8120/new/add-vrf", { waitUntil: "domcontentloaded" }); await p.waitForSelector("#f-vrf"); await sleep(800);
  await p.type("#f-vrf", "pay"); await p.type("#f-reason", "A zone for the payments service"); await sleep(300);
  await mark(); await clip("vrf-form.png", "#__t", "main .grid-form, #__c0");
  await b.close();
})().catch((e) => { console.error(String(e).slice(0, 300)); process.exit(1); });
