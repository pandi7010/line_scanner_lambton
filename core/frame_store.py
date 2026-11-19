"""
core/frame_store.py - Keeps the NEWEST frame of one camera.

The camera thread puts frames in (hundreds per second).
The screen takes the newest one out (25 times per second).
Older frames are simply replaced - the screen only needs the latest.

    store = FrameStore()
    store.put(frame)                 # camera thread
    frame, number = store.get()      # screen; `number` goes up by 1 per new frame
"""

import threading


class FrameStore:

    def __init__(self):
        self._frame = None
        self._number = 0                 # how many frames were put in so far
        self._lock = threading.Lock()    # one thread writes, another reads

    def put(self, frame):
        """Store a new frame. The frame must not be changed afterwards."""
        with self._lock:
            self._frame = frame
            self._number += 1

    def get(self):
        """(newest frame or None, frame number)."""
        with self._lock:
            return self._frame, self._number
