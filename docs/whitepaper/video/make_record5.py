"""record5.js = record2.js's set-up + the port profiles and owners segment + the closing card; frames to part5/, timings
to timing5.json. Live: a profile created (#N), leaf2 eth7 provisioned on it for APP-1042 (#N+1); cleaned up off camera."""
s = open("record2.js").read()
head = s[: s.index("  await startRec();") + len("  await startRec();\n")]
tail = s[s.index('  await caption(null);\n  await card(`<div class="brand">'):]
head = head.replace('const OUT = path.join(__dirname, "part2");', 'const OUT = path.join(__dirname, "part5");')
tail = tail.replace('"timing2.json"', '"timing5.json"')
seg = r'''
  // ---- port profiles and owners ------------------------------------------------------------------------------------------------
  const Tc = (t) => `xpath/(//section[contains(@class,'card')][.//h2[normalize-space(.)=${JSON.stringify(t)}]])[1]`;
  const waitDone = async (id, CAP) => {
    speed = 10; await badge("⏩ 10× speed");
    let last = "", r; const t0 = Date.now();
    while (Date.now() - t0 < 15 * 60 * 1000) {
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
    await page.waitForFunction(() => /\/requests\/\d+$/.test(location.pathname), { timeout: 20000 });
    const id = Number((await page.url()).match(/requests\/(\d+)/)[1]); log(who, id);
    await ready(); await hold(1);
    await caption("Approval", `Request <b>#${id}</b>: the operator approves it.`);
    await click(byText("button", "Approve")); await hold(0.8);
    await click(`xpath/(//footer//button[contains(normalize-space(.), "Approve")])[last()]`); await hold(0.8);
    return id;
  };
  await caption(null);
  await card(`<div class="n">NEW</div><h1>Port profiles and owners</h1>
    <p>Named presets for spare ports, an owner for every provisioned port — an application and its team, kept in Nautobot — and one action to put a port back to its default.</p>`);
  await hold(4.5);
  // applications
  await page.goto(BASE + "/tools/applications", { waitUntil: "domcontentloaded" }); await page.waitForSelector(Tc("Applications")); await ready(); await card(null);
  await highlight(Tc("Applications"));
  await caption("Applications", "Owners are <b>applications</b>: an ID (APP-…), a name and an <b>owner team</b> — Nautobot tenants, with the team linked as Owner. Operators add them here.");
  await hold(6.5); await highlight(null);
  // a profile
  await page.goto(BASE + "/tools/port-profiles", { waitUntil: "domcontentloaded" }); await page.waitForSelector(Tc("Profiles")); await ready();
  await caption("Port Profiles", "<b>Port profiles</b>: named presets for spare ports. A new one is a request, like every change.");
  await hold(3);
  await click(`xpath/(//a[contains(@href,'/new/port-profile?action=create')])[1]`);
  await page.waitForSelector("#f-name"); await ready(); await hold(0.6);
  await type("#f-name", "backup-trunk");
  await type("#f-description", "backup appliances: the web VLANs");
  await click("#f-mode"); await page.select("#f-mode", "tagged"); await hold(0.4);
  for (const label of ["41 ", "47 "]) await click(`xpath/(//div[contains(@class,'multi')]//button[starts-with(normalize-space(.), ${JSON.stringify(label)})])[1]`);
  await click("#f-native_vlan"); await page.select("#f-native_vlan", "43"); await hold(0.5);
  await caption("Port profile", "<b>backup-trunk</b>: a trunk carrying VLANs 41 and 47 tagged, and 43 untagged (native).");
  await hold(3.5);
  await type("#f-reason", "A preset for backup appliances");
  const p1 = await submitApprove("profile request");
  await waitDone(p1, { "Nautobot": "<b>Nautobot</b>: the profile joins the evpn-3tier plan." });
  await caption("Done", `Request <b>#${p1}</b>: the profile exists. No port uses it yet, so nothing else had to change.`);
  await hold(3.5);
  // a port on the profile, with an owner
  await page.goto(BASE + "/new/port", { waitUntil: "domcontentloaded" }); await page.waitForSelector("#f-port"); await ready(); await hold(0.5);
  await click("#f-port"); await page.select("#f-port", "leaf2|eth7"); await hold(0.4);
  await click("#f-state"); await page.select("#f-state", "enabled"); await hold(0.4);
  await click("#f-profile"); await page.select("#f-profile", "backup-trunk"); await hold(0.5);
  await caption("Port Provisioning", "<b>leaf2 eth7</b> on the <b>backup-trunk</b> profile: the profile sets the mode and the VLANs.");
  await hold(3.5);
  await click("#f-application"); await page.select("#f-application", "APP-1042"); await hold(0.4);
  await type("#f-description", "backup appliance");
  await highlight(`xpath/(//label[@for='f-application']/parent::div)[1]`);
  await caption("Port Provisioning", "Every enabled spare port needs an <b>owner</b>: here <b>APP-1042 Backup service</b>, owned by the Storage team.");
  await hold(4.5); await highlight(null);
  await type("#f-reason", "The backup appliance for the shop");
  const p2 = await submitApprove("port request");
  await waitDone(p2, {
    "Pre-flight checks": "<b>Pre-flight</b>: a spare port on a leaf — not an uplink.",
    "Nautobot": "<b>Nautobot</b>: the port's VLANs from the profile, its profile name, and its <b>application</b> (a Nautobot relationship).",
    "Switches pipeline": "<b>evpn-3tier-switches</b>: native-vlan 43, allowed-vlan 41 and 47, the owner in the port's description — then the whole lab verified.",
    "Port checks": "<b>Checks on leaf2</b>: the port's state and its bridge VLANs.",
    "Ping test from the WAN": "<b>Ping test from the WAN</b>: every server still answers.",
  });
  await scrollTo(0); await hold(1.2);
  const ow = `xpath/(//div[contains(@class,'ev-m')][contains(., 'owner APP-1042')])[1]`;
  const ol = `xpath/(//div[contains(@class,'ev-m')][contains(., 'on the leaf')])[1]`;
  if (await page.$(ol)) {
    await highlight(ol); await moveTo(ol, 0.1, 0.5);
    await caption("Done", "On leaf2: <b>41 and 47 tagged, 43 untagged</b> — exactly the profile.");
    await hold(5); await highlight(null);
  }
  if (await page.$(ow)) {
    await highlight(ow); await moveTo(ow, 0.1, 0.5);
    await caption("Done", "Owned by <b>APP-1042 Backup service</b> (Storage team); the switch's description says so: 'APP-1042 Backup service — backup appliance'.");
    await hold(5.5); await highlight(null);
  }
  // where it shows
  await page.goto(BASE + "/tools/port-profiles", { waitUntil: "domcontentloaded" }); await page.waitForSelector(Tc("Profiles")); await ready(); await hold(0.6);
  const prow = `xpath/(//section[contains(@class,'card')]//tbody/tr[td[1][contains(., 'backup-trunk')]])[1]`;
  if (await page.$(prow)) await highlight(prow);
  await caption("Port Profiles", "The profile now lists the port using it. <b>Change</b> re-applies a new version to every such port in one request; one in use cannot be deleted.");
  await hold(6); await highlight(null);
  await page.goto(BASE + "/tools/applications", { waitUntil: "domcontentloaded" }); await page.waitForSelector(Tc("Applications")); await ready(); await hold(0.6);
  const arow = `xpath/(//section[contains(@class,'card')]//tbody/tr[td[1][contains(., 'APP-1042')]])[1]`;
  if (await page.$(arow)) await highlight(arow);
  await caption("Applications", "And the application lists the ports it owns: <b>APP-1042</b> owns leaf2 eth7.");
  await hold(5); await highlight(null);
  // default
  await page.goto(BASE + "/new/port", { waitUntil: "domcontentloaded" }); await page.waitForSelector("#f-port"); await ready(); await hold(0.5);
  await click("#f-port"); await page.select("#f-port", "leaf2|eth7"); await hold(0.4);
  await click("#f-state"); await page.select("#f-state", "default"); await hold(0.6);
  await highlight(`xpath/(//label[@for='f-state']/parent::div)[1]`);
  await caption("Default", "And when the appliance goes: <b>Default</b> puts the port back to disabled, with no VLANs, profile, owner or description — in one request.");
  await hold(6.5); await highlight(null);
'''
open("record5.js", "w").write(head + seg + tail)
print("written", len(seg))
