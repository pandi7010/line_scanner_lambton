"""
tests/test_ejector.py - automatic test, NO cameras needed.

    python tests\\test_ejector.py      -> must print  ALL TESTS PASSED

Checks the ejector LEDs (exact touch time + delay):
  * camera clock: camera timestamps -> exact line times (also over the 32-bit wrap)
  * line by line: two grains in one channel with a gap of 3 lines = 2 objects
  * ON TIME / LATE / MISSED, "late by", PC reaction
  * the camera that touched FIRST starts the delay (even if its frame came later)
  * blow time follows the object's length
  * log + CSV columns
"""
import csv
import os
import sys
import tempfile

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # project folder
sys.path.insert(0, os.getcwd())

import numpy as np  # noqa: E402

from core import CameraClock, ChannelActivity, EjectScheduler, EventLog, write_csv  # noqa: E402

assert "PyQt5" not in sys.modules, "core must not import PyQt5"
LT = 1.0 / 4800                     # seconds per camera line
rng = np.random.default_rng(3)

# ---------------------------------------------------------------- 1) camera clock
clock = CameraClock(default_line_rate_hz=4800)
errors = []
for k in range(400):
    true_end = 50.0 + k * 16 * LT                       # newest line of frame k
    raw = int(round((true_end - 50.0) / 0.0001)) + (2 ** 32 - 3000)   # wraps around after a few frames
    arrived = true_end + 0.0005 + rng.uniform(0, 0.003)  # 0.5 ms fastest transfer + 0..3 ms extra
    frame_end, line_time = clock.update(raw % 2 ** 32, arrived, 16)
    if k > 50:
        errors.append(frame_end - true_end)
errors = np.array(errors) * 1000
assert clock.using_camera_clock and abs(1 / line_time - 4800) < 30, 1 / line_time
assert abs(errors.mean() - 0.5) < 0.2 and errors.max() - errors.min() < 0.3, (errors.mean(), errors.min(), errors.max())
times = CameraClock.line_times(10.0, LT, 16)
assert abs(times[-1] - 10.0) < 1e-12 and abs(times[0] - (10.0 - 15 * LT)) < 1e-12
print(f"camera clock ok: line rate {1 / line_time:,.0f}/s, line times off by {errors.mean():.2f} ms "
      f"(= the fastest transfer time, which no clock can see), spread {errors.max() - errors.min():.2f} ms")
no_ts = CameraClock()
assert no_ts.update(None, 7.0, 16)[0] == 7.0 and not no_ts.using_camera_clock


# ---------------------------------------------------------------- helpers
def rows_with(lines, channel_rows):
    """{channel: [line numbers]} -> True/False table (lines x 63)."""
    r = np.zeros((lines, 63), dtype=bool)
    for ch, line_numbers in channel_rows.items():
        r[line_numbers, ch - 1] = True
    return r


class Scene:
    """Two cameras fed frame by frame (16 lines) with exact line times."""

    def __init__(self, delay_ms=10.0, blow_ms=10.0):
        self.act = {"FRONT": ChannelActivity(63), "REAR": ChannelActivity(63)}
        self.log = EventLog()
        self.ej = EjectScheduler(self.act, self.log, delay_ms=delay_ms, blow_ms=blow_ms)

    def frame(self, cam, frame_end, channel_rows, found):
        times = CameraClock.line_times(frame_end, LT, 16)
        self.act[cam].mark_rows(rows_with(16, channel_rows), times, found)
        return times

    def run(self, t_from, t_to, step=0.0001):
        for t in np.arange(t_from, t_to, step):
            self.ej.check_once(now=float(t))

    def rows(self):
        return self.log.events_since(0)


# 2) line by line: gap of 3 lines = 2 objects; gap of 2 lines = 1 object; across frames = 1 object
s = Scene()
s.frame("FRONT", 1.0, {10: [3, 4, 5, 6, 10, 11], 11: [3, 4, 5, 6, 9, 10], 12: [14, 15]}, found=1.0)
s.frame("FRONT", 1.0 + 16 * LT, {12: [0, 1]}, found=1.0 + 16 * LT)
last, start, count, *_ = s.act["FRONT"].snapshot()
assert count[9] == 2 and count[10] == 1 and count[11] == 1, count[9:12]
t1 = CameraClock.line_times(1.0, LT, 16)
assert start[11] == t1[14]                                 # object started at line 14 of frame 1
print("line by line ok: gap 3 lines -> 2 objects, gap 2 -> 1 object, across frames -> 1 object")

# 3) ON TIME: touched at line 5, PC finds it 3 ms later, delay 10 ms
s = Scene(delay_ms=10.0)
times = s.frame("FRONT", 100.0, {16: [5, 6, 7]}, found=100.003)
touched = times[5]
s.run(100.003, 100.2)
e = s.rows()[0]
assert e.result == "ON TIME" and e.late_ms <= 0.1 and abs(e.delay_ms - 10.0) <= 0.1, (e.result, e.late_ms, e.delay_ms)   # test checks every 0.1 ms
assert abs(e.reaction_ms - (100.003 - touched) * 1000) < 0.1
print(f"ON TIME ok: reaction {e.reaction_ms} ms, delay measured {e.delay_ms} ms, late {e.late_ms} ms")

# 4) LATE: the PC finds it only 12 ms after the touch (delay 10) -> LED at once, 2 ms late
s = Scene(delay_ms=10.0)
times = s.frame("FRONT", 200.0, {16: [15]}, found=200.0 + 0.012)
s.frame("FRONT", 200.0 + 16 * LT, {}, found=200.0 + 0.012)   # next frame: object gone
s.run(times[15] + 0.012, 200.2)
e = s.rows()[0]
assert e.result == "LATE" and abs(e.late_ms - 2.0) <= 0.1, (e.result, e.late_ms)
print(f"LATE ok: late by {e.late_ms} ms")

# 5) MISSED: found after the whole blow window (touch + delay 10 + blow 10 = 20 ms) -> no LED
s = Scene(delay_ms=10.0, blow_ms=10.0)
times = s.frame("FRONT", 300.0, {16: [15]}, found=300.03)
s.frame("FRONT", 300.0 + 16 * LT, {}, found=300.03)        # object gone
s.run(300.03, 300.2)
e = s.rows()[0]
assert e.result == "MISSED" and e.on_time is None and e.late_ms is None
assert s.ej.lit_channels(min_visible_ms=0, now=300.031) == {}
print("MISSED ok: no LED, row says MISSED")

# 6) REAR touched 2 ms EARLIER, but its frame reached the PC later -> one object, delay from REAR
s = Scene(delay_ms=10.0)
tf = s.frame("FRONT", 400.0 + 15 * LT + 0.002, {16: [0, 1, 2]}, found=400.004)   # FRONT touch 400.002
s.run(400.004, 400.005)
tr = s.frame("REAR", 400.0 + 15 * LT, {16: [0, 1, 2]}, found=400.006)            # REAR touch 400.000
s.run(400.006, 400.2)
rows = s.rows()
assert len(rows) == 1 and rows[0].seen_by == "BOTH", [(r.seen_by, r.channel) for r in rows]
assert abs(rows[0].delay_ms - 10.0) <= 0.1 and rows[0].result == "ON TIME"
assert abs((rows[0].on_time - rows[0].seen_time) - 0.010) < 0.0002
print("first camera starts the delay ok (REAR touched first, its frame came later)")

# 7) FRONT-only and REAR-only 50 ms apart in one channel = 2 objects (window 30 ms)
s = Scene()
s.frame("FRONT", 500.0, {20: [3]}, found=500.001)
s.frame("REAR", 500.05, {20: [3]}, found=500.051)
s.run(500.051, 500.3)
assert [(r.channel, r.seen_by) for r in s.rows()] == [(20, "FRONT"), (20, "REAR")], [(r.channel, r.seen_by) for r in s.rows()]
print("different objects not joined ok")

# 8) blow follows the object's length: 40 lines long object -> LED until last line + delay + blow
s = Scene(delay_ms=10.0, blow_ms=10.0)
t_a = s.frame("FRONT", 600.0, {30: list(range(8, 16))}, found=600.001)
s.run(600.001, 600.002)
t_b = s.frame("FRONT", 600.0 + 16 * LT, {30: list(range(16))}, found=600.0 + 16 * LT + 0.001)
t_c = s.frame("FRONT", 600.0 + 32 * LT, {30: list(range(16))}, found=600.0 + 32 * LT + 0.001)
s.frame("FRONT", 600.0 + 48 * LT, {}, found=600.0 + 48 * LT + 0.001)
s.run(600.0 + 48 * LT + 0.001, 600.3)
expected_off = t_c[15] + 0.010 + 0.010
lit_before = s.ej.lit_channels(min_visible_ms=0, now=expected_off - 0.0005)
lit_after = s.ej.lit_channels(min_visible_ms=0, now=expected_off + 0.0005)
assert 30 in lit_before and 30 not in lit_after, (lit_before, lit_after)
e = s.rows()[0]
assert abs(e.length_ms - (t_c[15] - t_a[8]) * 1000) < 0.1
print(f"blow by length ok: object {e.length_ms} ms long -> LED ON for {(expected_off - e.on_time + s.ej._clock_offset) * 1000:.1f} ms")

# 9) CSV
path = os.path.join(tempfile.gettempdir(), "eject_test.csv")
write_csv(path, s.rows())
with open(path) as f:
    rows = list(csv.reader(f))
assert rows[0] == ["object_no", "touched_line_at", "seen_by", "channel", "pc_saw_at", "pc_reaction_ms", "led_on_at",
                   "delay_set_ms", "delay_measured_ms", "late_by_ms", "result", "object_length_ms",
                   "front_pixel_from", "front_pixel_to", "rear_pixel_from", "rear_pixel_to",
                   "valve_fired"], rows[0]
print("csv ok:", rows[1][:12])
print("ALL TESTS PASSED")
