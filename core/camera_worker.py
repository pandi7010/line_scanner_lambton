"""
core/camera_worker.py - Reads ONE camera in the background.

Why a background thread?
    Reading a camera means WAITING for the next frame. If the window waited,
    it would freeze. So each camera gets its own worker thread that only does:
        apply setting changes -> read frame -> mirror (if needed)
        -> channel detection (which of the 63 channels has something in it)
        -> FrameStore

How the window talks to a worker (no PyQt5 needed here):
    worker.frames.get()                          -> newest frame
    worker.get_status()                          -> ("Streaming | ...", True, version)
    worker.get_settings()                        -> ({"frame_lines": 16, ...}, version)
    worker.request_setting("exposure_us", 120)   -> change exposure
    worker.request_setting("frame_lines", 32)    -> change frame height
    worker.activity.active(hold=0.15)            -> channels that saw something recently
    worker.set_thresholds(lower=60, upper=200)   -> change detection thresholds
The window only LEAVES a request. The worker applies it between two frames,
so only ONE thread ever talks to the camera (no clashes).

`version` numbers go up every time something changes, so the window can
check cheaply "is there anything new to show?".
"""

import threading
import time

import numpy as np

from core.channel_activity import ChannelActivity
from core.frame_store import FrameStore

RETRY_SECONDS = 3       # wait this long before trying to (re)connect a camera


class CameraWorker:

    def __init__(self, name, camera, mirror=False, detector=None):
        """
        name     : "FRONT" or "REAR"
        camera   : a hardware camera object (e.g. hardware.MindVisionCamera)
        mirror   : flip the picture left <-> right (REAR looks from the other side)
        detector : core.ChannelDetector, or None for no channel detection
        """
        self.name = name
        self.camera = camera
        self.mirror = mirror
        self.detector = detector
        self.frames = FrameStore()
        self.activity = ChannelActivity(detector.channels if detector else 63)
        self.background_level = 0        # median colour level in the chute (for lower/upper)
        self.paused = False

        # Statistics (read by the window).
        self.frame_count = 0
        self.line_count = 0
        self.frame_size = (0, 0)         # (width, lines) of the last frame
        self.brightness = 0.0            # average pixel value of the last frame (0-255)

        self._lock = threading.Lock()
        self._status = ("Waiting...", False)
        self._status_version = 0
        self._settings = {}              # last values read back from the camera
        self._settings_version = 0
        self._requests = {}              # setting changes waiting to be applied

        self._running = False
        self._thread = None

    # ------------------------------------------------------------ start/stop
    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, name=f"camera-{self.name}", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5)

    # --------------------------------------------------- used by the window
    def request_setting(self, key, value):
        """Ask for a change. Keys: "frame_lines", "exposure_us"."""
        with self._lock:
            self._requests[key] = value

    def set_thresholds(self, lower=None, upper=None):
        """Change the detection thresholds (takes effect on the next frame)."""
        if self.detector is None:
            return
        if lower is not None:
            self.detector.lower = int(lower)
        if upper is not None:
            self.detector.upper = int(upper)

    def get_status(self):
        with self._lock:
            text, ok = self._status
            return text, ok, self._status_version

    def get_settings(self):
        with self._lock:
            return dict(self._settings), self._settings_version

    # ------------------------------------------------------------ main loop
    def _run(self):
        while self._running:

            # 1) Not connected? Try to open the camera.
            if self.camera.handle is None:
                if not self._try_open():
                    self._sleep(RETRY_SECONDS)
                continue

            # 2) Did the window ask for a new frame height / exposure?
            self._apply_requests()

            # 3) Read one frame.
            try:
                frame = self.camera.read_frame(timeout_ms=2000)
            except Exception as e:       # noqa: BLE001  (camera unplugged, ...)
                self._set_status(f"Connection lost: {e}", False)
                self.camera.close()
                self._sleep(RETRY_SECONDS)
                continue

            if frame is None:            # timeout: camera alive, but no data yet
                continue

            # 4) Statistics.
            self.frame_count += 1
            self.line_count += frame.shape[0]
            self.frame_size = (frame.shape[1], frame.shape[0])
            # Average pixel value 0 (black) ... 255 (white); every 16th pixel is enough.
            self.brightness = float(frame[:, ::16].mean())

            if self.paused:
                continue
            if self.mirror:
                frame = np.ascontiguousarray(frame[:, ::-1])   # flip left <-> right

            # 5) Which channels have something in them? (EVERY frame, so no grain is missed.)
            if self.detector is not None:
                active, self.background_level = self.detector.detect(frame)
                self.activity.mark(active)

            # 6) Hand it to the screen.
            self.frames.put(frame)

        self.camera.close()
        self._set_status("Stopped", False)

    # --------------------------------------------------------------- helpers
    def _try_open(self):
        self._set_status("Connecting...", False)
        try:
            self.camera.open()
        except Exception as e:           # noqa: BLE001
            self.camera.close()
            self._set_status(f"{e}  - retrying every {RETRY_SECONDS} s", False)
            return False
        self._report_settings()
        return True

    def _apply_requests(self):
        with self._lock:
            requests, self._requests = self._requests, {}
        if not requests:
            return
        try:
            if "frame_lines" in requests:
                self.camera.set_frame_lines(int(requests["frame_lines"]))
            if "exposure_us" in requests:
                self.camera.set_exposure(float(requests["exposure_us"]))
        except Exception as e:           # noqa: BLE001
            self._set_status(f"Could not change setting: {e}", False)
        self._report_settings()

    def _report_settings(self):
        """Read the real values back from the camera, for the window."""
        try:
            settings = self.camera.get_current_settings()
        except Exception as e:           # noqa: BLE001
            print(f"[{self.name}] could not read settings: {e}")
        else:
            with self._lock:
                self._settings = settings
                self._settings_version += 1
        text = "Streaming"
        if getattr(self.camera, "info", ""):
            text += "  |  " + self.camera.info
        self._set_status(text, True)

    def _set_status(self, text, ok):
        with self._lock:
            self._status = (text, ok)
            self._status_version += 1

    def _sleep(self, seconds):
        """Sleep in small steps so stop() stays fast."""
        end = time.monotonic() + seconds
        while self._running and time.monotonic() < end:
            time.sleep(0.1)
