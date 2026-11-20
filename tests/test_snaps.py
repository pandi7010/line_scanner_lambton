"""
tests/test_snaps.py - automatic test, NO cameras needed.

    python tests\\test_snaps.py      -> must print  ALL TESTS PASSED

Checks the OBJECT SNAPS (picture of every object in the Logs):
  * the picture shows the object, at the right place, also across 2 frames
  * too old (frames already thrown away) -> no picture, no crash
  * the ejector puts a FRONT + REAR picture in every log row
  * a camera that did NOT see the object still gets a picture (at the other camera's place)
  * only the newest `keep_snaps` rows keep their picture (memory)
"""
import os
import sys

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # project folder
sys.path.insert(0, os.getcwd())

import numpy as np  # noqa: E402

from core import (CameraClock, ChannelActivity, ChannelDetector, ChannelMap, EjectScheduler,  # noqa: E402
                  EventLog, FrameHistory)

assert "PyQt5" not in sys.modules, "core must not import PyQt5"
LT = 1.0 / 4800
LINES, WIDTH = 16, 4096
failed = []


def check(name, ok):
    print(("  ok    " if ok else "  FAIL  ") + name)
    if not ok:
        failed.append(name)


def blue_frame():
    f = np.zeros((LINES, WIDTH, 3), dtype=np.uint8)
    f[:, :, 0] = 90                  # blue background, like the chute
    return f


# ------------------------------------------------------------------ 1) crop
print("Crop:")
h = FrameHistory(seconds=0.6)
t = 10.0
frames = []
for k in range(10):
    f = blue_frame()
    if k == 4:
        f[10:16, 1000:1060] = (0, 0, 0)       # black object: frame 4 lines 10-15 ...
    if k == 5:
        f[0:4, 1000:1060] = (0, 0, 0)         # ... and frame 5 lines 0-3 (across 2 frames)
    end = t + k * LINES * LT
    h.add(end, LT, f)
    frames.append((end, f))
times4 = CameraClock.line_times(frames[4][0], LT, LINES)
times5 = CameraClock.line_times(frames[5][0], LT, LINES)
snap = h.crop(times4[10], times5[3], 1000, 1059)
dark = snap.image[:, :, 0] < 50
rows = np.flatnonzero(dark.any(axis=1))
cols = np.flatnonzero(dark.any(axis=0))
check(f"object across 2 frames: {len(rows)} dark lines (10 expected)", len(rows) == 10)
check(f"8 lines before/after the object: picture {snap.image.shape[0]} lines", snap.image.shape[0] == 26)
check(f"object pixels in the picture: {cols[0] + snap.x0} - {cols[-1] + snap.x0}",
      cols[0] + snap.x0 == 1000 and cols[-1] + snap.x0 == 1059 and snap.x0 == 970)
check("picture is a separate small array (not the whole frame)", snap.image.shape[1] == 120)

old = h.crop(5.0, 5.001, 1000, 1059)
check("too old -> no picture", old is None)
for k in range(400):                           # 400 more frames -> old ones thrown away
    h.add(t + (10 + k) * LINES * LT, LT, blue_frame())
check(f"only ~0.6 s kept: {len(h._frames)} frames", len(h._frames) <= int(0.6 / (LINES * LT)) + 2)

edge = FrameHistory()
edge.add(1.0, LT, blue_frame())
s = edge.crop(1.0 - 5 * LT, 1.0, 4080, 4095)
check("object at the right edge: picture stops at pixel 4095", s.x0 + s.image.shape[1] == WIDTH)

# ------------------------------------------------------------------ 2) ejector + log
print("Ejector rows with pictures:")
cmap = ChannelMap.equal(0, WIDTH, 63)
hist = {"FRONT": FrameHistory(), "REAR": FrameHistory()}
act = {"FRONT": ChannelActivity(63), "REAR": ChannelActivity(63)}
det = ChannelDetector(cmap, lower=60, upper=200)
log = EventLog(keep_snaps=3)
ej = EjectScheduler(act, log, delay_ms=10, blow_ms=10, histories=hist)


def feed(cam, frame_end, frame):
    hist[cam].add(frame_end, LT, frame)
    rows, _, pixels = det.detect_rows(frame)
    act[cam].mark_rows(rows, CameraClock.line_times(frame_end, LT, LINES), frame_end + 0.003, pixels)


def run(t_from, t_to):
    for x in np.arange(t_from, t_to, 0.0002):
        ej.check_once(now=float(x))


t = 100.0
f = blue_frame()
f[5:12, 1000:1060] = (0, 0, 0)
feed("FRONT", t, f)                               # only FRONT sees it
feed("REAR", t, blue_frame())
for k in range(1, 4):
    feed("FRONT", t + k * LINES * LT, blue_frame())
    feed("REAR", t + k * LINES * LT, blue_frame())
run(t + 0.003, t + 0.1)
rows = log.events_since(0)
e = rows[0]
check(f"log rows: {len(rows)}, each with pictures {[sorted(r.snaps) for r in rows]}",
      len(rows) >= 1 and all(r.snaps and set(r.snaps) == {"FRONT", "REAR"} for r in rows))
check("FRONT picture: seen, shows the dark object",
      e.snaps["FRONT"].seen and (e.snaps["FRONT"].image[:, :, 0] < 50).any())
check("REAR did not see it: picture at FRONT's pixels, marked 'not seen'",
      not e.snaps["REAR"].seen and e.snaps["REAR"].px_from == e.snaps["FRONT"].px_from)

no_hist = EjectScheduler({"FRONT": ChannelActivity(63)}, EventLog(), histories=None)
check("snaps switched off -> rows without pictures, no crash", no_hist._make_snaps(
    {"pixels": {"FRONT": (1, 2)}, "seen": 0, "last": 0}) is None)

for k in range(6):                                # 6 more objects -> only the newest 3 keep pictures
    t2 = 200.0 + k * 0.2
    f = blue_frame()
    f[5:12, 2000:2050] = (0, 0, 0)
    feed("FRONT", t2, f)
    feed("REAR", t2, f)
    for j in range(1, 4):
        feed("FRONT", t2 + j * LINES * LT, blue_frame())
        feed("REAR", t2 + j * LINES * LT, blue_frame())
    run(t2 + 0.003, t2 + 0.1)
rows = log.events_since(0)
with_pictures = [r.seq for r in rows if r.snaps]
check(f"keep_snaps=3: pictures only in the newest rows {with_pictures}",
      len(with_pictures) == 3 and with_pictures == [r.seq for r in rows][-3:])

print()
print("ALL TESTS PASSED" if not failed else f"{len(failed)} TEST(S) FAILED: {failed}")
sys.exit(1 if failed else 0)
