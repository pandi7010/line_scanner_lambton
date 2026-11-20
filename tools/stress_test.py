"""
tools/stress_test.py - FEEDER LOAD test, no cameras / no ESP32 needed (real time, real threads).

    .venv\\Scripts\\python.exe tools\\stress_test.py            (10 s, 150 objects/s)
    .venv\\Scripts\\python.exe tools\\stress_test.py 20 300     (20 s, 300 objects/s)
    .venv\\Scripts\\python.exe tools\\stress_test.py compare    (5 variants, 8 s each, one table)

"compare" runs the same load 5 times, each in a fresh program, with different settings:
    A  as the viewer now      high priority ON,  1 ms timer ON,  ejector spin ON
    B  no high priority       high priority OFF, 1 ms timer ON,  ejector spin ON
    C  nothing special        high priority OFF, 1 ms timer OFF, ejector spin ON
    D  no spin                high priority ON,  1 ms timer ON,  ejector spin OFF
    E  no priority, no spin   high priority OFF, 1 ms timer ON,  ejector spin OFF

Copies the feeder test of 20 Nov 2026 (190 log rows/s): two fake cameras at 300 frames/s,
many small random grains (5 - 70 px wide, 1 - 25 lines long) in channels 1 - 63,
each camera sees a grain with 80 % chance. The ejector is ARMED on a fake ESP32
(USB write 1 ms). Object snaps ON - like the real program.

Prints ON TIME / LATE / MISSED, the PC reaction and the CPU of every part.
Goal: no MISSED and no LATE when the PC found the object in time.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from core import (CameraClock, CameraWorker, ChannelDetector, ChannelMap,  # noqa: E402
                  EjectScheduler, EventLog, realtime)
from hardware import esp32_ejector  # noqa: E402

sys.setswitchinterval(0.0005)
LINE_TIME = 1.0 / 4800
LINES = 16
SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] != "compare" else 10.0
RATE = float(sys.argv[2]) if len(sys.argv) > 2 else 150.0
VARIANT = sys.argv[3] if len(sys.argv) > 3 else "A"
VARIANTS = {   # name: (high priority, 1 ms timer, ejector spin)
    "A": (True, True, True), "B": (False, True, True), "C": (False, False, True),
    "D": (True, True, False), "E": (False, True, False)}


class FeederCamera:
    def __init__(self, t0, grains, seed):
        self.t0, self.grains = t0, grains
        self.rng = np.random.default_rng(seed)
        self.k, self.handle, self.info, self.last_frame_info = 0, None, "", None
        self.bg = np.zeros((LINES, 4096, 3), np.uint8)
        self.bg[..., 0] = 90
        self.idx = 0

    def open(self):
        self.handle = 1

    def close(self):
        self.handle = None

    def read_frame(self, timeout_ms=2000):
        first = self.k * LINES
        self.k += 1
        frame_end = self.t0 + (first + LINES - 1) * LINE_TIME
        arrive = frame_end + 0.0005 + self.rng.uniform(0, 0.003)
        wait = arrive - time.perf_counter()      # a real camera waits inside the SDK (C code, lane free)
        if wait > 0:
            time.sleep(wait)
        frame = self.bg.copy()
        while self.idx < len(self.grains) and self.grains[self.idx][0] + 40 < first:
            self.idx += 1
        j = self.idx
        while j < len(self.grains) and self.grains[j][0] < first + LINES:
            line, length, x, w, seen = self.grains[j]
            if seen[self.name_index]:
                a, b = max(line, first), min(line + length, first + LINES)
                if b > a:
                    frame[a - first:b - first, x:x + w, 0] = 25
            j += 1
        ts = int(round((frame_end - self.t0) / 0.0001))
        self.last_frame_info = {"camera_ts": ts, "arrived": time.perf_counter(), "lines": LINES}
        return frame

    def set_frame_lines(self, n):
        pass

    def set_exposure(self, e):
        pass

    def get_current_settings(self):
        return {}


class FakeUsb:
    """Fake ESP32 COM port: every USB write takes 1 ms, answers the ping."""

    def __init__(self, port, baud):
        self.answer, self.bytes = b"", 0

    def write(self, data):
        if b"P" in data:
            self.answer = b"ACK: ESP32_ALIVE\r\n"
        time.sleep(0.001)
        self.bytes += len(data)
        return len(data)

    def read(self, n):
        a, self.answer = self.answer, b""
        return a

    def reset_input_buffer(self):
        pass

    def flush(self):
        pass

    def close(self):
        pass


def main():
    priority, timer, spin = VARIANTS[VARIANT]
    realtime.configure(priority, timer)
    if not spin:
        from core import ejector as ejector_module
        ejector_module.SPIN_S = 0.0          # no "check again at once": sleep until the LED is due
    rng = np.random.default_rng(7)
    t0 = time.perf_counter() + 0.2
    total_lines = int(SECONDS / LINE_TIME)
    n = int(SECONDS * RATE)
    starts = np.sort(rng.integers(100, total_lines - 200, n))
    grains = []
    for line in starts:
        seen = rng.random(2) < 0.8
        if not seen.any():
            seen[rng.integers(2)] = True
        grains.append((int(line), int(rng.integers(1, 26)), int(rng.integers(30, 4000)),
                       int(rng.integers(5, 71)), seen))

    workers = {}
    for i, name in enumerate(("FRONT", "REAR")):
        cam = FeederCamera(t0, grains, seed=i)
        cam.name_index = i
        det = ChannelDetector(ChannelMap.equal(19, 4046), 68, 221)
        workers[name] = CameraWorker(name, cam, detector=det, clock=CameraClock(default_line_rate_hz=4800),
                                     end_gap=8, snap_seconds=0.6)
    log = EventLog(object_join_ms=5)
    ej = EjectScheduler({n_: w.activity for n_, w in workers.items()}, log, delay_ms=10, blow_ms=10,
                        histories={n_: w.history for n_, w in workers.items()})
    esp32_ejector.serial_factory = FakeUsb
    board = esp32_ejector.Esp32Ejector()
    board.connect("COM_FAKE")
    ej.set_valves(board)
    ej.arm(True)

    cpu0 = time.process_time()
    for w in workers.values():
        w.start()
    ej.start()
    time.sleep(SECONDS + 1.5)
    for w in workers.values():
        w.stop()
    ej.stop()
    board.close()
    cpu = (time.process_time() - cpu0) / (SECONDS + 1.5) * 100

    rows = log.events_since(0)
    res = [r.result for r in rows]
    react = np.array([r.reaction_ms for r in rows]) if rows else np.zeros(1)
    late = np.array([r.late_ms for r in rows if r.late_ms is not None] or [0])
    in_time = [r for r in rows if r.reaction_ms < 9.0]
    bad_in_time = [r for r in in_time if r.result != "ON TIME"]
    print(f"grains dropped: {n} ({RATE:.0f}/s for {SECONDS:.0f} s)   log rows: {len(rows)} "
          f"({len(rows) / SECONDS:.0f}/s)   objects: {log.object_count}")
    print(f"ON TIME {res.count('ON TIME')}   LATE {res.count('LATE')}   MISSED {res.count('MISSED')}")
    print(f"PC reaction: median {np.median(react):.1f} ms, 95 % {np.percentile(react, 95):.1f} ms, "
          f"max {react.max():.1f} ms   late by: max {late.max():.1f} ms")
    with_pictures = sum(1 for r in rows if r.snaps and len(r.snaps) == 2)
    print(f"rows with FRONT + REAR picture: {with_pictures} of {len(rows)} (newest 2000 keep them)")
    print(f"found in time (< 9 ms) but NOT on time: {len(bad_in_time)} of {len(in_time)}  <- must be 0")
    print(f"CPU: whole program {cpu:.0f} % of one core | cameras "
          + ", ".join(f"{k} {w.cpu_percent:.0f} %" for k, w in workers.items())
          + f" | ejector {ej.cpu_percent:.0f} %")
    print(f"RESULT {VARIANT} ontime={res.count('ON TIME')} late={res.count('LATE')} missed={res.count('MISSED')} "
          f"react_max={react.max():.1f} late_max={late.max():.1f} bad={len(bad_in_time)} rows={len(rows)} cpu={cpu:.0f}")
    return len(bad_in_time)


def compare():
    import subprocess
    names = {"A": "as the viewer now", "B": "no high priority", "C": "nothing special",
             "D": "no spin", "E": "no priority, no spin"}
    print(f"Python {sys.version.split()[0]}, {os.cpu_count()} CPU cores. 5 runs x 8 s at 190 grains/s ...")
    table = []
    for v in names:
        out = subprocess.run([sys.executable, os.path.abspath(__file__), "8", "190", v],
                             capture_output=True, text=True).stdout
        line = next((l for l in out.splitlines() if l.startswith("RESULT")), f"RESULT {v} failed")
        values = dict(x.split("=") for x in line.split()[2:]) if "=" in line else {}
        table.append((v, names[v], values))
        print(f"  {v} done")
    print()
    print(f"{'':2} {'variant':22} {'ON TIME':>8} {'LATE':>6} {'MISSED':>7} {'max reaction':>13} "
          f"{'max late':>9} {'found in time, not on time':>27} {'CPU %':>6}")
    for v, name, x in table:
        print(f"{v:2} {name:22} {x.get('ontime', '-'):>8} {x.get('late', '-'):>6} {x.get('missed', '-'):>7} "
              f"{x.get('react_max', '-'):>10} ms {x.get('late_max', '-'):>6} ms {x.get('bad', '-'):>27} "
              f"{x.get('cpu', '-'):>6}")
    print()
    print("Send me this table.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "compare":
        compare()
    else:
        sys.exit(1 if main() else 0)
