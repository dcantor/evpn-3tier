// Screenshots of Set up a load balancer (pairs) for the deck and the white paper (read-only pages; the form is not submitted).
// node lb_screenshot.js <out-dir> [request] [form]  ->  lb-request.png (the request), lb-form.png (Add a pair, tier app)
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
  const [out, req, form] = [process.argv[2] || ".", process.argv[3], process.argv[4]];
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
  if (req && req !== "-") {
    await p.goto(`http://127.0.0.1:8120/requests/${req}`, { waitUntil: "domcontentloaded" }); await p.waitForSelector("ol.stepper"); await sleep(900);
    await mark(); await clip("lb-request.png", "#__t", "ol.stepper");
  }
  if (form) {
    await p.goto("http://127.0.0.1:8120/new/lb-peering", { waitUntil: "domcontentloaded" }); await p.waitForSelector("#f-action"); await sleep(800);
    await p.select("#f-action", "add"); await p.waitForSelector("#f-tier"); await p.select("#f-tier", "app");
    await p.type("#f-reason", "A load balancer pair for the app tier"); await sleep(400);
    await mark(); await clip("lb-form.png", "#__t", "main .grid-form, #__c0");
  }
  await b.close();
})().catch((e) => { console.error(String(e).slice(0, 300)); process.exit(1); });
