"""
tests/test_timing.py - REAL-TIME end-to-end test, NO cameras needed (takes ~4 seconds).

    python tests\\test_timing.py      -> must print  ALL TESTS PASSED

Two fake cameras run in real time like the real ones: 16-line frames at 4,800 lines/s,
camera timestamps in 0.1 ms steps, and every frame reaches the PC 0.5 ms + 0..3 ms
(random) after it was finished. 10 objects are dropped, 0.1 s apart, through channel 16.

Checks, with the real threads (cameras + ejector):
  * all 10 objects -> 10 rows, seen by BOTH cameras
  * "touched line at" = the real touch time (only the fixed 0.5 ms transfer remains)
  * delay 10 ms -> all ON TIME;  delay 2 ms -> all LATE (the PC needs longer than 2 ms)
  * prints this PC's reaction time and how late the LEDs were
"""
import os
import sys
import time

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # project folder
sys.path.insert(0, os.getcwd())

import numpy as np  # noqa: E402

from core import (CameraClock, CameraWorker, ChannelDetector, ChannelMap,  # noqa: E402
                  EjectScheduler, EventLog)

assert "PyQt5" not in sys.modules, "core must not import PyQt5"
sys.setswitchinterval(0.0005)       # the same as main.py (switch threads every 0.5 ms)
LINE_TIME = 1.0 / 4800
LINES = 16
BASE_LATENCY = 0.0005               # fastest frame transfer (no clock can see this part)
JITTER = 0.003                      # extra random transfer + Windows delay, 0..3 ms
N_OBJECTS = 10


class TimedCamera:
    """Fake camera in real time: frames, camera timestamps, transfer delays, falling objects."""

    def __init__(self, t0, object_lines, seed):
        self.t0 = t0
        self.objects = object_lines          # first line of every object (5 lines tall)
        self.rng = np.random.default_rng(seed)
        self.k = 0
        self.handle = None
        self.info = ""
        self.last_frame_info = None
        self.bg = np.zeros((LINES, 4096, 3), np.uint8)
        self.bg[..., 0] = 93

    def open(self):
        self.handle = 1

    def close(self):
        self.handle = None

    def read_frame(self, timeout_ms=2000):
        first = self.k * LINES
        self.k += 1
        frame_end = self.t0 + (first + LINES - 1) * LINE_TIME        # newest line of this frame
        arrive = frame_end + BASE_LATENCY + self.rng.uniform(0, JITTER)
        # A real camera waits inside the camera SDK (C code) - other threads keep running.
        while time.perf_counter() < arrive - 0.0015:
            time.sleep(0.0005)
        while time.perf_counter() < arrive:
            time.sleep(0)
        frame = self.bg.copy()
        for start in self.objects:
            for line in range(start, start + 5):
                if first <= line < first + LINES:
                    frame[line - first, 990:1030, 0] = 30            # dark grain in channel 16
        ts = int(round((frame_end - self.t0) / 0.0001))
        self.last_frame_info = {"camera_ts": ts, "arrived": time.perf_counter(), "lines": LINES}
        return frame

    def set_frame_lines(self, n):
        pass

    def set_exposure(self, e):
        pass

    def get_current_settings(self):
        return {}


def run(delay_ms):
    t0 = time.perf_counter() + 0.05
    # objects start at line 2 of a frame (early in the frame -> the PC learns about it ~3 ms later)
    object_lines = [LINES * (30 * (j + 1)) + 2 for j in range(N_OBJECTS)]     # every 480 lines = 0.1 s
    truth = [t0 + line * LINE_TIME for line in object_lines]
    workers = {}
    for name, seed in (("FRONT", 1), ("REAR", 2)):
        det = ChannelDetector(ChannelMap.equal(19, 4046), 68, 221)
        workers[name] = CameraWorker(name, TimedCamera(t0, object_lines, seed), detector=det,
                                     clock=CameraClock(default_line_rate_hz=4800))
    log = EventLog()
    ej = EjectScheduler({n: w.activity for n, w in workers.items()}, log, delay_ms=delay_ms, blow_ms=10.0)
    for w in workers.values():
        w.start()
    ej.start()
    time.sleep(0.05 + (object_lines[-1] + 200) * LINE_TIME + 0.1)
    for w in workers.values():
        w.stop()
    time.sleep(0.15)
    ej.stop()
    rows = log.events_since(0)
    seen_perf = [r.seen_time - ej._clock_offset for r in rows]
    return rows, seen_perf, truth


# ---- delay 10 ms
rows, seen, truth = run(10.0)
print(f"delay 10 ms: {len(rows)} rows, seen by {sorted({r.seen_by for r in rows})}, "
      f"results {[r.result for r in rows].count('ON TIME')} ON TIME")
assert len(rows) == N_OBJECTS and all(r.seen_by == "BOTH" and r.channel == 16 for r in rows), \
    [(r.channel, r.seen_by) for r in rows]
errors = (np.array(seen) - np.array(truth)) * 1000
print(f"  touched-line time vs real touch: {errors.mean():.2f} ms on average "
      f"(the fixed {BASE_LATENCY * 1000:.1f} ms transfer), spread {errors.max() - errors.min():.2f} ms")
assert abs(errors.mean() - BASE_LATENCY * 1000) < 0.4 and errors.max() - errors.min() < 0.6, errors
reactions = [r.reaction_ms for r in rows]
lates = [r.late_ms for r in rows]
print(f"  PC reaction: {min(reactions):.1f} - {max(reactions):.1f} ms | late by: max {max(lates):.1f} ms")
assert all(r.result == "ON TIME" for r in rows), [(r.result, r.late_ms) for r in rows]

# ---- delay 2 ms: shorter than the PC reaction -> every LED is late
rows, seen, truth = run(2.0)
print(f"delay 2 ms : {len(rows)} rows, results {sorted({r.result for r in rows})}, "
      f"late by {min(r.late_ms for r in rows):.1f} - {max(r.late_ms for r in rows):.1f} ms")
assert len(rows) == N_OBJECTS and all(r.result == "LATE" for r in rows), [(r.result, r.late_ms) for r in rows]
print("ALL TESTS PASSED")
