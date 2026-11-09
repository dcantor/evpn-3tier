// Screenshots of the ASN Database and the VLAN Database for the deck and the white paper (read-only pages).
// node db_screenshot.js <out-dir>  ->  asn-db.png, asn-sessions.png, vlan-db.png
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
  await p.type("input[autocomplete=username]", FF.user); await p.type("input[type=password]", FF.pass);
  await p.click("button.btn.primary"); await sleep(2500);
  // from the page title down to row `rows` of the table inside the card titled `card`
  const shot = async (file, card, rows, fromTitle = true) => {
    const r = await p.evaluate((card, rows, fromTitle) => {
      const sec = [...document.querySelectorAll("section.card")].find((s) => s.querySelector("h2")?.textContent.trim() === card);
      const trs = sec.querySelectorAll("tbody tr"); const last = trs[Math.min(rows, trs.length) - 1];
      const top = (fromTitle ? document.querySelector("main h1") : sec).getBoundingClientRect(), s = sec.getBoundingClientRect(), l = last.getBoundingClientRect();
      return { x: s.left + scrollX - 14, y: top.top + scrollY - 14, w: s.width + 28, h: l.bottom - top.top + 22 };
    }, card, rows, fromTitle);
    await p.screenshot({ path: path.join(out, file), captureBeyondViewport: true, clip: { x: r.x, y: r.y, width: r.w, height: r.h } });
  };
  await p.goto("http://127.0.0.1:8120/tools/asns", { waitUntil: "domcontentloaded" });
  await p.waitForSelector(".db-table"); await sleep(600);
  await shot("asn-db.png", "ASNs", 12);
  await shot("asn-sessions.png", "BGP sessions", 8, false);
  await p.goto("http://127.0.0.1:8120/tools/vlans", { waitUntil: "domcontentloaded" });
  await p.waitForSelector(".db-table"); await sleep(600);
  await shot("vlan-db.png", "VLANs", 12);
  await b.close();
})().catch((e) => { console.error(String(e).slice(0, 300)); process.exit(1); });
