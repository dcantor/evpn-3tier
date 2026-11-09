"""record4.js = record2.js's set-up + the Port Provisioning segment (an uplink refused; a spare port made a trunk, live)
+ the closing card; frames to part4/, timings to timing4.json. assemble4.py splices it in before the closing card."""
s = open("record2.js").read()
head = s[: s.index("  await startRec();") + len("  await startRec();\n")]
tail = s[s.index('  await caption(null);\n  await card(`<div class="brand">'):]
head = head.replace('const OUT = path.join(__dirname, "part2");', 'const OUT = path.join(__dirname, "part4");')
tail = tail.replace('"timing2.json"', '"timing4.json"')
seg = r'''
  // ---- Port Provisioning ---------------------------------------------------------------------------------------------------
  await caption(null);
  await card(`<div class="n">NEW</div><h1>Port Provisioning</h1>
    <p>Enable or disable a leaf port, and set a spare port up as an access port or a trunk — with the uplinks to the spines blocked.</p>`);
  await hold(4.5);
  await page.goto(BASE + "/new", { waitUntil: "domcontentloaded" }); await ready(); await card(null);
  const wfC = `xpath/(//section[contains(@class,'card')][.//h3[normalize-space(.)='Port Provisioning']])[1]`;
  await page.waitForSelector(wfC); await scrollToEl(wfC, 140);
  await highlight(wfC); await moveTo(wfC, 0.5, 0.2);
  await caption("New request", "<b>Port Provisioning</b>: a change request like the others, approved by an operator and built by the switches pipeline.");
  await hold(4.5); await highlight(null);
  await click(`xpath/(//a[@href='/new/port'])[1]`);
  await page.waitForSelector("#f-port"); await ready(); await hold(0.6);
  await caption("Port Provisioning", "Every leaf port is listed: the uplinks to the spines as <b>blocked</b>, the servers' bond legs, and two <b>spare ports</b> per leaf (eth6, eth7).");
  await moveTo("#f-port"); await hold(4.5);
  // an uplink: refused
  await click("#f-port"); await page.select("#f-port", "leaf1|eth1"); await hold(0.6);
  await click("#f-state"); await page.select("#f-state", "disabled"); await hold(0.5);
  await type("#f-reason", "Maintenance on the uplink");
  await caption("Port Provisioning", "Try to shut <b>leaf1 eth1</b>, an uplink to spine1…");
  await hold(1.5);
  await click(byText("button", "Submit for approval"));
  await page.waitForSelector(".note.bad", { timeout: 15000 }); await hold(0.5);
  await scrollToEl(".note.bad", 140); await highlight(".note.bad");
  await caption("Blocked", "…refused before it reaches anyone: <b>uplinks to the spines carry the whole fabric</b> and cannot be changed here. The worker checks it again.");
  await hold(6.5); await highlight(null);
  // a spare port as a trunk, live
  await click("#f-port"); await page.select("#f-port", "leaf1|eth6"); await hold(0.5);
  await click("#f-state"); await page.select("#f-state", "enabled"); await hold(0.4);
  await click("#f-mode"); await page.select("#f-mode", "tagged"); await hold(0.5);
  await caption("Port Provisioning", "Now a spare port: <b>leaf1 eth6</b>, enabled, as a <b>trunk</b> — pick the VLANs it carries, tagged. Only VLANs the leaves carry are offered.");
  for (const label of ["41 ", "47 "]) await click(`xpath/(//div[contains(@class,'multi')]//button[starts-with(normalize-space(.), ${JSON.stringify(label)})])[1]`);
  await hold(1);
  await highlight(`xpath/(//div[contains(@class,'multi')])[1]`); await hold(3.5); await highlight(null);
  await type("#f-description", "backup appliance");
  await ev(() => { const t = document.querySelector("#f-reason"); t.focus(); t.select(); });
  await page.keyboard.press("Backspace");
  await type("#f-reason", "A backup appliance on leaf1 needs the web VLANs 41 and 47");
  await click(byText("button", "Submit for approval"));
  await page.waitForFunction(() => /\/requests\/\d+$/.test(location.pathname), { timeout: 20000 });
  const pid = Number((await page.url()).match(/requests\/(\d+)/)[1]); log("port request", pid);
  await ready(); await hold(1.5);
  await caption("Approval", `Request <b>#${pid}</b>: the operator approves it.`);
  await click(byText("button", "Approve")); await hold(1);
  await click(`xpath/(//footer//button[contains(normalize-space(.), "Approve")])[last()]`); await hold(1);
  const PCAP = {
    "Pre-flight checks": "<b>Pre-flight</b>: a leaf port, not an uplink; for a server's leg, its other leg must be up.",
    "Nautobot": "<b>Nautobot</b>: the port's native interface fields — enabled, mode tagged, VLANs 41 and 47, the description.",
    "Switches pipeline": "<b>evpn-3tier-switches</b>: renders the port's bridge membership from Nautobot, applies it to leaf1, then verifies the whole lab.",
    "Port checks": "<b>Checks on leaf1</b>: the port's admin state from VyOS, and its VLANs straight from the kernel bridge.",
    "Ping test from the WAN": "<b>Ping test from the WAN</b>: every web, app and db server still answers.",
  };
  speed = 10; await badge("⏩ 10× speed");
  let lastS = "", pr;
  const tp0 = Date.now();
  while (Date.now() - tp0 < 15 * 60 * 1000) {
    pr = await api(`/api/requests/${pid}`);
    if (["done", "failed"].includes(pr.status)) break;
    const st = pr.stage || "";
    if (st && st !== lastS) { lastS = st; await caption(`Running · ${st} · ${pr.percent ?? 0}%`, PCAP[st] || st); }
    await sleep(2000);
  }
  speed = 1; await badge(null); log("port request", pr.status);
  await scrollTo(0); await hold(1.5);
  const ol = `xpath/(//div[contains(@class,'ev-m')][contains(., 'on the leaf')])[1]`;
  if (await page.$(ol)) {
    await highlight(ol); await moveTo(ol, 0.1, 0.5);
    await caption("Done", `Request <b>#${pid}</b> is done: leaf1 eth6 is up and its bridge carries exactly <b>VLANs 41 and 47, tagged</b> — and every server still answers.`);
    await hold(7); await highlight(null);
  }
'''
open("record4.js", "w").write(head + seg + tail)
print("written", len(seg))
