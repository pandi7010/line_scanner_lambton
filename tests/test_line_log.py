"""
tests/test_line_log.py - automatic test, NO cameras needed (real time, ~3 s).

    python tests\\test_line_log.py      -> must print  ALL TESTS PASSED

A fake camera at 4800 lines/s (16-line frames, camera timestamps) with an object in
channels 16-17, and 3 frames left out on purpose. The Line log must:
  * write ONE row per line received, with ONE 64-bit number as the ejector status
  * mark the lost lines (missed_lines_before = 48 = 3 frames x 16 lines)
  * step the camera time by exactly one line time (1/4800 s = 0.2083 ms)
  * keep up: the camera thread only hands over the frame (no slow-down)
"""
import csv
import os
import sys
import tempfile
import time

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # project folder
sys.path.insert(0, os.getcwd())

import numpy as np  # noqa: E402

from core import CameraClock, CameraWorker, ChannelDetector, ChannelMap, LineLogger  # noqa: E402

sys.setswitchinterval(0.0005)
LINE_TIME = 1.0 / 4800
LINES = 16
DROP = {200, 401, 402}
failed = []


def check(name, ok):
    print(("  ok    " if ok else "  FAIL  ") + name)
    if not ok:
        failed.append(name)


class FakeCamera:
    def __init__(self, t0):
        self.t0, self.k = t0, 0
        self.handle, self.info, self.last_frame_info = None, "", None
        self.bg = np.zeros((LINES, 4096, 3), np.uint8)
        self.bg[..., 0] = 90

    def open(self):
        self.handle = 1

    def close(self):
        self.handle = None

    def read_frame(self, timeout_ms=2000):
        while self.k in DROP:
            self.k += 1
        k = self.k
        self.k += 1
        frame_end = self.t0 + (k * LINES + LINES - 1) * LINE_TIME
        wait = frame_end + 0.0005 - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
        frame = self.bg.copy()
        if k % 10 == 0:
            frame[4:7, 1000:1050, 0] = 20                    # object: lines 5-7 of the frame, ch 16-17
        self.last_frame_info = {"camera_ts": int(round((frame_end - self.t0) / 0.0001)),
                                "arrived": time.perf_counter(), "lines": LINES}
        return frame

    def frame_statistics(self):
        return {"lost": 0}

    def set_frame_lines(self, n):
        pass

    def set_exposure(self, e):
        pass

    def get_current_settings(self):
        return {"line_rate": 4800}


cam = FakeCamera(time.perf_counter() + 0.05)
worker = CameraWorker("FRONT", cam, detector=ChannelDetector(ChannelMap.equal(19, 4046), 68, 221),
                      clock=CameraClock(default_line_rate_hz=4800), end_gap=8, snap_seconds=0)
path = os.path.join(tempfile.mkdtemp(), "line_log.csv")
worker.start()
time.sleep(0.2)
logger = LineLogger(path, {"FRONT": 4800}).start({"FRONT": worker})
t_end = time.time() + 10
while worker.frame_count < 520 and time.time() < t_end:
    time.sleep(0.05)
summary = logger.stop()
worker.stop()
print(summary["text"])

rows = list(csv.DictReader(open(path)))
frames = summary["FRONT"]["frames"]
check(f"one row per line: {len(rows)} rows = {frames} frames x 16", len(rows) == frames * 16 and frames > 300)
check("every ejector status is ONE number (digits only)", all(r["ejector_status"].isdigit() for r in rows))
lost = sum(int(r["missed_lines_before"]) for r in rows)
check(f"lost lines marked: {lost} (3 frames x 16 = 48)", lost == 48 and summary["FRONT"]["lost"] == 48)
obj = [r for r in rows if int(r["ejector_status"])]
check(f"object lines: {len(obj)}, status = channels 16 + 17 only "
      f"({obj[0]['ejector_status'] if obj else '-'} = 2^15 + 2^16 = {2 ** 15 + 2 ** 16})",
      bool(obj) and all(int(r["ejector_status"]) == 2 ** 15 + 2 ** 16 for r in obj)
      and {r["line"] for r in obj} == {"5", "6", "7"})
t = [float(r["camera_time_ms"]) for r in rows[:32]]
check(f"camera time steps by one line: {t[1] - t[0]:.4f} ms (0.2083)", abs(t[1] - t[0] - 0.20833) < 0.0005)
check(f"next frame continues the time: {t[16] - t[15]:.4f} ms", abs(t[16] - t[15] - 0.20833) < 0.0005)
i = rows.index(next(r for r in rows if int(r["missed_lines_before"]) == 16))
check(f"a lost frame shows as a time jump of 17 lines: {float(rows[i]['camera_time_ms']) - float(rows[i - 1]['camera_time_ms']):.4f} ms",
      abs(float(rows[i]["camera_time_ms"]) - float(rows[i - 1]["camera_time_ms"]) - 17 * 0.20833) < 0.001)
gap_row = next(r for r in rows if int(r["missed_lines_before"]) == 16)
check(f"after a lost frame the time jumps 17 lines: row frame {gap_row['frame']} line {gap_row['line']}",
      gap_row["line"] == "1")

print()
print("ALL TESTS PASSED" if not failed else f"{len(failed)} TEST(S) FAILED: {failed}")
sys.exit(1 if failed else 0)
