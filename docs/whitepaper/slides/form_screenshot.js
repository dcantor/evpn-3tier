// Screenshot of the Add a VLAN form after "Select the next unused VLAN ID" (nothing is submitted).
const puppeteer = require("puppeteer-core");
// Fabric Factory's admin login, read at run time from ~/.config/evpn-3tier/selfservice.env (never stored here)
const FF = (() => {
  const t = require("fs").readFileSync(require("os").homedir() + "/.config/evpn-3tier/selfservice.env", "utf8");
  const g = (k) => ((t.match(new RegExp("^" + k + "=(.*)$", "m")) || [])[1] || "").trim().replace(/^["']|["']$/g, "");
  return { user: g("ADMIN_USER") || "admin", pass: g("ADMIN_PASSWORD") };
})();

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({ executablePath: "/usr/bin/google-chrome", headless: "new", args: ["--no-sandbox", "--hide-scrollbars"] });
  const p = await b.newPage();
  await p.setViewport({ width: 1440, height: 1100, deviceScaleFactor: 2 });
  await p.goto("http://127.0.0.1:8120/", { waitUntil: "domcontentloaded" });
  await p.waitForSelector("input[autocomplete=username]");
  await p.type("input[autocomplete=username]", FF.user);
  await p.type("input[type=password]", FF.pass);
  await p.click("button.btn.primary");
  await sleep(2500);
  await p.goto("http://127.0.0.1:8120/new/add-vlan", { waitUntil: "domcontentloaded" });
  await p.waitForSelector("#f-vrf");
  await p.select("#f-vrf", "web");
  await sleep(500);
  const btn = await p.$("xpath/(//button[contains(normalize-space(.),'Select the next unused VLAN ID')])[1]");
  await btn.click();
  await sleep(3000);
  const card = await p.$("xpath/(//section[contains(@class,'card')][.//select[@id='f-vrf']])[1]");
  await card.screenshot({ path: process.argv[2] });
  await b.close();
})();
