// Thin layer over pptxgenjs: every element is added to the deck AND recorded, so preview.html can be rendered in Chrome
// (Liberation Sans is metric-compatible with Arial) for visual QA without LibreOffice.
const pptxgen = require("pptxgenjs");
const fs = require("fs");
const path = require("path");
const React = require("react");
const RDS = require("react-dom/server");
const sharp = require("sharp");
const Lu = require("react-icons/lu");

const THEME = {
  name: "Fabric Factory",
  headFontFace: "Arial",
  bodyFontFace: "Arial",
  colors: {
    dk1: "111827", lt1: "FFFFFF", dk2: "4B5563", lt2: "F1F3F8",
    accent1: "4F46E5", accent2: "2563EB", accent3: "059669", accent4: "C2410C", accent5: "7C3AED", accent6: "B91C1C",
    hlink: "4F46E5", folHlink: "7C3AED",
  },
};
const HEX = { tx1: THEME.colors.dk1, bg1: THEME.colors.lt1, tx2: THEME.colors.dk2, bg2: THEME.colors.lt2,
  accent1: THEME.colors.accent1, accent2: THEME.colors.accent2, accent3: THEME.colors.accent3,
  accent4: THEME.colors.accent4, accent5: THEME.colors.accent5, accent6: THEME.colors.accent6 };
const NAVY = "0F1330"; // dark slide background (hex only used for background fill of dark layouts)

function makeDeck() {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE"; // 13.333 x 7.5
  pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
  pres.title = "Fabric Factory and the evpn-3tier Lab";
  pres.subject = "Architecture, tech stack, automation and orchestration";
  pres.author = "evpn-3tier";
  pres.company = "evpn-3tier lab";
  const C = pres.SchemeColor;
  const rec = { layouts: {}, slides: [] };
  return { pres, C, rec };
}

// ---- recording helpers ---------------------------------------------------------------------------------------------------
const hexOf = (c) => (c && HEX[c]) || c || "000000";

async function iconPng(name, color, size = 256) {
  const Comp = Lu[name];
  if (!Comp) throw new Error("no icon " + name);
  const svg = RDS.renderToStaticMarkup(React.createElement(Comp, { color: "#" + color, size: String(size) }));
  const buf = await sharp(Buffer.from(svg)).png().toBuffer();
  return "image/png;base64," + buf.toString("base64");
}

const cp = (x) => JSON.parse(JSON.stringify(x));
function wrap(slide, store) {
  const els = store;
  return {
    raw: slide,
    text(t, o) { els.push({ k: "text", t: cp(t), o: cp(o) }); slide.addText(t, { isTextBox: true, ...o }); },
    ph(t, o) { els.push({ k: "ph", t: cp(t), o: cp(o) }); slide.addText(t, o); },
    shape(type, o) { els.push({ k: "shape", type, o: cp(o) }); slide.addShape(type, o); },
    image(o) { els.push({ k: "image", o: { ...o } }); slide.addImage(o); },
    table(rows, o) { els.push({ k: "table", rows: cp(rows), o: cp(o) }); slide.addTable(rows, o); },
    chart(type, data, o) { els.push({ k: "chart", o: cp(o), data: cp(data) }); slide.addChart(type, data, o); },
    notes(s) { slide.addNotes(s); },
  };
}

module.exports = { makeDeck, wrap, iconPng, THEME, HEX, NAVY, hexOf };
