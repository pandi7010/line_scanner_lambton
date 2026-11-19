"""
core/channel_activity.py - Remembers WHEN each channel saw something.

The camera worker calls  mark(active_channels)  for EVERY frame.
For each of the 63 channels this keeps:

    last_seen   : time the channel was last ON              -> LED hold (below)
    start_time  : time the channel last switched OFF -> ON  -> "a new object arrived"
    start_count : how many times it switched OFF -> ON      -> lets others notice NEW objects

Why the LED hold?
    A grain passes the camera in a few milliseconds (a few frames), but the
    screen is only redrawn 25 times per second (every 40 ms). So the LED stays
    lit for `hold` seconds after last_seen (default 0.15 s) - long enough to see.

    activity = ChannelActivity(63)
    activity.mark({5, 6})              # camera thread, every frame
    activity.active(hold=0.15)         # screen: {5, 6} for the next 0.15 s, then set()

Clock: time.perf_counter() (precise to microseconds). Python's time.monotonic()
on Windows (Python 3.11) only ticks every ~15.6 ms - far too coarse here.
"""

import threading
import time

import numpy as np


class ChannelActivity:

    def __init__(self, channels=63):
        self.channels = channels
        self._last_seen = np.full(channels, -1e9)        # index 0 = channel 1
        self._start_time = np.full(channels, -1e9)
        self._start_count = np.zeros(channels, dtype=np.int64)
        self._hits = np.zeros(channels, dtype=np.int64)  # frames each channel was ON
        self._previous = set()                           # channels ON in the previous frame
        self._lock = threading.Lock()

    def mark(self, active_channels, now=None):
        """Call for EVERY frame, also when nothing is ON (needed to see OFF -> ON)."""
        now = time.perf_counter() if now is None else now
        active = {ch for ch in active_channels if 1 <= ch <= self.channels}
        with self._lock:
            new_objects = active - self._previous         # switched OFF -> ON
            self._previous = active
            if active:
                idx = [ch - 1 for ch in active]
                self._last_seen[idx] = now
                self._hits[idx] += 1
            if new_objects:
                idx = [ch - 1 for ch in new_objects]
                self._start_time[idx] = now
                self._start_count[idx] += 1

    def active(self, hold=0.15, now=None):
        """Channel numbers (1..63) that were ON within the last `hold` seconds."""
        now = time.perf_counter() if now is None else now
        with self._lock:
            recent = np.flatnonzero(now - self._last_seen <= hold)
        return {int(i) + 1 for i in recent}

    def snapshot(self):
        """Copies of (last_seen, start_time, start_count) - index 0 = channel 1."""
        with self._lock:
            return self._last_seen.copy(), self._start_time.copy(), self._start_count.copy()

    def hits(self):
        """Copy of the hit counters (index 0 = channel 1)."""
        with self._lock:
            return self._hits.copy()
