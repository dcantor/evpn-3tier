// Screenshots of where evpn-3tier lives in Nautobot, for the deck and the white paper (read-only: pages are only viewed;
// GraphiQL runs a query). Signs in with the NMS's Nautobot superuser, read over SSH at run time and never printed.
//   node nautobot_screenshots.js <out-dir>
// -> nb-devices.png, nb-interfaces.png, nb-prefixes.png, nb-vlans.png, nb-changelog.png, nb-graphql.png
const path = require("path");
const { execFileSync } = require("child_process");
const puppeteer = require("puppeteer-core");

const NB = "http://10.0.0.10:8080";
const LEAF1 = "afc3f06e-043e-47c0-b451-b1180c0b7fec";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const env = (k) => execFileSync("ssh", ["-o", "BatchMode=yes", "lab@10.0.0.10", `grep ^${k}= /opt/nautobot/.env | cut -d= -f2-`]).toString().trim();

// what GraphiQL shows: the leaves with their uplink cabling, and the prefix of the demo's VLAN 47
const QUERY = `{
  devices(location: ["evpn-3tier"], role: ["e3t-leaf"]) {
    name rack { name } primary_ip4 { address }
    interfaces(name: ["eth1", "eth2"]) {
      name cable_peer_interface { name device { name } }
    }
  }
  prefixes(namespace: ["evpn-3tier"], q: "172.16.6") {
    prefix vrfs { name } vlan { vid name } description
  }
}`;
// the interface table has 20 columns; show the ones this lab fills (checkbox, name, status, type, MTU, VRF, description, cable, connection, IPs)
const IF_CSS = [4, 5, 6, 7, 9, 10, 11, 12, 13, 16, 21, 22].map((n) => `table tr > *:nth-child(${n})`).join(",") + "{display:none}";

async function login(p) {
  await p.goto(NB + "/login/", { waitUntil: "networkidle2" });
  await p.type("input[name=username]", env("NAUTOBOT_SUPERUSER_NAME"));
  await p.type("input[name=password]", env("NAUTOBOT_SUPERUSER_PASSWORD"));
  await Promise.all([p.waitForNavigation({ waitUntil: "networkidle2" }), p.click("button[type=submit]")]);
}

async function collapse(p) {
  const t = await p.$("button.nb-sidenav-toggler[aria-expanded=true]");
  if (t) { await t.click(); await sleep(500); }
}

// clip from the page title down to the bottom of row `rows` of the first table (all rows when omitted)
async function shotTable(p, file, rows) {
  const r = await p.evaluate((rows) => {
    const h = document.querySelector("h1") || document.querySelector(".nb-page-title") || document.body;
    const t = [...document.querySelectorAll("main table")].find((x) => x.offsetParent);
    const trs = [...t.querySelectorAll("tbody tr")].filter((x) => x.offsetParent);
    const last = trs[Math.min(rows || trs.length, trs.length) - 1] || t;
    const a = h.getBoundingClientRect(), tb = t.getBoundingClientRect(), l = last.getBoundingClientRect();
    return { x: tb.left + scrollX - 14, y: a.top + scrollY - 12, w: tb.width + 28, h: l.bottom - a.top + 14 };
  }, rows);
  await p.screenshot({ path: file, captureBeyondViewport: true, clip: { x: Math.max(0, r.x), y: Math.max(0, r.y), width: r.w, height: r.h } });
}

(async () => {
  const out = process.argv[2] || ".";
  const b = await puppeteer.launch({ executablePath: "/usr/bin/google-chrome", headless: "new", args: ["--no-sandbox", "--hide-scrollbars"] });
  const p = await b.newPage();
  await p.emulateMediaFeatures([{ name: "prefers-color-scheme", value: "light" }]);
  await p.setViewport({ width: 1440, height: 1000, deviceScaleFactor: 2 });
  await login(p);
  const go = async (url) => { await p.goto(NB + url, { waitUntil: "networkidle2" }); await collapse(p); await sleep(400); };

  await go("/dcim/devices/?location=evpn-3tier&per_page=50");
  await shotTable(p, path.join(out, "nb-devices.png"));
  await go(`/dcim/devices/${LEAF1}/interfaces/`);
  await p.addStyleTag({ content: IF_CSS }); await sleep(300);
  await shotTable(p, path.join(out, "nb-interfaces.png"));
  await go("/ipam/prefixes/?namespace=evpn-3tier&per_page=50");
  await shotTable(p, path.join(out, "nb-prefixes.png"), 11);
  await go("/ipam/vlans/?vlan_group=evpn-3tier&per_page=50");
  await shotTable(p, path.join(out, "nb-vlans.png"), 11);
  await go("/extras/object-changes/?per_page=25");
  await shotTable(p, path.join(out, "nb-changelog.png"), 8);       // request #18 (Add VLAN 47): the eight newest changes

  await p.setViewport({ width: 1440, height: 760, deviceScaleFactor: 2 });
  await go("/graphql/");
  await p.waitForSelector(".graphiql-query-editor .CodeMirror");
  await p.evaluate((q) => document.querySelector(".graphiql-query-editor .CodeMirror").CodeMirror.setValue(q), QUERY);
  await p.click(".graphiql-execute-button"); await sleep(2500);
  const g = await p.evaluate(() => { const r = document.querySelector(".graphiql-container").getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; });
  await p.screenshot({ path: path.join(out, "nb-graphql.png"), clip: { x: g.x, y: g.y, width: g.w, height: g.h } });
  await b.close();
})().catch((e) => { console.error(String(e).slice(0, 400)); process.exit(1); });
