"""
tests/test_frame_check.py - automatic test, NO cameras needed (real time, ~4 s).

    python tests\\test_frame_check.py      -> must print  ALL TESTS PASSED

A fake camera sends 16-line frames at 300 frames/s with camera timestamps, but
LEAVES OUT 5 frames on purpose (frame 100, 250, 400, 401, 402). The Frame check must say:
  1. ejector status calculated for every frame that arrived
  2. 5 frames missed, 80 lines missed (5 x 16)
  3. a time per frame for every frame (and below the 3.33 ms budget here)
"""
import os
import sys
import time

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # project folder
sys.path.insert(0, os.getcwd())

import numpy as np  # noqa: E402

from core import CameraClock, CameraWorker, ChannelDetector, ChannelMap  # noqa: E402

sys.setswitchinterval(0.0005)
LINE_TIME = 1.0 / 4800
LINES = 16
DROP = {100, 250, 400, 401, 402}
failed = []


def check(name, ok):
    print(("  ok    " if ok else "  FAIL  ") + name)
    if not ok:
        failed.append(name)


class DroppingCamera:
    def __init__(self, t0, drop=DROP, pause_every=0):
        self.t0, self.k = t0, 0
        self.drop = drop
        self.pause_every = pause_every   # every Nth frame the camera pauses 8 lines (no frame missing)
        self.extra_lines = 0
        self.handle, self.info, self.last_frame_info = None, "", None
        self.bg = np.zeros((LINES, 4096, 3), np.uint8)
        self.bg[..., 0] = 90
        self.sdk_lost = 0
        self.line_rate = None            # None = the check measures the line time from the camera clock

    def open(self):
        self.handle = 1

    def close(self):
        self.handle = None

    def read_frame(self, timeout_ms=2000):
        while self.k in self.drop:       # this frame never reaches the PC
            self.k += 1
        k = self.k
        self.k += 1
        if self.pause_every and k and k % self.pause_every == 0:
            self.extra_lines += 8            # the camera stopped scanning for 8 lines
        frame_end = self.t0 + (k * LINES + self.extra_lines + LINES - 1) * LINE_TIME
        wait = frame_end + 0.0005 - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
        frame = self.bg.copy()
        if k % 7 == 0:
            frame[3:9, 1000:1050, 0] = 20                      # some objects
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
        return {"line_rate": self.line_rate}


cam = DroppingCamera(time.perf_counter() + 0.05)
worker = CameraWorker("FRONT", cam, detector=ChannelDetector(ChannelMap.equal(19, 4046), 68, 221),
                      clock=CameraClock(default_line_rate_hz=4800), end_gap=8)
fc = worker.start_frame_check(1000)
worker.start()
t_end = time.time() + 15
while not fc.done and time.time() < t_end:
    time.sleep(0.05)
worker.stop()

r = fc.report()
print(fc.text())
print()
check("1000 frames received", r["frames_received"] == 1000)
check("ejector status calculated for all 1000", r["status_calculated"] == 1000)
check(f"missed frames found from the camera timestamps: {r['missed_frames_camera_clock']} (5 left out)",
      r["missed_frames_camera_clock"] == 5)
check(f"missed lines: {r['lines_missed']} (5 x 16 = 80)", r["lines_missed"] == 80)
check(f"lines received {r['lines_received']} = 1000 x 16", r["lines_received"] == 16000)
check(f"time per frame measured for every frame (avg {r['proc_avg_ms']:.3f} ms)",
      len(fc.proc_s) == 1000 and r["proc_avg_ms"] > 0)
check("4. consistency: spread + time bands reported", "within_1ms_of_median" in r
      and sum(r["bands"].values()) == 1000)
import tempfile  # noqa: E402
csv_path = os.path.join(tempfile.mkdtemp(), "frames.csv")
fc.write_csv(csv_path)
import csv as _csv  # noqa: E402
rows = list(_csv.DictReader(open(csv_path)))
check("CSV: 1000 frames, one row each", len(rows) == 1000)
check("ejector status = ONE number (digits only) in every row",
      all(r["ejector_status"].isdigit() for r in rows))
with_obj = [int(r["ejector_status"]) for r in rows if int(r["ejector_status"])]
ch = [c + 1 for c in range(63) if with_obj and with_obj[0] >> c & 1]
check(f"object in pixels 1000-1049 -> status {with_obj[0] if with_obj else '-'} = channels {ch}",
      bool(with_obj) and all(16 <= c <= 17 for c in ch) and all(s < 2 ** 63 for s in with_obj))
check(f"missed_frames_before adds up to 5: {sum(int(r['missed_frames_before'] or 0) for r in rows)}",
      sum(int(r["missed_frames_before"] or 0) for r in rows) == 5)
check(f"frame time from the camera clock: {r['frame_time_ms']:.2f} ms (3.33 expected)",
      abs(r["frame_time_ms"] - 3.333) < 0.1)

# --- 2) line rate SETTING known (4800 lines/s), camera PAUSES 8 lines every 50th frame, per-line experiment
print()
cam2 = DroppingCamera(time.perf_counter() + 0.05, drop=set(), pause_every=50)
cam2.line_rate = 4800
worker2 = CameraWorker("REAR", cam2, detector=ChannelDetector(ChannelMap.equal(19, 4046), 68, 221),
                       clock=CameraClock(default_line_rate_hz=4800), end_gap=8)
worker2.start()
time.sleep(0.3)                                   # camera open -> settings (line rate) read back
fc2 = worker2.start_frame_check(500, per_line=True)
t_end = time.time() + 10
while not fc2.done and time.time() < t_end:
    time.sleep(0.05)
worker2.stop()
r2 = fc2.report()
print(fc2.text())
print()
pauses = sum(1 for k in range(1, 2000) if k % 50 == 0 and k < cam2.k and k >= cam2.k - 500)
check(f"line-to-line time from the setting: {r2['line_time_us']:.1f} us (208.3 = 1/4800)",
      abs(r2["line_time_us"] - 208.33) < 0.1 and r2["line_time_source"] == "camera setting")
check(f"camera paused (no whole frame missing): frames lost {r2['missed_frames_camera_clock']} = 0",
      r2["missed_frames_camera_clock"] == 0)
check(f"... but the paused lines are counted as LOST: {r2['lines_missed']} (~{pauses * 8} expected)",
      abs(r2["lines_missed"] - pauses * 8) <= 3)
path2 = os.path.join(tempfile.mkdtemp(), "lines.csv")
n_lines = fc2.write_lines_csv(path2)
line_rows = list(_csv.DictReader(open(path2)))
check(f"per-line experiment: {n_lines} line rows (500 x 16)", n_lines == 8000 and len(line_rows) == 8000)
obj = [r for r in line_rows if int(r["line_status"])]
check(f"per-line status: object lines found ({len(obj)}), each ONE number with channels 16-17 only",
      bool(obj) and all(int(r["line_status"]) & ~((1 << 15) | (1 << 16)) == 0 for r in obj))
t = [float(r["line_time_ms"]) for r in line_rows[:16]]
check(f"line times step by the line time: {t[1] - t[0]:.4f} ms", abs((t[1] - t[0]) - 0.20833) < 0.001)

print()
print("ALL TESTS PASSED" if not failed else f"{len(failed)} TEST(S) FAILED: {failed}")
sys.exit(1 if failed else 0)
