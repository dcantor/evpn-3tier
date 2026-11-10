"""record6.js = record2.js's set-up + the leaf pair and VRF segment + the closing card; frames to part5/, timings
to timing5.json. Live: a profile created (#N), leaf2 eth7 provisioned on it for APP-1042 (#N+1); cleaned up off camera."""
s = open("record2.js").read()
head = s[: s.index("  await startRec();") + len("  await startRec();\n")]
tail = s[s.index('  await caption(null);\n  await card(`<div class="brand">'):]
head = head.replace('const OUT = path.join(__dirname, "part2");', 'const OUT = path.join(__dirname, "part6");')
tail = tail.replace('"timing2.json"', '"timing6.json"')
seg = r'''
  // ---- grow the fabric: a leaf pair, a VRF -----------------------------------------------------------------------------------------
  const Tc = (t) => `xpath/(//section[contains(@class,'card')][.//h2[normalize-space(.)=${JSON.stringify(t)}]])[1]`;
  const waitDone = async (id, CAP) => {
    speed = 10; await badge("⏩ 10× speed");
    let last = "", r; const t0 = Date.now();
    while (Date.now() - t0 < 30 * 60 * 1000) {
      r = await api(`/api/requests/${id}`);
      if (["done", "failed"].includes(r.status)) break;
      const st = r.stage || "";
      if (st && st !== last) { last = st; await caption(`Running · ${st} · ${r.percent ?? 0}%`, CAP[st] || st); }
      await sleep(1500);
    }
    speed = 1; await badge(null); log("request", id, r.status); return r;
  };
  const submitApprove = async (who) => {
    await click(byText("button", "Submit for approval"));
    await page.waitForFunction(() => /\/requests\/\d+$/.test(location.pathname), { timeout: 30000 });
    const id = Number((await page.url()).match(/requests\/(\d+)/)[1]); log(who, id);
    await ready(); await hold(1);
    await caption("Approval", `Request <b>#${id}</b>: the operator approves it.`);
    await click(byText("button", "Approve")); await hold(0.8);
    await click(`xpath/(//footer//button[contains(normalize-space(.), "Approve")])[last()]`); await hold(0.8);
    return id;
  };
  const showEvent = async (text, title, body, secs) => {
    const x = `xpath/(//div[contains(@class,'ev-m')][contains(., ${JSON.stringify(text)})])[1]`;
    if (await page.$(x)) { await highlight(x); await moveTo(x, 0.1, 0.5); await caption(title, body); await hold(secs); await highlight(null); }
  };
  await caption(null);
  await card(`<div class="n">NEW</div><h1>Grow the fabric</h1>
    <p><b>Add a leaf pair</b>: a new rack of two leaves, cabled, configured and verified. <b>Add a VRF</b>: a new tier zone behind the firewalls. One request each.</p>`);
  await hold(4.5);
  // ---- a leaf pair
  await page.goto(BASE + "/new/add-leaf-pair", { waitUntil: "domcontentloaded" }); await page.waitForSelector("#f-pair"); await ready(); await card(null); await hold(0.6);
  await click("#f-pair"); await page.select("#f-pair", "5,6"); await hold(0.6);
  await highlight(`xpath/(//label[@for='f-pair']/parent::div)[1]`);
  await caption("Add a leaf pair", "Nothing to fill in: the app allocates the next pair — <b>leaf5 + leaf6 in rack3</b>, their AS numbers, management and loopback addresses, and the spines' next spare ports.");
  await hold(6.5); await highlight(null);
  await highlight(Tc("When it is approved"));
  await caption("Add a leaf pair", "phpIPAM, Nautobot, the VMs racked and cabled, day-0 over the serial console, the switches pipeline, then checks.");
  await hold(5); await highlight(null);
  await type("#f-reason", "Rack 3: room for the next applications");
  const p1 = await submitApprove("leaf pair request");
  await waitDone(p1, {
    "Pre-flight checks": "<b>Pre-flight</b>: host memory, the spines' spare ports, every address and AS number still free.",
    "phpIPAM reservation": "<b>phpIPAM</b>: 30 addresses — management, loopbacks, each leaf's address in every leaf VLAN, probes.",
    "Nautobot": "<b>Nautobot</b>: rack3, both leaves with their interfaces and addresses, four cables to the spines.",
    "Rack and cable": "<b>Rack and cable</b>: the leaves' VMs cabled to spine ports eth7 / eth8, then day-0 over the serial console — the lab's zero-touch provisioning.",
    "Switches pipeline": "<b>evpn-3tier-switches</b>: the leaves' full configuration and the spines' new neighbours, from Nautobot — then the whole lab verified.",
    "Fabric checks": "<b>Checks</b>: BGP and BFD to both spines, the same VNIs and anycast gateways as leaf1, the loopbacks reachable.",
    "Ping test from the WAN": "<b>Ping test from the WAN</b>: every server still answers.",
  });
  await scrollTo(0); await hold(1.2);
  await showEvent("on leaf5:", "Done", "leaf5 and leaf6 are in the fabric: <b>BGP + BFD to both spines</b>, the same VNIs and anycast gateways as leaf1. About twelve minutes.", 6);
  await showEvent("rack and cable:", "Done", "Racked and cabled: their VMs started, day-0 pushed over the serial console.", 4);
  await page.goto(BASE + "/tools/fabric", { waitUntil: "domcontentloaded" }); await page.waitForSelector(Tc("Devices by role")); await ready(); await hold(0.8);
  await highlight(Tc("Devices by role"));
  await caption("Fabric Dashboard", "The dashboard counts <b>six leaves</b> now — read straight from Nautobot.");
  await hold(5.5); await highlight(null);
  // ---- a VRF
  await page.goto(BASE + "/new/add-vrf", { waitUntil: "domcontentloaded" }); await page.waitForSelector("#f-vrf"); await ready(); await hold(0.6);
  await type("#f-vrf", "pay");
  if (!(await page.$eval("#f-first_vlan", (e) => e.checked))) await click("#f-first_vlan");
  await highlight(`xpath/(//label[@for='f-vrf']/parent::div)[1]`);
  await caption("Add a VRF", "A new <b>tier zone</b>, like web, app and db: you name it — <b>pay</b>. Its L3VNI, transit VLAN and subnet to the firewalls are allocated.");
  await hold(6); await highlight(null);
  await highlight(`xpath/(//input[@id='f-first_vlan']/ancestor::div[contains(@class,'field')][1])[1]`);
  await caption("Add a VRF", "With its <b>first VLAN</b> in the same request, the zone is usable at once. The firewall zone <b>denies by default</b>, except the standard ICMP rules.");
  await hold(6); await highlight(null);
  await type("#f-reason", "A zone for the payments service");
  const p2 = await submitApprove("vrf request");
  await waitDone(p2, {
    "Pre-flight checks": "<b>Pre-flight</b>: the name, and every allocated number still free.",
    "phpIPAM reservation": "<b>phpIPAM</b>: VRF pay, its VLANs, the transit /28 with the borders' and firewalls' addresses — and the first VLAN's /24.",
    "Nautobot": "<b>Nautobot</b>: tenant, route target, VRF, VLANs and prefixes; the VRF and its transit in the evpn-3tier plan.",
    "Standard firewall rules": "<b>Standard Firewall Rules</b>: ICMP from pay to every VLAN, and to pay's VLAN from every zone.",
    "Switches pipeline": "<b>evpn-3tier-switches</b>: the VRF and its L3VNI on the borders and every leaf — the new ones too.",
    "Firewalls pipeline": "<b>evpn-3tier-firewalls</b>: the transit VLAN, eBGP to both borders, zone PAY — then the whole lab verified.",
    "Device checks": "<b>Checks</b>: the VRF on every switch, the firewall sessions, zone PAY, the default route, the first VLAN.",
    "Ping test from the WAN": "<b>Ping test from the WAN</b>: every server, and pay's new gateway.",
  });
  await scrollTo(0); await hold(1.2);
  await showEvent("on the devices: VRF pay", "Done", "VRF <b>pay</b> on both borders and all six leaves; both firewalls peer with the borders in it; zone <b>PAY</b> on both.", 6);
  await showEvent("Standard Firewall Rules: 10", "Done", "Deny by default — only the standard ICMP rules, from pay to every other VLAN.", 4.5);
  await page.goto(BASE + "/tools/fabric", { waitUntil: "domcontentloaded" }); await page.waitForSelector(Tc("VRFs")); await ready(); await hold(0.8);
  await highlight(Tc("VRFs"));
  await caption("Fabric Dashboard", "Five VRFs: web, app, db, outside — and <b>pay</b>.");
  await hold(5); await highlight(null);
  // ---- delete refused while in use
  await page.goto(BASE + "/new/delete-vrf", { waitUntil: "domcontentloaded" }); await page.waitForSelector("#f-vrf"); await ready(); await hold(0.5);
  await click("#f-vrf"); await page.select("#f-vrf", "pay"); await hold(0.4);
  await type("#f-confirm", "pay"); await type("#f-reason", "No longer needed");
  await click(byText("button", "Submit for approval")); await hold(1.5);
  const err = `xpath/(//*[contains(., 'is in use')][not(*[contains(., 'is in use')])])[1]`;
  if (await page.$(err)) await highlight(err);
  await caption("Delete a VRF", "Deleting works the same way in reverse — and is <b>refused</b> while the VRF holds anything: here its VLAN. Remove a leaf pair is refused while a server or a provisioned port uses it.");
  await hold(7); await highlight(null);
'''
open("record6.js", "w").write(head + seg + tail)
print("written", len(seg))
