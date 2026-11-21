"""
core/line_logger.py - LINE LOG: the ejector status of EVERY camera line, into one CSV file.

Asked by the project lead (21 Nov 2026): work at 4800 lines/s, calculate the ejector status
line by line, log every line's status to a file so it can be analysed.

    logger = LineLogger("logs/line_log_20261121_111500.csv", {"FRONT": 4800, "REAR": 4800})
    logger.start(workers)        # every camera worker now hands each frame to the logger
    ...                          # machine runs (feeder ON or OFF)
    summary = logger.stop()      # file closed; lines per camera, lost lines

One row per line:
    camera, frame, line, camera_time_ms, missed_lines_before, ejector_status
      frame               : frame number of that camera (1, 2, 3 ... since the log started)
      line                : 1 ... 16 inside the frame (1 = oldest)
      camera_time_ms      : time of the line = line number x line time (1/4800 s = 0.2083 ms),
                            counting the lost lines too; the camera's own timestamps (0.1 ms steps)
                            are used to find lost lines. 0 = the first line logged
      missed_lines_before : lines the camera made but that never reached the PC, just before
                            this line (only filled on line 1 of a frame; normally 0)
      ejector_status      : ONE 64-bit number: bit 0 = channel 1 ... bit 62 = channel 63, bit 63 = 0;
                            a bit is 1 if that channel had something in THIS line
                            (example: channels 1 and 3 -> 5)

Speed: the camera thread only hands over the frame's table (no copying, no file work);
a separate writer thread turns it into numbers and writes the file every 0.2 s.
At 4800 lines/s x 2 cameras = 9,600 rows/s, about 0.3 MB per second.
No PyQt5 here.
"""

import threading
import time
from collections import deque

import numpy as np


def line_statuses(rows):
    """Table (lines x channels, True/False) -> one 64-bit number per line, oldest line first."""
    rows = np.asarray(rows, dtype=bool)[:, :63]
    packed = np.packbits(rows, axis=1, bitorder="little")
    padded = np.zeros((packed.shape[0], 8), dtype=np.uint8)
    padded[:, :packed.shape[1]] = packed
    return padded.view("<u8").ravel()


class LineLogger:

    def __init__(self, path, line_rates=None):
        """
        path       : CSV file to write
        line_rates : {"FRONT": 4800, ...} camera line-rate settings (lines/s) - used for the
                     line times and to count lost lines; missing / 0 = use the camera clock
        """
        self.path = path
        self.line_rates = dict(line_rates or {})
        self._queue = deque()
        self._running = False
        self._thread = None
        self._workers = {}
        self._file = None
        self.stats = {}                  # camera -> {"frames", "lines", "lost", "first", "last", "with_object"}
        self.started = None
        self.error = ""

    # -------------------------------------------------------------- start / stop
    def start(self, workers):
        self._file = open(self.path, "w", newline="", encoding="utf-8", buffering=1024 * 1024)
        self._file.write("camera,frame,line,camera_time_ms,missed_lines_before,ejector_status\n")
        self._running = True
        self.started = time.time()
        self._thread = threading.Thread(target=self._write_loop, name="line-log", daemon=True)
        self._thread.start()
        self._workers = dict(workers)
        for w in self._workers.values():
            w.line_logger = self
        return self

    def stop(self):
        for w in self._workers.values():
            if getattr(w, "line_logger", None) is self:
                w.line_logger = None
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=10)
        if self._file is not None:
            self._file.close()
            self._file = None
        return self.summary()

    @property
    def running(self):
        return self._running

    # -------------------------------------------------------------- camera thread
    def add(self, camera, camera_s, line_time_s, rows):
        """Called by the camera worker for every frame (cheap: only a reference is kept)."""
        if self._running and camera_s is not None:
            self._queue.append((camera, camera_s, line_time_s, rows))

    # -------------------------------------------------------------- writer thread
    def _write_loop(self):
        line_no = {}                     # camera -> number of the next line (lost lines included)
        last_end = {}
        while self._running or self._queue:
            out = []
            while self._queue:
                camera, end_s, measured_lt, rows = self._queue.popleft()
                st = self.stats.setdefault(camera, {"frames": 0, "lines": 0, "lost": 0, "first": None,
                                                   "last": None, "with_object": 0})
                rate = self.line_rates.get(camera)
                lt = 1.0 / rate if rate and rate > 0 else measured_lt
                n = len(rows)
                first_line_s = end_s - (n - 1) * lt
                missed = 0
                if camera in last_end:
                    missed = max(0, int(round((first_line_s - last_end[camera]) / lt)) - 1)
                last_end[camera] = end_s
                st["frames"] += 1
                st["lines"] += n
                st["lost"] += missed
                st["first"] = st["first"] if st["first"] is not None else first_line_s
                st["last"] = end_s
                statuses = line_statuses(rows)
                st["with_object"] += int(np.count_nonzero(statuses))
                start = line_no.get(camera, 0) + missed      # lost lines still take their time
                line_no[camera] = start + n
                step = lt * 1000.0
                base = start * step
                frame_no = st["frames"]
                for k in range(n):
                    out.append(f"{camera},{frame_no},{k + 1},{base + k * step:.4f},"
                               f"{missed if k == 0 else 0},{int(statuses[k])}\n")
            if out:
                try:
                    self._file.write("".join(out))
                except Exception as exc:          # noqa: BLE001  (disk full ...)
                    self.error = str(exc)
                    self._running = False
            if self._running:
                time.sleep(0.2)

    # -------------------------------------------------------------- summary
    def summary(self):
        """{camera: {...}} + text lines for a message box."""
        result, text = {}, []
        for camera, st in self.stats.items():
            rate = self.line_rates.get(camera)
            span = (st["last"] - st["first"]) if st["first"] is not None else 0.0
            expected = st["lines"] + st["lost"]
            pct = 100.0 * st["lost"] / expected if expected else 0.0
            result[camera] = dict(st, seconds=span, lost_pct=pct, line_rate=rate)
            text.append(f"{camera}: {st['lines']:,} lines in {st['frames']:,} frames ({span:.1f} s camera time), "
                        f"lost {st['lost']:,} ({pct:.3f} %), lines with something in them: {st['with_object']:,}")
        if self.error:
            text.append(f"WRITE ERROR: {self.error}")
        result["text"] = "\n".join(text)
        return result
