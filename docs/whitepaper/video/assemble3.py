import json, os, subprocess, datetime, bisect
import imageio_ffmpeg
here = os.path.dirname(os.path.abspath(__file__)); os.chdir(here)
day = datetime.date.today()
T = lambda s: datetime.datetime.combine(day, datetime.time.fromisoformat(s)).timestamp()
f1 = sorted(os.listdir("part1"))
m = [os.stat("part1/" + f).st_mtime_ns / 1e9 for f in f1]
CUT, W0, W1 = T("06:52:25.6"), T("06:44:33.9"), T("06:52:07.0")
HOLDS = [T(x) for x in ("06:44:34.6", "06:44:37.6", "06:49:08.6", "06:51:44.6", "06:52:02.6")]
n1 = bisect.bisect_left(m, CUT)
entries = []
for i in range(n1 - 1):
    a, b = m[i], m[i + 1]
    # real time spent in/out of the 10x window
    inside = max(0, min(b, W1) - max(a, W0))
    d = (b - a - inside) + inside / 10
    if any(a <= h < b for h in HOLDS): d += 3.5
    entries.append(("part1/" + f1[i], d))
entries.append(("part1/" + f1[n1 - 1], 0.2))
from PIL import Image
t2 = json.load(open("timing2.json"))["times"]
f2 = sorted(os.listdir("part2"))
# part2 ends with the closing card (dark navy, full screen): cut before it — part3 brings it back after the new segment
dark = lambda p: sum(Image.open(p).convert("L").resize((32, 18)).getdata()) / (32 * 18) < 40   # noqa: E731
cut = len(f2)
while cut > 0 and dark("part2/" + f2[cut - 1]): cut -= 1
# back through the fade-in (short frames) to the last scene's hold frame (a long one): keep the hold, drop the fade
while cut > 1 and t2[cut] - t2[cut - 1] < 0.5: cut -= 1
print("part2: closing card from frame", cut, "of", len(f2), f"(t={t2[cut]:.1f}s)")
for i in range(cut):
    d = t2[i + 1] - t2[i]                     # the last kept frame keeps its own duration (it may be a hold)
    entries.append(("part2/" + f2[i], max(d, 0.001)))
t3 = json.load(open("timing3.json"))["times"]
f3 = sorted(os.listdir("part3"))
start = 0                                  # part3 opens on its title card: skip the page it faded in from
while start < len(f3) and not dark("part3/" + f3[start]): start += 1
print("part3: from frame", start, f"(t={t3[start]:.1f}s)")
for i in range(start, len(f3)):
    d = (t3[i + 1] - t3[i]) if i + 1 < len(t3) else 0.5
    entries.append(("part3/" + f3[i], max(d, 0.001)))
with open("list.txt", "w") as f:
    for p, d in entries: f.write(f"file '{p}'\nduration {d:.4f}\n")
    f.write(f"file '{entries[-1][0]}'\n")
print("total", round(sum(d for _, d in entries), 1), "s;", len(entries), "frames")
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "list.txt",
                "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-movflags", "+faststart",
                "fabric-factory-demo.mp4"], check=True)
print(os.path.getsize("fabric-factory-demo.mp4"))
