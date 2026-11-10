"""The demo video: part1 (the tour and the live Add a VLAN, timed from frame mtimes) + part2 (proof, the dashboard,
Nautobot, the change workflows) + part3 (the ASN and VLAN databases) + part4 (Port Provisioning) + part5 (port profiles, owners, Default) — each later part
opens on its own title card, and every part but the last is cut before its closing card (the last one keeps it)."""
import bisect, datetime, json, os, subprocess
import imageio_ffmpeg
from PIL import Image

here = os.path.dirname(os.path.abspath(__file__)); os.chdir(here)
day = datetime.date.today()
T = lambda s: datetime.datetime.combine(day, datetime.time.fromisoformat(s)).timestamp()   # noqa: E731
lum = lambda p: sum(Image.open(p).convert("L").resize((32, 18)).getdata()) / (32 * 18)       # noqa: E731
dark = lambda p: lum(p) < 40                                                                # noqa: E731

# ---- part1: frames by mtime; a 10x window and the held moments as in assemble2.py ---------------------------------------
f1 = sorted(os.listdir("part1"))
m = [os.stat("part1/" + f).st_mtime_ns / 1e9 for f in f1]
CUT, W0, W1 = T("06:52:25.6"), T("06:44:33.9"), T("06:52:07.0")
HOLDS = [T(x) for x in ("06:44:34.6", "06:44:37.6", "06:49:08.6", "06:51:44.6", "06:52:02.6")]
n1 = bisect.bisect_left(m, CUT)
entries = []
for i in range(n1 - 1):
    a, b = m[i], m[i + 1]
    inside = max(0, min(b, W1) - max(a, W0))
    d = (b - a - inside) + inside / 10
    if any(a <= h < b for h in HOLDS): d += 3.5
    entries.append(("part1/" + f1[i], d))
entries.append(("part1/" + f1[n1 - 1], 0.2))


def part(name, timing, first, last):
    t = json.load(open(timing))["times"]; f = sorted(os.listdir(name))
    start = 0
    if not first:                                   # open on the part's title card
        while start < len(f) and lum(f"{name}/{f[start]}") > 26.5: start += 1      # fully faded in (the card is ~25)
    end = len(f)
    if not last:                                    # cut before the closing card and its fade-in; keep the hold before it
        while end > start and dark(f"{name}/{f[end - 1]}"): end -= 1
        while end > start + 1 and t[end] - t[end - 1] < 0.5: end -= 1
    print(f"{name}: frames {start}..{end} of {len(f)} (t {t[start]:.1f}-{t[end] if end < len(t) else t[-1]:.1f} s)")
    for i in range(start, end):
        d = (t[i + 1] - t[i]) if i + 1 < len(t) else 0.5
        entries.append((f"{name}/{f[i]}", max(d, 0.001)))


part("part2", "timing2.json", True, False)
part("part3", "timing3.json", False, False)
part("part4", "timing4.json", False, False)
part("part5", "timing5.json", False, True)
with open("list.txt", "w") as f:
    for p, d in entries: f.write(f"file '{p}'\nduration {d:.4f}\n")
    f.write(f"file '{entries[-1][0]}'\n")
print("total", round(sum(d for _, d in entries), 1), "s;", len(entries), "frames")
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "list.txt",
                "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-movflags", "+faststart",
                "fabric-factory-demo.mp4"], check=True)
print(os.path.getsize("fabric-factory-demo.mp4"))
