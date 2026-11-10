// Screenshots of applications (port owners) and port profiles for the deck and the white paper (read-only pages).
// node owners_screenshot.js <out-dir> <request-id>  ->  own-apps.png, own-profiles.png, own-request.png
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
  const [out, req] = [process.argv[2] || ".", process.argv[3]];
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
  await p.goto("http://127.0.0.1:8120/tools/applications", { waitUntil: "domcontentloaded" }); await p.waitForSelector("main table"); await sleep(600);
  await mark(); await clip("own-apps.png", "#__t", "main .grid-form, #__c0");
  await p.goto("http://127.0.0.1:8120/tools/port-profiles", { waitUntil: "domcontentloaded" }); await p.waitForSelector("main table"); await sleep(600);
  await mark(); await clip("own-profiles.png", "#__t", "#__c0");
  await p.goto(`http://127.0.0.1:8120/requests/${req}`, { waitUntil: "domcontentloaded" }); await p.waitForSelector("ol.stepper"); await sleep(800);
  await mark(); await clip("own-request.png", "#__t", "ol.stepper");
  await b.close();
})().catch((e) => { console.error(String(e).slice(0, 300)); process.exit(1); });
