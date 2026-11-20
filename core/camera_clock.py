"""
core/camera_clock.py - WHEN was each camera line really scanned?

Why:
    The PC gets a frame a few ms AFTER its lines were scanned (the frame must be full,
    then it travels over the network). So "the moment the PC saw it" is too late.
    The camera has its own clock and writes the time into every frame. With that we
    can work out the real moment each line was scanned = the moment an object
    touched the decision line.

How (the same method as lambton-main capture_timing.py):
    1. camera clock -> seconds: the timestamp counts in 0.1 ms steps (32-bit, it wraps
       around after ~5 days; we handle that).
    2. line time: (time between two frames) / (lines per frame), smoothed.
       Example: frames 3.32 ms apart, 16 lines  ->  0.2075 ms per line (4,819 lines/s)
    3. camera clock -> PC clock: offset = PC arrival time - camera time.
       Network/Windows delays only ever make the offset BIGGER, so the SMALLEST offset
       of the last 300 frames is the best one (the "fastest" frame).
    4. the timestamp is the END of the frame (newest line). Line r of a frame with
       N lines was scanned at   frame_end - (N - 1 - r) * line_time

    clock = CameraClock()
    frame_end, line_time = clock.update(camera_ts, arrived, lines)
    times = clock.line_times(frame_end, line_time, lines)    # PC time of every line

If a camera gives no timestamp (e.g. a test), the PC arrival time is used instead.
No PyQt5 here.
"""

from collections import deque

import numpy as np

TS_UNIT_S = 0.0001              # camera timestamp unit: 0.1 ms
WRAP = 2 ** 32                  # the timestamp is a 32-bit number


class CameraClock:

    def __init__(self, default_line_rate_hz=4800.0, offset_window_frames=300, use_camera_timestamp=True):
        self.default_line_time = 1.0 / max(1.0, float(default_line_rate_hz))
        self.use_camera_timestamp = use_camera_timestamp
        self._offsets = deque(maxlen=int(offset_window_frames))
        self._last_raw = None
        self._camera_s = 0.0             # camera time in seconds (wrap removed)
        self.line_time = None            # seconds per line, measured
        self.using_camera_clock = False  # True once camera timestamps are being used
        self.missed_frames = 0           # frames the program never got (gaps in the camera clock)

    def update(self, camera_ts, arrived, lines):
        """
        camera_ts : raw camera timestamp of this frame, or None
        arrived   : PC time (perf_counter) when the frame arrived
        lines     : lines in this frame
        Returns (PC time of the frame's NEWEST line, seconds per line).
        """
        lines = max(1, int(lines))
        if not self.use_camera_timestamp or camera_ts is None:
            self.using_camera_clock = False
            return arrived, self.line_time or self.default_line_time

        raw = int(camera_ts) % WRAP
        if self._last_raw is None:
            self._camera_s = raw * TS_UNIT_S
        else:
            step = ((raw - self._last_raw) % WRAP) * TS_UNIT_S      # also correct over the wrap
            self._camera_s += step
            # A gap of 2+ frame times in the camera clock = frames that never reached us.
            if self.line_time is not None and 0 < step < 1.0:
                frame_time = lines * self.line_time
                if step > 1.5 * frame_time:
                    self.missed_frames += int(round(step / frame_time)) - 1
            if 0 < step < 1.0:
                lt = step / lines
                if 0.2 * self.default_line_time < lt < 5.0 * self.default_line_time:
                    self.line_time = lt if self.line_time is None else 0.9 * self.line_time + 0.1 * lt
        self._last_raw = raw

        self._offsets.append(arrived - self._camera_s)
        self.using_camera_clock = True
        frame_end = self._camera_s + min(self._offsets)
        return frame_end, self.line_time or self.default_line_time

    @staticmethod
    def line_times(frame_end, line_time, lines):
        """PC time of every line of the frame (index 0 = oldest line, last = newest)."""
        return frame_end - (lines - 1 - np.arange(lines)) * line_time
