"""
core/frame_check.py - "Frame check": are ALL frames and lines processed, and how fast?

Asked by the project lead (20 + 21 Nov 2026): for 1000 frames, make sure that
  1. 1000 ejector statuses are calculated - one per frame, as ONE 64-bit NUMBER:
     bit 0 = channel 1 ... bit 62 = channel 63 (bit 63 = 0); bit = 1 if that channel had
     something in the frame (ChannelDetector.detect_rows -> core.camera_worker.ejector_status)
  2. no lines and no frames are missed (also: does a SLOWER line rate reduce the loss?)
  3. we know how long one frame needs (process + ejector status), and the line-to-line time
  4. the time per frame is CONSISTENT (the same every frame, no outliers)
  + separate experiment: the ejector status of EVERY LINE (16 numbers per frame), per_line=True

How it is measured, per camera (the camera thread calls record() for every frame):
  * frames received        : frames the camera SDK gave us
  * ejector status computed: frames that went through detect_rows + mark_rows
  * line rate              : the camera's SETTING (lines/s) -> line-to-line time = 1 / line rate.
                             Setting "max" (0) -> the line time measured from the camera clock.
  * lines expected         : camera clock time from the first to the last frame x line rate
                             (+ the lines of the first frame). Uses the camera's OWN RAW timestamps.
  * lines lost             : expected - received  (whether frames were dropped or the camera paused)
  * frames lost            : gaps between frames of 2 or more frame times (camera clock)
  * processing time        : "frame arrived in the PC" -> "ejector status done + frame handed to
                             the screen" (perf_counter). Budget = one frame time.
Consistency OK = no frame over budget and 99.9 % of frames within +-1 ms of the median.
write_csv() = every frame; write_lines_csv() = every line (per_line experiment).
No PyQt5 here.
"""

import csv
import time

import numpy as np


class FrameCheck:

    def __init__(self, frames=1000, name="", line_rate_setting=None, per_line=False):
        self.name = name
        self.target = int(frames)
        self.line_rate_setting = line_rate_setting      # lines/s, 0 / None = maximum (unknown)
        self.per_line = per_line
        self.received = 0
        self.status_done = 0
        self.paused = 0
        self.lines = 0
        self.proc_s = []
        self.records = []                # every frame: (processing s, camera time s or None, lines, status or None)
        self.arrivals = []               # PC time (perf_counter) when each frame arrived
        self.line_statuses = []          # per_line: list of per-line status lists (one list per frame)
        self.frame_ends = []
        self.frame_lines = []
        self.sdk_lost_start = None
        self.sdk_lost_end = None
        self.camera_clock = True
        self.started = time.perf_counter()
        self.finished = None
        self.done = False

    def record(self, proc_s, frame_end, lines, status, paused, sdk_lost, camera_clock=True, arrived=None,
               line_statuses=None):
        """Camera thread: one frame finished. frame_end = the camera's OWN clock (seconds) or None."""
        if self.done:
            return
        if self.sdk_lost_start is None:
            self.sdk_lost_start = sdk_lost
        self.camera_clock = camera_clock
        self.received += 1
        self.lines += lines
        self.paused += bool(paused)
        self.status_done += status is not None
        self.proc_s.append(proc_s)
        self.records.append((proc_s, frame_end, lines, status))
        self.arrivals.append(arrived)
        if self.per_line:
            self.line_statuses.append(line_statuses)
        if frame_end is not None:
            self.frame_ends.append(frame_end)
            self.frame_lines.append(lines)
        if self.received >= self.target:
            self.sdk_lost_end = sdk_lost
            self.finished = time.perf_counter()
            self.done = True

    # ------------------------------------------------------------- timing
    def line_time_s(self):
        """Line-to-line time (s): from the line-rate SETTING, else from the camera clock."""
        if self.line_rate_setting and self.line_rate_setting > 0:
            return 1.0 / self.line_rate_setting, "camera setting"
        ends = np.array(self.frame_ends)
        if len(ends) >= 3:
            gaps = np.diff(ends)
            typical = float(np.percentile(gaps, 10))        # normal frame-to-frame time (0.1 ms steps)
            frames_spanned = np.floor(gaps / typical + 0.25).sum()   # incl. missing frames
            frame = (ends[-1] - ends[0]) / max(1.0, frames_spanned)  # exact average frame time
            return frame / float(np.median(self.frame_lines)), "measured (camera clock)"
        return None, "unknown"

    def report(self):
        proc = np.array(self.proc_s) * 1000.0 if self.proc_s else np.zeros(1)
        ends = np.array(self.frame_ends)
        lines_per_frame = int(np.median(self.frame_lines)) if self.frame_lines else 16
        line_s, line_source = self.line_time_s()
        frame_s = None if line_s is None else line_s * lines_per_frame
        missed_frames = expected_lines = span_s = None
        if len(ends) >= 3 and frame_s:
            gaps = np.diff(ends)
            span_s = float(ends[-1] - ends[0])
            # a frame is missing only when the gap is ~2 frame times or more (camera clock 0.1 ms steps)
            missed_frames = int(np.clip(np.floor(gaps / frame_s + 0.25) - 1, 0, None).sum())
            expected_lines = int(round(span_s / line_s)) + int(self.frame_lines[0])
        seconds = (self.finished or time.perf_counter()) - self.started
        budget = (frame_s or 0.0) * 1000
        lines_lost = None if expected_lines is None else max(0, expected_lines - self.lines)
        return {
            "camera": self.name,
            "frames_received": self.received,
            "status_calculated": self.status_done,
            "frames_with_object": sum(1 for rec in self.records if rec[3]),
            "paused_frames": self.paused,
            "line_rate_setting": self.line_rate_setting,
            "line_time_us": None if line_s is None else line_s * 1e6,
            "line_time_source": line_source,
            "effective_line_rate": (self.lines / span_s) if span_s else None,
            "missed_frames_camera_clock": missed_frames,
            "missed_frames_sdk": (None if self.sdk_lost_start is None or self.sdk_lost_end is None
                                  else self.sdk_lost_end - self.sdk_lost_start),
            "lines_received": self.lines,
            "lines_expected": expected_lines,
            "lines_missed": lines_lost,
            "lines_missed_pct": (100.0 * lines_lost / expected_lines) if expected_lines else None,
            "frame_time_ms": budget or None,
            "proc_avg_ms": float(proc.mean()),
            "proc_median_ms": float(np.median(proc)),
            "proc_p95_ms": float(np.percentile(proc, 95)),
            "proc_p99_ms": float(np.percentile(proc, 99)),
            "proc_max_ms": float(proc.max()),
            "proc_per_line_us": float(proc.mean()) * 1000 / lines_per_frame,
            "proc_per_line_p99_us": float(np.percentile(proc, 99)) * 1000 / lines_per_frame,
            "over_budget": int((proc > budget).sum()) if budget else None,
            "proc_min_ms": float(proc.min()),
            "proc_std_ms": float(proc.std()),
            "proc_p999_ms": float(np.percentile(proc, 99.9)),
            "within_1ms_of_median": float(np.mean(np.abs(proc - np.median(proc)) <= 1.0)) * 100,
            "bands": {label: int(((proc >= lo) & (proc < hi)).sum()) for label, lo, hi in (
                ("< 0.5 ms", 0, 0.5), ("0.5 - 1 ms", 0.5, 1), ("1 - 2 ms", 1, 2),
                ("2 ms - 1 frame", 2, max(2.0, budget or 2.0)), ("> 1 frame", max(2.0, budget or 2.0), 1e9))},
            "seconds": seconds,
            "fps": self.received / seconds if seconds > 0 else 0.0,
            "camera_clock": self.camera_clock,
        }

    def text(self):
        r = self.report()

        def ok(good):
            return "OK " if good else "NOT OK"

        n = r["frames_received"]
        setting = r["line_rate_setting"]
        setting_text = "max" if not setting or setting <= 0 else f"{setting:,} lines/s"
        lines = [f"=== {r['camera']} camera: {n} frames in {r['seconds']:.2f} s ({r['fps']:.1f} frames/s) ==="]
        lt = r["line_time_us"]
        lines.append(f"   Line rate setting: {setting_text}   line-to-line time: "
                     + (f"{lt:.1f} us ({r['line_time_source']})" if lt else "unknown")
                     + (f"   effective (incl. losses): {r['effective_line_rate']:,.0f} lines/s"
                        if r["effective_line_rate"] else ""))
        lines.append(f"1. Ejector status (64-bit number) calculated: {r['status_calculated']} of {n} frames   "
                     f"[{ok(r['status_calculated'] == n)}]   frames with something in them: {r['frames_with_object']}"
                     + (f"  ({r['paused_frames']} frames while Pause was on)" if r["paused_frames"] else ""))
        mf, ms, lm = r["missed_frames_camera_clock"], r["missed_frames_sdk"], r["lines_missed"]
        if lm is not None:
            lines.append(f"2. Lines: received {r['lines_received']:,}, expected {r['lines_expected']:,} "
                         f"-> LOST {lm:,} ({r['lines_missed_pct']:.2f} %)   [{ok(lm == 0)}]")
            lines.append(f"   Frames lost (gap of 2+ frame times, camera clock): {mf}   camera SDK counter: {ms}")
        else:
            lines.append("2. Lines / frames lost: not enough frames to tell")
        if not r["camera_clock"]:
            lines.append("   (camera timestamps are OFF - lost lines cannot be counted exactly)")
        budget = r["frame_time_ms"] or 0
        lines.append(f"3. Time per frame (arrived -> ejector status done): average {r['proc_avg_ms']:.3f} ms, "
                     f"median {r['proc_median_ms']:.3f}, 95 % {r['proc_p95_ms']:.3f}, "
                     f"99 % {r['proc_p99_ms']:.3f}, max {r['proc_max_ms']:.3f} ms")
        lines.append(f"   Per line: {r['proc_per_line_us']:.1f} us on average, {r['proc_per_line_p99_us']:.1f} us (99 %)"
                     + (f" - the camera gives a new line every {lt:.1f} us" if lt else ""))
        lines.append(f"   Budget = one frame = {budget:.2f} ms.  Frames over budget: {r['over_budget']} of {n}   "
                     f"[{ok((r['over_budget'] or 0) == 0)}]")
        consistent = (r["over_budget"] or 0) == 0 and r["within_1ms_of_median"] >= 99.9
        lines.append(f"4. Consistency: min {r['proc_min_ms']:.3f} ms, max {r['proc_max_ms']:.3f} ms, "
                     f"standard deviation {r['proc_std_ms']:.3f} ms, 99.9 % below {r['proc_p999_ms']:.3f} ms")
        lines.append(f"   Frames within +-1 ms of the median: {r['within_1ms_of_median']:.2f} %   "
                     f"[{ok(consistent)}]  (OK = 99.9 % or more, and none over budget)")
        lines.append("   Time bands: " + ",  ".join(f"{k}: {v}" for k, v in r["bands"].items()))
        return "\n".join(lines)

    # ---------------------------------------------------------------- CSV
    def write_csv(self, path, append=False):
        """
        Every frame, one row:
          camera, frame (1..1000), camera_time_ms (camera's RAW clock, from the first frame),
          gap_to_previous_frame_ms (camera clock), missed_frames_before (frames missing just before),
          pc_arrived_ms (PC clock, from the first frame), pc_gap_ms (PC clock),
          lines, processing_ms, ejector_status (ONE 64-bit number, bit 0 = channel 1)
        """
        recs = self.records
        line_s, _ = self.line_time_s()
        frame_s = None if line_s is None else line_s * (int(np.median(self.frame_lines)) if self.frame_lines else 16)
        ends = [e for _, e, _, _ in recs if e is not None]
        t0 = ends[0] if ends else None
        a0 = next((a for a in self.arrivals if a is not None), None)
        with open(path, "a" if append else "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if not append:
                w.writerow(["camera", "frame", "camera_time_ms", "gap_to_previous_frame_ms", "missed_frames_before",
                            "pc_arrived_ms", "pc_gap_ms", "lines", "processing_ms", "ejector_status"])
            previous = None
            for i, (proc, end, lines, status) in enumerate(recs):
                gap = missed = ""
                if end is not None and previous is not None:
                    gap = round((end - previous) * 1000, 3)
                    missed = max(0, int(np.floor((end - previous) / frame_s + 0.25)) - 1) if frame_s else ""
                arr = self.arrivals[i] if i < len(self.arrivals) else None
                prev_arr = self.arrivals[i - 1] if 0 < i <= len(self.arrivals) else None
                w.writerow([self.name, i + 1, "" if end is None or t0 is None else round((end - t0) * 1000, 3), gap,
                            missed, "" if arr is None or a0 is None else round((arr - a0) * 1000, 3),
                            "" if arr is None or prev_arr is None else round((arr - prev_arr) * 1000, 3),
                            lines, round(proc * 1000, 4), "" if status is None else status])
                if end is not None:
                    previous = end

    def write_lines_csv(self, path, append=False):
        """
        Separate experiment (per_line): EVERY LINE, one row:
          camera, frame, line (1..16, oldest first), line_time_ms (camera clock, from the first frame),
          line_status (ONE 64-bit number for this line, bit 0 = channel 1)
        Returns the number of rows written (0 if per_line was off).
        """
        if not self.per_line:
            return 0
        line_s, _ = self.line_time_s()
        ends = [e for _, e, _, _ in self.records if e is not None]
        t0 = ends[0] if ends else None
        rows = 0
        with open(path, "a" if append else "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if not append:
                w.writerow(["camera", "frame", "line", "line_time_ms", "line_status"])
            for i, ((_, end, lines, _), statuses) in enumerate(zip(self.records, self.line_statuses)):
                if not statuses:
                    continue
                n = len(statuses)
                for k, status in enumerate(statuses):
                    t = ("" if end is None or t0 is None or line_s is None
                         else round((end - (n - 1 - k) * line_s - t0) * 1000, 4))
                    w.writerow([self.name, i + 1, k + 1, t, status])
                    rows += 1
        return rows
