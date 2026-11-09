// Renders deck.rec.json to preview.html (13.333in x 7.5in pages, Arial ≈ Liberation Sans) for visual QA.
const fs = require("fs");
const path = require("path");
const { HEX, NAVY } = require("./kit");
const rec = JSON.parse(fs.readFileSync(process.argv[2]));
const IMG = path.join(__dirname, "img");
const col = (c) => "#" + (HEX[c] || c || "000000");
const rgba = (c, tr) => { const h = HEX[c] || c; const n = parseInt(h, 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${1 - (tr || 0) / 100})`; };
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
const pos = (o) => `left:${o.x}in;top:${o.y}in;width:${o.w}in;height:${o.h}in;`;
const L = 0.6, CW = 13.333 - 1.2;
const PH = {
  Content: { title: { x: L, y: 0.38, w: CW, h: 0.72, fontSize: 32, bold: true, color: "tx1", valign: "middle", margin: 0 },
    lead: { x: L, y: 1.1, w: CW, h: 0.5, fontSize: 15, color: "tx2", valign: "top", margin: 0 } },
  Section: { title: { x: L + 0.2, y: 3.0, w: CW - 0.4, h: 1.0, fontSize: 40, bold: true, color: "bg1", valign: "top", margin: 0 },
    sub: { x: L + 0.2, y: 4.05, w: 9.5, h: 1.2, fontSize: 18, color: "bg2", valign: "top", margin: 0 } },
};
function textBox(t, o) {
  const m = Array.isArray(o.margin) ? o.margin : [o.margin ?? 3.6, o.margin ?? 7.2, o.margin ?? 3.6, o.margin ?? 7.2];
  const runs = Array.isArray(t) ? t : [{ text: t, options: {} }];
  const paras = [[]];
  runs.forEach((r) => { paras[paras.length - 1].push(r); if (r.options && r.options.breakLine) paras.push([]); });
  const va = { top: "flex-start", middle: "center", bottom: "flex-end" }[o.valign || "top"];
  const html = paras.filter((p) => p.length).map((p) => {
    const after = Math.max(...p.map((r) => (r.options && r.options.paraSpaceAfter) || o.paraSpaceAfter || 0));
    return `<p style="margin:0 0 ${after}pt;text-align:${o.align || "left"}">` + p.map((r) => {
      const ro = { ...o, ...(r.options || {}) };
      return `<span style="font-size:${ro.fontSize || 18}pt;font-weight:${ro.bold ? 700 : 400};color:${col(ro.color)};font-family:${ro.fontFace === "Courier New" ? "'Liberation Mono'" : "'Liberation Sans'"};letter-spacing:${ro.charSpacing ? ro.charSpacing + "pt" : "normal"}">${esc(r.text).replace(/\n/g, "<br>")}</span>`;
    }).join("") + "</p>";
  }).join("");
  return `<div class="t" style="${pos(o)}padding:${m[0]}pt ${m[1]}pt ${m[2]}pt ${m[3]}pt;justify-content:${va}">${html}</div>`;
}
let out = `<!doctype html><html><head><style>@page{size:13.333in 7.5in;margin:0}body{margin:0}
.s{position:relative;width:13.333in;height:7.5in;overflow:hidden;break-after:page;font-family:'Liberation Sans'}
.s>*{position:absolute;box-sizing:border-box}.t{display:flex;flex-direction:column;line-height:1.17;white-space:pre-wrap;outline:1px dashed rgba(255,0,0,.0)}
table{border-collapse:collapse;table-layout:fixed;line-height:1.17}td{border:0.75pt solid #D6DBE4;overflow:hidden}</style></head><body>`;
rec.slides.forEach((sl, i) => {
  const bg = sl.layout === "Content" ? "#fff" : "#" + NAVY;
  out += `<div class="s" style="background:${bg}">`;
  if (sl.layout === "Content") {
    out += `<img src="${IMG}/logo.png" style="left:${L}in;top:7in;width:.24in;height:.24in">`;
    out += textBox("Fabric Factory · evpn-3tier lab", { x: L + 0.34, y: 7, w: 5, h: 0.24, fontSize: 10, color: "tx2", margin: 0, valign: "middle" });
  }
  if (sl.layout !== "Cover") out += textBox(String(i + 1), { x: 13.333 - 1.2, y: 7, w: 0.6, h: 0.24, fontSize: 10, color: sl.layout === "Content" ? "tx2" : "bg2", margin: 0, align: "right" });
  for (const e of sl.els) {
    const o = e.o;
    if (e.k === "ph") out += textBox(e.t, { ...PH[sl.layout][o.placeholder] });
    else if (e.k === "text") out += textBox(e.t, o);
    else if (e.k === "shape") {
      const fill = o.fill ? rgba(o.fill.color, o.fill.transparency) : "transparent";
      const line = o.line && o.line.type !== "none" ? `border:${o.line.width || 0.75}pt solid ${col(o.line.color)};` : "";
      const rad = e.type === "ellipse" ? "50%" : e.type === "roundRect" ? `${(o.rectRadius || 0) * Math.min(o.w, o.h)}in` : 0;
      out += `<div style="${pos(o)}background:${fill};${line}border-radius:${rad}"></div>`;
    } else if (e.k === "image") {
      const src = o.data ? "data:" + o.data : o.path;
      out += `<img src="${src}" style="${pos(o)}">`;
    } else if (e.k === "table") {
      const m = o.margin || [3, 5, 3, 5];
      out += `<table style="left:${o.x}in;top:${o.y}in;width:${o.w}in"><colgroup>${o.colW.map((w) => `<col style="width:${w}in">`).join("")}</colgroup>`;
      for (const row of e.rows) {
        out += `<tr style="height:${o.rowH || 0}in">`;
        for (const c of row) {
          const t = typeof c === "string" ? c : c.text, co = { ...o, ...(typeof c === "string" ? {} : c.options) };
          out += `<td style="padding:${m[0]}pt ${m[1]}pt ${m[2]}pt ${m[3]}pt;font-size:${co.fontSize}pt;font-weight:${co.bold ? 700 : 400};color:${col(co.color)};background:${co.fill ? col(co.fill.color) : "transparent"};vertical-align:${co.valign || "middle"}">${esc(t).replace(/\n/g, "<br>")}</td>`;
        }
        out += "</tr>";
      }
      out += "</table>";
    } else if (e.k === "chart") {
      const d = e.data[0], max = o.valAxisMaxVal || Math.max(...d.values);
      const rh = (o.h - 0.6) / d.labels.length;
      out += `<div style="${pos(o)}border:1px dotted #ccc"><div style="position:absolute;top:.1in;width:100%;text-align:center;font-size:${o.titleFontSize}pt;font-weight:700">${esc(o.title)}</div>`;
      d.labels.forEach((l, j) => {
        const y = 0.5 + j * rh;
        out += `<div style="position:absolute;left:.1in;top:${y + rh * 0.2}in;font-size:${o.catAxisLabelFontSize}pt;color:#4B5563">${l}</div>`;
        out += `<div style="position:absolute;left:1in;top:${y + rh * 0.2}in;height:${rh * 0.6}in;width:${(d.values[j] / max) * (o.w - 1.6)}in;background:#4F46E5"></div>`;
        out += `<div style="position:absolute;left:${1.05 + (d.values[j] / max) * (o.w - 1.6)}in;top:${y + rh * 0.2}in;font-size:${o.dataLabelFontSize}pt">${d.values[j]}</div>`;
      });
      out += "</div>";
    }
  }
  out += "</div>";
});
out += `<script>document.querySelectorAll('.t').forEach(e=>{if(e.scrollHeight>e.clientHeight+1||e.scrollWidth>e.clientWidth+1){e.style.outline='3px solid red'}});
document.querySelectorAll('.s').forEach(s=>{const b=s.getBoundingClientRect();s.querySelectorAll('table').forEach(t=>{const r=t.getBoundingClientRect();if(r.bottom-b.top>6.9*96){t.style.outline='3px solid red'}})});</script>`;
fs.writeFileSync(process.argv[3], out + "</body></html>");
