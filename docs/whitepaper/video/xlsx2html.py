"""A spreadsheet-style HTML preview of a workbook's first sheet (grid, column letters, row numbers, sheet tabs):
python xlsx2html.py in.xlsx out.html"""
import html, sys
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
wb = load_workbook(sys.argv[1]); ws = wb.worksheets[0]; E = html.escape
cols = min(ws.max_column, 9); rows = min(ws.max_row, 16)
widths = [max(60, min(260, int((ws.column_dimensions[get_column_letter(c)].width or 10) * 7.2))) for c in range(1, cols + 1)]
head = "".join(f'<th style="width:{w}px">{get_column_letter(i)}</th>' for i, w in enumerate(widths, 1))
body = ""
for r in range(1, rows + 1):
    cells = ""
    for c in range(1, cols + 1):
        v = ws.cell(r, c).value; v = "" if v is None else v
        cls = "h" if r == 1 else ("n" if isinstance(v, (int, float)) else "")
        cells += f'<td class="{cls}">{E(str(v))}</td>'
    body += f"<tr><th class='rn'>{r}</th>{cells}</tr>"
tabs = "".join(f'<span class="tab {"on" if i == 0 else ""}">{E(s)}</span>' for i, s in enumerate(wb.sheetnames))
name = E(sys.argv[1].rsplit("/", 1)[-1])
open(sys.argv[2], "w").write(f"""<!doctype html><meta charset="utf-8"><title>{name}</title><style>
body{{margin:0;font:13px Inter,Ubuntu,'Noto Sans',sans-serif;background:#eef0f4;color:#1f2937}}
.bar{{background:#1f7a4d;color:#fff;padding:12px 20px;font-weight:600;display:flex;gap:14px;align-items:center}}
.bar small{{opacity:.8;font-weight:400}}
.fx{{background:#fff;border-bottom:1px solid #cfd5df;padding:6px 20px;font-family:monospace;color:#555}}
.wrap{{padding:0 0 0 0;overflow:hidden;background:#fff}}
table{{border-collapse:collapse;table-layout:fixed}} th,td{{border:1px solid #d6dbe4;padding:4px 7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;height:22px}}
thead th{{background:#f1f3f7;color:#666;font-weight:500;text-align:center}} th.rn{{background:#f1f3f7;color:#666;width:34px;font-weight:500}}
td.h{{background:#4f46e5;color:#fff;font-weight:700}} td.n{{text-align:right}}
.tabs{{position:fixed;bottom:0;left:0;right:0;background:#f1f3f7;border-top:1px solid #cfd5df;padding:6px 20px;display:flex;gap:4px}}
.tab{{padding:5px 14px;border:1px solid #cfd5df;border-top:0;background:#e6e9ef;color:#444}} .tab.on{{background:#fff;color:#1f7a4d;font-weight:600}}
</style><div class="bar">{name} <small>downloaded from Fabric Factory · {len(wb.sheetnames)} sheets · {ws.max_row - 1} rows on "{E(ws.title)}"</small></div>
<div class="fx">A1 &nbsp; {E(str(ws['A1'].value))}</div><div class="wrap"><table><thead><tr><th class="rn"></th>{head}</tr></thead><tbody>{body}</tbody></table></div>
<div class="tabs">{tabs}</div>""")
