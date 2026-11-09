// Screenshots of the Fabric Dashboard for the deck and the white paper (read-only page; nothing changes).
// node dashboard_screenshot.js <out-dir>  ->  dashboard.png (tiles, design KPIs, topology), dashboard-health.png (live health)
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
  const out = process.argv[2] || ".";
  const b = await puppeteer.launch({ executablePath: "/usr/bin/google-chrome", headless: "new", args: ["--no-sandbox", "--hide-scrollbars"] });
  const p = await b.newPage();
  await p.emulateMediaFeatures([{ name: "prefers-color-scheme", value: "light" }]);
  await p.setViewport({ width: 1340, height: 1000, deviceScaleFactor: 2 });
  await p.goto("http://127.0.0.1:8120/", { waitUntil: "domcontentloaded" });
  await p.waitForSelector("input[autocomplete=username]");
  await p.type("input[autocomplete=username]", FF.user);
  await p.type("input[type=password]", FF.pass);
  await p.click("button.btn.primary");
  await sleep(2500);
  await p.goto("http://127.0.0.1:8120/tools/fabric", { waitUntil: "domcontentloaded" });
  await p.waitForSelector(".health-wrap", { timeout: 90000 });
  await sleep(800);
  const box = (xp) => p.$eval(`xpath/${xp}`, (e) => { const r = e.getBoundingClientRect(); return { x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height }; });
  const card = (t) => `(//section[contains(@class,'card')][.//h2[contains(normalize-space(.), "${t}")]])[1]`;
  const shot = async (top, bottom, file) => {
    const pad = 12;
    await p.screenshot({ path: path.join(out, file), captureBeyondViewport: true,
      clip: { x: top.x - pad, y: top.y - pad, width: top.w + 2 * pad, height: bottom.y + bottom.h - top.y + 2 * pad } });
  };
  await shot(await box("(//div[contains(@class,'fab-tiles')])[1]"), await box(card("Topology")), "dashboard.png");
  const h = await box(card("Live health"));
  // the live-health card down to the last network device's row (wan)
  const last = await box(`${card("Live health")}//tr[.//span[normalize-space(.)='wan']]`);
  await shot(h, { ...last, y: last.y, h: last.h }, "dashboard-health.png");
  await b.close();
})();
