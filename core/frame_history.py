"""
core/frame_history.py - Keeps the last ~0.6 s of camera frames, to cut out OBJECT SNAPS.

Why: when an object's log row is written (about 30 - 50 ms after it touched the
decision line), the Logs window should be able to show a small picture of exactly
that object, as FRONT and REAR saw it. The frames of that moment are still here.

    history = FrameHistory(seconds=0.6)
    history.add(frame_end, line_time, frame)        # camera thread, every frame (no copy)
    snap = history.crop(t_from, t_to, 1000, 1060)   # -> Snap (picture + where it was cut)

A snap is cut from the lines between t_from and t_to (+ a few lines before/after)
and from the pixels px_from .. px_to (+ some pixels left/right). Several frames are
stacked: oldest line at the top, newest at the bottom.

Memory: one 4096 x 16 frame = 196 KB; 0.6 s at 300 frames/s = 35 MB per camera.
No PyQt5 here.
"""

import math
import threading
from collections import deque

import numpy as np


class Snap:
    """A small picture of one object from one camera."""

    __slots__ = ("image", "x0", "px_from", "px_to", "seen")

    def __init__(self, image, x0, px_from, px_to, seen):
        self.image = image        # BGR, lines x pixels x 3 (oldest line at the top)
        self.x0 = x0              # camera pixel of the picture's left edge
        self.px_from = px_from    # object's first / last pixel (camera pixels)
        self.px_to = px_to
        self.seen = seen          # True = this camera saw the object, False = cut at the other camera's place


class FrameHistory:

    def __init__(self, seconds=0.6):
        self.seconds = seconds
        self._frames = deque()           # (frame_end, line_time, frame)
        self._lock = threading.Lock()

    def add(self, frame_end, line_time, frame):
        with self._lock:
            self._frames.append((frame_end, line_time, frame))
            while self._frames and self._frames[0][0] < frame_end - self.seconds:
                self._frames.popleft()

    def crop(self, t_from, t_to, px_from, px_to, margin_px=30, margin_lines=8, max_lines=300, seen=True):
        """Picture of pixels px_from..px_to between the times t_from..t_to, or None if too old."""
        frames = []
        with self._lock:                 # newest first, stop at the first frame that is too old
            for item in reversed(self._frames):
                frame_end, lt, frame = item
                if frame_end < t_from - (margin_lines + frame.shape[0]) * lt:
                    break
                frames.append(item)
        frames.reverse()
        pieces, x0 = [], 0
        for frame_end, lt, frame in frames:
            n, width = frame.shape[:2]
            first = frame_end - (n - 1) * lt              # time of the frame's oldest line
            lo, hi = t_from - margin_lines * lt, t_to + margin_lines * lt
            if frame_end < lo or first > hi:
                continue
            r0 = max(0, math.ceil((lo - first) / lt - 1e-6))
            r1 = min(n, math.floor((hi - first) / lt + 1e-6) + 1)
            x0 = max(0, int(px_from) - margin_px)
            x1 = min(width, int(px_to) + margin_px + 1)
            if r1 > r0 and x1 > x0:
                pieces.append(frame[r0:r1, x0:x1])
        if not pieces:
            return None
        image = np.ascontiguousarray(np.concatenate(pieces)[:max_lines])
        return Snap(image, x0, int(px_from), int(px_to), seen)
