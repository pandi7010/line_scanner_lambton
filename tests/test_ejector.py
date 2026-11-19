"""
tests/test_ejector.py - automatic test, NO cameras needed.

    python tests\\test_ejector.py      -> must print  ALL TESTS PASSED

Checks the ejector LEDs and their log:
  * either camera triggers an ejection; FRONT + REAR on one object = ONE ejection ("BOTH")
  * the LED switches ON `delay` after the FIRST camera saw it, for `blow` ms
  * the same camera seeing a channel again = a new object = a new ejection
  * the log row has seen time, LED ON time, delay set and delay measured
  * with real threads: how exactly THIS PC keeps the delay (printed)
"""
import csv
import os
import sys
import tempfile
import time

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # project folder
sys.path.insert(0, os.getcwd())

import numpy as np  # noqa: E402

from core import (CameraWorker, ChannelActivity, ChannelDetector,  # noqa: E402
                  ChannelMap, EjectScheduler, EventLog, write_csv)

assert "PyQt5" not in sys.modules, "core must not import PyQt5"
FRAME = 0.0033


class Scene:
    """Feeds both cameras frame by frame with exact (fake) times."""

    def __init__(self, delay_ms=2.0, blow_ms=10.0):
        self.act = {"FRONT": ChannelActivity(63), "REAR": ChannelActivity(63)}
        self.log = EventLog()
        self.ej = EjectScheduler(self.act, self.log, delay_ms=delay_ms, blow_ms=blow_ms)
        self.t = 100.0
        self.lit_history = []

    def frame(self, front=(), rear=()):
        self.act["FRONT"].mark(set(front), now=self.t)
        self.act["REAR"].mark(set(rear), now=self.t)
        # the scheduler checks every 0.1 ms between frames
        for k in range(33):
            now = self.t + k * 0.0001
            self.ej.check_once(now=now)
            self.lit_history.append((round(now - 100.0, 4), self.ej.lit_channels(min_visible_ms=0, now=now)))
        self.t += FRAME

    def idle(self, seconds):
        for _ in range(int(seconds / FRAME)):
            self.frame()

    def rows(self):
        return [(e.channel, e.seen_by, e.delay_set_ms, e.delay_ms) for e in self.log.events_since(0)]


# 1) FRONT only, REAR only -> each one ejection
s = Scene(delay_ms=2.0)
s.frame(front={10}); s.idle(0.3)
s.frame(rear={20}); s.idle(0.3)
assert s.rows() == [(10, "FRONT", 2.0, 2.0), (20, "REAR", 2.0, 2.0)], s.rows()
print("either camera ok:", s.rows())

# 2) FRONT then REAR 1 frame later = one object, BOTH, delay from FRONT
s = Scene(delay_ms=5.0, blow_ms=10.0)
s.frame(front={16}); s.frame(front={16}, rear={16}); s.frame(rear={16}); s.idle(0.3)
assert s.rows() == [(16, "BOTH", 5.0, 5.0)], s.rows()
on = [t for t, lit in s.lit_history if 16 in lit]
assert abs(on[0] - 0.005) < 1e-9 and abs(on[-1] - 0.015) < 2e-4, (on[0], on[-1])   # ON at 5 ms, OFF after 10 ms
e = s.log.events_since(0)[0]
assert abs((e.on_time - e.seen_time) * 1000 - 5.0) < 0.01
print(f"both cameras -> 1 ejection ok: LED ON at {on[0]*1000:.1f} ms, OFF at {on[-1]*1000:.1f} ms")

# 3) same camera, 3 objects 30 ms apart in one channel -> 3 ejections
s = Scene(delay_ms=2.0)
for _ in range(3):
    s.frame(front={7}); s.idle(0.03)
s.idle(0.3)
assert [r[0] for r in s.rows()] == [7, 7, 7], s.rows()
print("3 objects in one channel -> 3 ejections ok")

# 4) delay 0, and changing the delay later
s = Scene(delay_ms=0.0)
s.frame(front={3}); s.idle(0.2)
s.ej.set_timing(delay_ms=20.0)
s.frame(front={4}); s.idle(0.3)
r = s.rows()   # the test checks every 0.1 ms -> measured may be 0.1 ms later
assert [x[:3] for x in r] == [(3, "FRONT", 0.0), (4, "FRONT", 20.0)], r
assert r[0][3] == 0.0 and 20.0 <= r[1][3] <= 20.1, r
print("delay 0 and delay change ok")

# 4b) pixel ranges in the log: FRONT first, REAR 1 frame later; the object grows in the 2nd frame
s = Scene(delay_ms=2.0)
s.act["FRONT"].mark({16, 17}, now=s.t, pixels={16: (1000, 1050), 17: (1000, 1050)})
s.act["REAR"].mark(set(), now=s.t)
s.ej.check_once(now=s.t)
s.t += FRAME
s.act["FRONT"].mark({16, 17}, now=s.t, pixels={16: (998, 1060), 17: (998, 1060)})
s.act["REAR"].mark({16, 17}, now=s.t, pixels={16: (1012, 1071), 17: (1012, 1071)})
s.ej.check_once(now=s.t)
s.idle(0.3)
ev = s.log.events_since(0)
assert [(e.channel, e.seen_by, e.pixels) for e in ev] == [
    (16, "BOTH", {"FRONT": (998, 1060), "REAR": (1012, 1071)}),
    (17, "BOTH", {"FRONT": (998, 1060), "REAR": (1012, 1071)})], [(e.channel, e.pixels) for e in ev]
assert ev[0].pixels_text("FRONT") == "998 - 1060" and ev[0].pixels_text("REAR") == "1012 - 1071"
print("pixels in log ok:", ev[0].pixels_text("FRONT"), "|", ev[0].pixels_text("REAR"))

# 4c) decision-line picture: dot falls from 0 (decision line) to 1 (nozzle), then disappears
act = {"FRONT": ChannelActivity(63), "REAR": ChannelActivity(63)}
ej = EjectScheduler(act, EventLog(), delay_ms=10.0)
act["FRONT"].mark({5}, now=0.0)
ej.check_once(now=0.0)
pic = lambda t: [(o["channel"], round(o["progress"], 2), o["fired"]) for o in ej.in_flight(150, now=t)]  # noqa: E731
assert pic(0.0) == [(5, 0.0, False)], pic(0.0)
ej.check_once(now=0.010)                                  # LED ON after 10 ms
assert pic(0.075) == [(5, 0.5, True)], pic(0.075)        # shown over 150 ms -> half way
assert pic(0.150) == [(5, 1.0, True)] and pic(0.400) == [], (pic(0.150), pic(0.400))
ej.set_timing(delay_ms=400.0)                             # long delay: real speed is shown
act["FRONT"].mark(set(), now=1.0)
act["FRONT"].mark({9}, now=1.01)
ej.check_once(now=1.01)
assert pic(1.21) == [(9, 0.5, False)], pic(1.21)          # half way, not fired yet
ej.check_once(now=1.411)
assert pic(1.411) == [(9, 1.0, True)], pic(1.411)
print("decision line picture ok")

# 5) CSV
path = os.path.join(tempfile.gettempdir(), "eject_test.csv")
write_csv(path, s.log.events_since(0))
with open(path) as f:
    rows = list(csv.reader(f))
assert rows[0] == ["seen_at", "seen_by", "channel", "led_on_at", "delay_set_ms", "delay_measured_ms",
                   "difference_ms", "front_pixel_from", "front_pixel_to", "rear_pixel_from",
                   "rear_pixel_to"] and len(rows) == 3, rows
assert rows[1][7:] == ["998", "1060", "1012", "1071"], rows[1]
print("csv ok:", rows[2])

# 6) real threads: cameras + scheduler, measure how exact this PC is
bg = np.zeros((16, 4096, 3), np.uint8)
bg[..., 0] = 93


class Cam:
    """Fake camera: a dark grain for 3 frames every 30 frames."""
    handle = None
    info = ""

    def __init__(self, xs):
        self.n = 0
        self.xs = xs

    def open(self):
        self.handle = 1

    def close(self):
        self.handle = None

    def read_frame(self, timeout_ms=2000):
        time.sleep(0.003)
        self.n += 1
        f = bg.copy()
        if self.n % 30 < 3:
            for x in self.xs:
                f[:, x:x + 60, 0] = 30
        return f

    def set_frame_lines(self, n):
        pass

    def set_exposure(self, e):
        pass

    def get_current_settings(self):
        return {}


def detector():
    return ChannelDetector(ChannelMap.equal(19, 4046), 68, 221)


fw = CameraWorker("FRONT", Cam([1000, 3000]), detector=detector())   # ch 16,17 and 47,48
rw = CameraWorker("REAR", Cam([1000]), detector=detector())          # ch 16,17
log = EventLog()
ej = EjectScheduler({"FRONT": fw.activity, "REAR": rw.activity}, log, delay_ms=5.0, blow_ms=10.0)
for w in (fw, rw):
    w.start()
ej.start()
time.sleep(2.0)
for w in (fw, rw):
    w.stop()
time.sleep(0.3)
ej.stop()
ev = log.events_since(0)
by = {}
for e in ev:
    by.setdefault((e.channel, e.seen_by), []).append(e)
diffs = np.array([e.delay_ms - e.delay_set_ms for e in ev])
print("threads: rows per (channel, seen by):", {k: len(v) for k, v in sorted(by.items())})
print(f"threads: delay set 5.0 ms -> measured min {diffs.min() + 5:.2f}, "
      f"average {diffs.mean() + 5:.2f}, max {diffs.max() + 5:.2f} ms on this PC")
assert {ch for ch, _ in by} == {16, 17, 47, 48}
# 16/17: seen by both cameras. (When the test stops the two fake cameras one after the
# other, the very last grain may be seen by one camera only -> allow 1 such row.)
for ch in (16, 17):
    assert len(by.get((ch, "BOTH"), [])) >= 15, by.keys()
    assert len(by.get((ch, "FRONT"), [])) + len(by.get((ch, "REAR"), [])) <= 1, by.keys()
assert all(b == "FRONT" for ch, b in by if ch in (47, 48))
both_rows = [e for e in ev if e.seen_by == "BOTH"]
assert all(e.pixels == {"FRONT": (1000, 1059), "REAR": (1000, 1059)} for e in both_rows), \
    {(e.channel, str(e.pixels)) for e in both_rows}
assert all(e.pixels == {"FRONT": (3000, 3059)} for e in ev if e.channel in (47, 48))
print("threads: pixels in log ok, e.g.", both_rows[0].pixels)
assert diffs.min() >= -0.01 and diffs.max() < 5.0, diffs
print("ALL TESTS PASSED")
