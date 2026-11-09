// Screenshots of Port Provisioning for the deck and the white paper (nothing is submitted).
// node port_screenshot.js <out-dir> <request-id>  ->  port-form.png (a spare port as a trunk), port-request.png (a finished request)
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
  const [out, req] = [process.argv[2] || ".", process.argv[3] || "34"];
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
      return { x: a.left + scrollX - 14, y: a.top + scrollY - 14, w: Math.max(a.right, z.right) - a.left + 28, h: z.bottom - a.top + 28 }; }, top, bottom);
    await p.screenshot({ path: path.join(out, file), captureBeyondViewport: true, clip: { x: r.x, y: r.y, width: r.w, height: r.h } });
  };
  // the form: leaf1 eth6 as a trunk carrying VLANs 41 and 47
  await p.goto("http://127.0.0.1:8120/new/port", { waitUntil: "domcontentloaded" });
  await p.waitForSelector("#f-port"); await sleep(400);
  await p.select("#f-port", "leaf1|eth6"); await p.select("#f-state", "enabled"); await p.select("#f-mode", "tagged");
  for (const label of ["41 ", "47 "]) {
    const c = await p.$(`xpath/(//div[contains(@class,'multi')]//button[starts-with(normalize-space(.), ${JSON.stringify(label)})])[1]`); await c.click();
  }
  await p.type("#f-description", "backup appliance"); await p.type("#f-reason", "A backup appliance on leaf1 needs the web VLANs 41 and 47");
  await sleep(300);
  await p.evaluate(() => { document.querySelector("main h1").id = "__t"; });
  await clip("port-form.png", "#__t", "main form");
  // a finished request: its stages and its history
  await p.goto(`http://127.0.0.1:8120/requests/${req}`, { waitUntil: "domcontentloaded" });
  await p.waitForSelector("ol.stepper"); await sleep(800);
  await p.evaluate(() => {
    document.querySelector("main h1").id = "__t";
    const h = [...document.querySelectorAll("section.card")].find((s) => s.querySelector("h2")?.textContent.trim() === "History"); h.id = "__h";
    const req = [...document.querySelectorAll("section.card")].find((s) => s.querySelector("h2")?.textContent.trim() === "Request"); if (req) req.id = "__r";
  });
  await clip("port-request.png", "#__t", "ol.stepper");
  await clip("port-history.png", "#__h", "#__h");
  await b.close();
})().catch((e) => { console.error(String(e).slice(0, 300)); process.exit(1); });
