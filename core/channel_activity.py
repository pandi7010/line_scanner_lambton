"""
core/channel_activity.py - Follows each of the 63 channels LINE BY LINE.

The camera worker gives it, for every frame, a table "which channel has something in
which line" (core/channel_detector.py detect_rows) plus the exact time of every line
(core/camera_clock.py). For each channel it keeps:

    start_time  : exact time of the line where the current object STARTED
                  (= the moment it touched the decision line)
    found_time  : PC time when the program found that object (to measure the PC reaction)
    start_count : how many objects started so far -> lets others notice NEW objects
    last_seen   : time of the last line with something in it
    open        : is the object still there?
    pixel range : first / last pixel of the current object

When does an object END?  After `end_gap` empty lines in a row (default 2).
So two grains in the same channel with a gap of 3 or more background lines are
two objects - even inside one frame.

Clock: time.perf_counter() (precise to microseconds).
No PyQt5 here.

    activity = ChannelActivity(63)
    activity.mark_rows(rows, line_times, found_time, pixels)    # camera thread, every frame
    activity.mark({5, 6})                                        # simple form: one "line", now
"""

import threading
import time

import numpy as np


class ChannelActivity:

    def __init__(self, channels=63, end_gap=2):
        self.channels = channels
        self.end_gap = end_gap
        self._last_seen = np.full(channels, -1e9)        # index 0 = channel 1
        self._start_time = np.full(channels, -1e9)
        self._found_time = np.full(channels, -1e9)
        self._start_count = np.zeros(channels, dtype=np.int64)
        self._open = np.zeros(channels, dtype=bool)
        self._gap = np.zeros(channels, dtype=np.int64)   # empty lines since the object was last seen
        self._hits = np.zeros(channels, dtype=np.int64)  # lines each channel had something
        self._pixel_from = np.full(channels, np.nan)
        self._pixel_to = np.full(channels, np.nan)
        self._lock = threading.Lock()

    # ------------------------------------------------------- camera thread
    def mark_rows(self, rows, line_times, found_time, pixels=None, end_gap=None):
        """
        rows       : True/False table (lines x channels) from ChannelDetector.detect_rows
        line_times : time of every line (from CameraClock.line_times)
        found_time : PC time when this frame was checked
        pixels     : {channel: (first pixel, last pixel)} from the detector
        """
        end_gap = self.end_gap if end_gap is None else end_gap
        rows = np.asarray(rows, dtype=bool)
        with self._lock:
            for r in range(rows.shape[0]):
                on = rows[r]
                t = float(line_times[r])
                new = on & ~self._open                      # OFF -> ON: a new object starts here
                if new.any():
                    self._start_time[new] = t
                    self._found_time[new] = found_time
                    self._start_count[new] += 1
                    self._pixel_from[new] = np.nan
                    self._pixel_to[new] = np.nan
                    self._open |= new
                self._last_seen[on] = t
                self._hits[on] += 1
                self._gap[on] = 0
                waiting = self._open & ~on                  # object open, but this line is empty
                self._gap[waiting] += 1
                ended = waiting & (self._gap > end_gap)
                self._open[ended] = False
                self._gap[ended] = 0

            seen_in_frame = rows.any(axis=0)
            for ch, (first, last) in (pixels or {}).items():
                i = ch - 1
                if 0 <= i < self.channels and seen_in_frame[i]:
                    self._pixel_from[i] = np.fmin(self._pixel_from[i], first)
                    self._pixel_to[i] = np.fmax(self._pixel_to[i], last)

    def mark(self, active_channels, now=None, pixels=None):
        """Simple form: the whole frame is ONE line at time `now` (objects end at once when gone)."""
        now = time.perf_counter() if now is None else now
        row = np.zeros((1, self.channels), dtype=bool)
        for ch in active_channels:
            if 1 <= ch <= self.channels:
                row[0, ch - 1] = True
        self.mark_rows(row, [now], now, pixels, end_gap=0)

    # -------------------------------------------------------------- readers
    def active(self, hold=0.15, now=None):
        """Channel numbers (1..63) that had something within the last `hold` seconds."""
        now = time.perf_counter() if now is None else now
        with self._lock:
            recent = np.flatnonzero(now - self._last_seen <= hold)
        return {int(i) + 1 for i in recent}

    def snapshot(self):
        """
        Copies, index 0 = channel 1:
        (last_seen, start_time, start_count, pixel_from, pixel_to, found_time, open)
        """
        with self._lock:
            return (self._last_seen.copy(), self._start_time.copy(), self._start_count.copy(),
                    self._pixel_from.copy(), self._pixel_to.copy(), self._found_time.copy(),
                    self._open.copy())

    def hits(self):
        """Copy of the hit counters (index 0 = channel 1)."""
        with self._lock:
            return self._hits.copy()
