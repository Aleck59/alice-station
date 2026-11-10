"""Clock-screen emotion animations for the Station Midi (45 white LEDs).

Frame = 48 bytes: 0x00, 45 brightness bytes (map below, 1-based), duration ms
big-endian. Map measured on the speaker 2026-11-10 (clock_led_util calibration).
Writes <mood>.scled (binary, the bridge sends it gzip+base64 like the cloud),
<mood>.txt (clock_led_util text format, for testing on the speaker) and
emotions.go.txt (base64 constants for the bridge).
"""
import base64, gzip, io

SEG = {}
for d, base in enumerate((1, 10, 19, 28), start=1):
    for i, s in enumerate("abcdefg"):
        SEG[f"{s}{d}"] = base + i
SEG.update({"p1": 8, "q1": 9, "p2": 17, "q2": 18, "p4": 26, "q4": 27, "p5": 35, "q5": 36,
            "deg": 37, "p3": 38, "q3": 39, "h1": 40, "h2": 41, "m1": 42, "m2": 43, "l1": 44, "l2": 45})


def frame(lit, level=255, ms=20, extra=None):
    b = [0] * 46
    for n in lit:
        b[SEG[n]] = level
    for n, v in (extra or {}).items():
        b[SEG[n]] = v
    return bytes(b) + ms.to_bytes(2, "big")


def fade(lit, steps=(64, 128, 191, 255), ms=20):
    return [frame(lit, v, ms) for v in steps]


def show(lit, hold, extra_frames=()):
    out = fade(lit) + [frame(lit, 255, hold)] + list(extra_frames)
    out += fade(lit, (191, 128, 64), 20) + [frame([], 0, 500)]
    return out


EYES_FLAT = ["g1", "g4"]
ANIM = {}
# sad: flat eyes, frown (∩ over digits 2-3), a tear slides down under the left eye
frown = ["f2", "a2", "a3", "b3"]
sad = EYES_FLAT + frown
tear = [frame(sad + t, 255, 260) for t in (["c1"], ["d1"], [], ["c1"], ["d1"], [])]
ANIM["sad"] = show(sad, 600, tear + [frame(sad, 255, 600)])
# angry: slanted brows (b1 \ , f4 /) over flat eyes, flat mouth; brows twitch
angry = ["b1", "g1", "f4", "g4", "d2", "d3"]
twitch = []
for _ in range(3):
    twitch += [frame(angry, 255, 160), frame(["g1", "g4", "d2", "d3"], 255, 120)]
ANIM["angry"] = show(angry, 500, twitch + [frame(angry, 255, 500)])
# wow: wide eyes (upper boxes) and a big O mouth over digits 2-3
eyes_wide = ["a1", "b1", "f1", "g1", "a4", "b4", "f4", "g4"]
mouth_o = ["a2", "f2", "e2", "d2", "a3", "b3", "c3", "d3"]
ANIM["wow"] = show(eyes_wide + mouth_o, 1800)
# love: happy eyes ^ ^ and a heartbeat on the colon
eyes_happy = ["a1", "b1", "a4", "f4"]
beat = []
for _ in range(3):
    beat += [frame(eyes_happy + ["p3", "q3"], 255, 140), frame(eyes_happy, 255, 110),
             frame(eyes_happy + ["p3", "q3"], 255, 140), frame(eyes_happy, 255, 420)]
ANIM["love"] = show(eyes_happy, 300, beat)

consts = []
for mood, frames in ANIM.items():
    raw = b"".join(frames)
    assert all(len(f) == 48 for f in frames)
    open(f"{mood}.scled", "wb").write(raw)
    with open(f"{mood}.txt", "w") as t:
        for f in frames:
            t.write(" ".join(str(x) for x in f[1:46]) + " " + str(int.from_bytes(f[46:48], "big")) + "\n")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as z:
        z.write(raw)
    consts.append(f'\t"{mood}": "{base64.b64encode(buf.getvalue()).decode()}",')
    print(mood, len(frames), "frames", sum(int.from_bytes(f[46:48], "big") for f in frames), "ms")
open("emotions.go.txt", "w").write("\n".join(consts) + "\n")
