import re, subprocess, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import build as m
out = pathlib.Path(sys.argv[1])
CROP = 50
for name in ["d_platform", "d_topology", "d_evpn", "d_policy", "d_request", "d_orchestration", "d_pipeline", "d_sequence", "d_states"]:
    s = getattr(m, name)()
    w, h = map(int, re.search(r'viewBox="0 0 (\d+) (\d+)"', s).groups())
    s = s.replace(f'viewBox="0 0 {w} {h}"', f'viewBox="-6 {CROP} {w+12} {h-CROP+6}" width="{w+12}" height="{h-CROP+6}"', 1)
    page = out / f"{name}.html"
    page.write_text(f'<!doctype html><html><body style="margin:0;background:#fff">{s}</body></html>')
    png = out / f"{name[2:]}.png"
    subprocess.run(["google-chrome", "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars", "--force-device-scale-factor=3",
                    f"--window-size={w+12},{h-CROP+6}", f"--screenshot={png}", page.as_uri()], check=True, capture_output=True, timeout=60)
    print(png.name, w+12, h-CROP+6)
logo = m.logo(256)
(out / "logo.html").write_text(f'<!doctype html><html><body style="margin:0;background:transparent">{logo}</body></html>')
subprocess.run(["google-chrome", "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars", "--default-background-color=00000000",
                "--window-size=256,256", f"--screenshot={out/'logo.png'}", (out/'logo.html').as_uri()], check=True, capture_output=True, timeout=60)
