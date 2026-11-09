"""record3.js = record2.js's set-up (overlay, recorder, helpers, sign-ins) + the ASN / VLAN database segment + the closing
card; frames to part3/, timings to timing3.json. The assembler cuts part2 before its closing card and appends part3."""
s = open("record2.js").read()
head = s[: s.index("  await startRec();") + len("  await startRec();\n")]
tail = s[s.index('  await caption(null);\n  await card(`<div class="brand">'):]
head = head.replace('const OUT = path.join(__dirname, "part2");', 'const OUT = path.join(__dirname, "part3");')
tail = tail.replace('"timing2.json"', '"timing3.json"')
seg = r'''
  // ---- the ASN and VLAN databases ----------------------------------------------------------------------------------------
  const { execFileSync } = require("child_process");
  const DL = path.join(__dirname, "downloads"); fs.rmSync(DL, { recursive: true, force: true }); fs.mkdirSync(DL);
  await cdp.send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: DL }).catch(() =>
    cdp.send("Page.setDownloadBehavior", { behavior: "allow", downloadPath: DL }));
  const newest = async (prefix) => {                     // wait for the browser to finish writing the download
    for (let i = 0; i < 40; i++) {
      const f = fs.readdirSync(DL).filter((x) => x.startsWith(prefix) && x.endsWith(".xlsx"));
      if (f.length) return path.join(DL, f.sort().pop());
      await sleep(250);
    }
    return null;
  };
  const preview = async (file, caps) => {                 // the downloaded workbook, as a spreadsheet grid
    const out = path.join(__dirname, path.basename(file) + ".html");
    execFileSync("/home/dcantor/evpn-3tier/selfservice/.venv/bin/python", [path.join(__dirname, "xlsx2html.py"), file, out]);
    await page.goto("file://" + out, { waitUntil: "load" }); await ready();
    for (const [k, c, s] of caps) { await caption(k, c); await hold(s); }
  };
  await caption(null);
  await card(`<div class="n">NEW</div><h1>The ASN and VLAN databases</h1>
    <p>Every AS and every VLAN of the fabric, read from Nautobot, searchable, and downloadable as an Excel workbook.</p>`);
  await hold(4.5);
  await page.goto(BASE + "/new", { waitUntil: "domcontentloaded" }); await ready(); await card(null);
  const toolCard = (t) => `xpath/(//section[contains(@class,'card')][.//h3[normalize-space(.)=${JSON.stringify(t)}]])[1]`;
  await page.waitForSelector(toolCard("ASN Database"));
  await scrollToEl(toolCard("ASN Database"), 120);
  await caption("New request · Tools", "Two new tools next to the Fabric Dashboard: the <b>ASN Database</b> and the <b>VLAN Database</b>. Read-only, no approval.");
  for (const t of ["ASN Database", "VLAN Database"]) { await highlight(toolCard(t)); await moveTo(toolCard(t), 0.5, 0.2); await hold(2); }
  await highlight(null);
  // ASN Database
  await click(`xpath/(//a[@href='/tools/asns'])[1]`);
  await page.waitForSelector(".db-table"); await ready(); await hold(0.8);
  const asn = await api("/api/tools/asns");
  await highlight(".tiles");
  await caption("ASN Database", `<b>${asn.summary.asns} ASNs</b>, <b>${asn.summary.sessions} BGP sessions</b> in the design, ${asn.summary.private_used} of ${asn.summary.private_pool} private ASNs used.`);
  await hold(5); await highlight(null);
  const T = (title) => `xpath/(//section[contains(@class,'card')][.//h2[normalize-space(.)=${JSON.stringify(title)}]])[1]`;
  await scrollToEl(T("ASNs"), 75);
  await caption("ASN Database", "Each AS with its devices, router IDs, the ASNs it peers with and its VRFs: the spines share <b>65100</b>, every leaf and border has its own.");
  await hold(5.5);
  const chip = (card, label) => `${T(card)}//button[contains(@class,'chip') and normalize-space(.)=${JSON.stringify(label)}]`;
  await click(chip("ASNs", "Firewall")); await hold(0.6);
  await highlight(`xpath/(${T("ASNs").slice(6)}//tbody/tr)[1]`);
  await caption("ASN Database", "Filter by kind: the firewall pair shares <b>65200</b> and peers with both border leaves in every VRF — 16 sessions.");
  await hold(5); await highlight(null);
  await click(chip("ASNs", "Load balancer")); await hold(0.6);
  await highlight(`xpath/(${T("ASNs").slice(6)}//table)[1]`);
  await caption("ASN Database", "The load balancers: <b>65310</b> and <b>65320</b>, each peering with the border pair over its own VLAN — added by self-service requests.");
  await hold(5); await highlight(null);
  await click(chip("ASNs", "All")); await hold(0.4);
  await scrollToEl(T("BGP sessions"), 75);
  await click(chip("BGP sessions", "Firewall transit")); await hold(0.6);
  await caption("ASN Database · BGP sessions", "Every BGP session of the design, both ends: here the firewall transit sessions, one per border, firewall and VRF, all with BFD.");
  await hold(5.5);
  await click(chip("BGP sessions", "All")); await hold(0.3);
  await scrollTo(0); await hold(0.6);
  await caption("ASN Database", "<b>Download Excel</b>: a workbook built on the server, with the ASNs, the BGP sessions and an About sheet.");
  await click(`xpath/(//a[contains(@href,'/api/tools/asns.xlsx')])[1]`);
  const fa = await newest("evpn-3tier-asns"); log("download", fa);
  await hold(2.5);
  if (fa) await preview(fa, [["The downloaded workbook", "The workbook itself: a frozen header row, a filter on every column, numbers as numbers — and its sheets: ASNs, BGP sessions, About.", 6.5]]);
  // VLAN Database
  await page.goto(BASE + "/tools/vlans", { waitUntil: "domcontentloaded" }); await page.waitForSelector(".db-table"); await ready(); await hold(0.6);
  const vl = await api("/api/tools/vlans");
  await highlight(".tiles");
  await caption("VLAN Database", `<b>${vl.summary.vlans} VLANs</b>: ${Object.entries(vl.summary.by_type).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(", ")}; <b>${vl.summary.self_service}</b> made by self-service requests.`);
  await hold(5.5); await highlight(null);
  await scrollToEl(T("VLANs"), 75);
  await caption("VLAN Database", "Each VLAN with its name, VRF, VNI, subnet, gateway, where it lives, its hosts — and its origin: the lab's design, or the request that made it.");
  await hold(5.5);
  await click(chip("VLANs", "Tier")); await hold(0.4);
  await click(`${T("VLANs")}//button[contains(@class,'chip') and normalize-space(.)='web']`); await hold(0.6);
  const v47 = `${T("VLANs")}//tbody/tr[td[1][normalize-space(.)='47']]`;
  const capAt = (top) => ev((top) => { const c = document.getElementById("__cap"); c.style.top = top ? "78px" : ""; c.style.bottom = top ? "auto" : ""; }, top);
  if (await page.$(v47)) { await highlight(v47); await moveTo(`${v47}//a`, 0.5, 0.5); }
  await capAt(true);                                   // the row is at the page's bottom: the caption goes to the top
  await caption("VLAN Database", "Filtered to the web tier: VLAN <b>47</b>, from the Add a VLAN in this video — its origin links to request <b>#18</b>.");
  await hold(5.5); await highlight(null); await caption(null); await capAt(false);
  await click(chip("VLANs", "All")); await hold(0.3);
  await click(`${T("VLANs")}//button[contains(@class,'chip') and normalize-space(.)='any']`); await hold(0.3);
  await click(chip("VLANs", "Firewall transit")); await hold(0.6);
  await caption("VLAN Database", "The firewall transit VLANs: a /28 per VRF between the border leaves and the firewall pair.");
  await hold(4.5);
  await click(chip("VLANs", "All")); await hold(0.3);
  await scrollTo(0); await hold(0.5);
  await caption("VLAN Database", "<b>Download Excel</b> again: every VLAN, one row each.");
  await click(`xpath/(//a[contains(@href,'/api/tools/vlans.xlsx')])[1]`);
  const fv = await newest("evpn-3tier-vlans"); log("download", fv);
  await hold(2);
  if (fv) await preview(fv, [["The downloaded workbook", "The VLAN workbook: ID, name, type, VRF, VNI, subnet, gateway, usable addresses, where, hosts, origin — ready to filter and share.", 6.5]]);
'''
open("record3.js", "w").write(head + seg + tail)
print("written", len(head), len(seg), len(tail))
