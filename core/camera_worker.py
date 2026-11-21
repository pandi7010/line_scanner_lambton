"""
core/camera_worker.py - Reads ONE camera in the background.

Why a background thread?
    Reading a camera means WAITING for the next frame. If the window waited,
    it would freeze. So each camera gets its own worker thread that only does:
        apply setting changes -> read frame -> exact line times (camera clock)
        -> mirror (if needed) -> line-by-line channel detection -> FrameStore

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

from core import realtime
from core.camera_clock import CameraClock
from core.channel_activity import ChannelActivity
from core.frame_check import FrameCheck
from core.frame_history import FrameHistory
from core.frame_store import FrameStore

RETRY_SECONDS = 3       # wait this long before trying to (re)connect a camera


def ejector_status(rows):
    """
    The ejector status of one frame as ONE 64-bit number:
    bit 0 = channel 1, bit 1 = channel 2, ... bit 62 = channel 63, bit 63 = always 0.
    A bit is 1 if that channel had something in ANY line of the frame.
    Example: something in channels 1 and 3 -> 0b101 = 5.
    """
    channels = np.asarray(rows, dtype=bool).any(axis=0)[:63]
    packed = np.packbits(channels, bitorder="little")          # 8 bytes, channel 1 = lowest bit
    return int.from_bytes(packed.tobytes(), "little")


def line_statuses(rows):
    """Separate experiment: the ejector status of EVERY LINE - one 64-bit number per line
    (bit 0 = channel 1 ... bit 62 = channel 63), oldest line first."""
    rows = np.asarray(rows, dtype=bool)[:, :63]
    packed = np.packbits(rows, axis=1, bitorder="little")        # lines x 8 bytes
    padded = np.zeros((packed.shape[0], 8), dtype=np.uint8)
    padded[:, :packed.shape[1]] = packed
    return padded.view("<u8").ravel().tolist()


def mirror_frame(frame):
    """
    Flip left <-> right, 3x faster than frame[:, ::-1] (0.08 instead of 0.24 ms per
    4096 x 16 frame): the 3 colour bytes of a pixel are moved as ONE item.
    """
    frame = np.ascontiguousarray(frame)
    lines, width = frame.shape[:2]
    if frame.ndim == 3 and frame.shape[2] == 3 and frame.dtype == np.uint8:
        pixels = frame.reshape(lines, width * 3).view("V3")          # one item = one pixel (B, G, R)
        return pixels[:, ::-1].copy().view(np.uint8).reshape(lines, width, 3)
    return np.ascontiguousarray(frame[:, ::-1])


class CameraWorker:

    def __init__(self, name, camera, mirror=False, detector=None, clock=None, end_gap=2, snap_seconds=0.6):
        """
        name     : "FRONT" or "REAR"
        camera   : a hardware camera object (e.g. hardware.MindVisionCamera)
        mirror   : flip the picture left <-> right (REAR looks from the other side)
        detector : core.ChannelDetector, or None for no channel detection
        clock    : core.CameraClock (camera timestamps -> exact line times); None = a new one
        end_gap  : an object ends after this many empty lines
        snap_seconds : keep this many seconds of frames for object snaps (0 = no snaps)
        """
        self.name = name
        self.camera = camera
        self.mirror = mirror
        self.detector = detector
        self.clock = clock if clock is not None else CameraClock()
        self.frames = FrameStore()
        self.history = FrameHistory(snap_seconds) if snap_seconds > 0 else None   # for object snaps
        self.activity = ChannelActivity(detector.channels if detector else 63, end_gap=end_gap)
        self.background_level = 0        # median colour level in the chute (for lower/upper)
        self.paused = False
        self.frame_check = None          # core.FrameCheck while a "Frame check" is running
        self.line_logger = None          # core.LineLogger while the "Line log" is recording

        # Statistics (read by the window).
        self.frame_count = 0
        self.line_count = 0
        self.frame_size = (0, 0)         # (width, lines) of the last frame
        self.brightness = 0.0            # average pixel value of the last frame (0-255)
        self.sdk_lost = 0                # camera SDK's own "lost frames" counter (if it has one)
        self.cpu_percent = 0.0           # CPU used by THIS thread (100 = one full core)
        self._cpu_mark = None
        self._next_stats = 0.0

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
        """Ask for a change. Keys: "frame_lines", "exposure_us", "line_rate" (lines/s, 0 = max)."""
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
        realtime.boost_this_thread(f"{self.name} camera")   # Windows: run this thread first
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
                arrived = time.perf_counter()
            except Exception as e:       # noqa: BLE001  (camera unplugged, ...)
                self._set_status(f"Connection lost: {e}", False)
                self.camera.close()
                self._sleep(RETRY_SECONDS)
                continue

            if frame is None:            # timeout: camera alive, but no data yet
                continue

            # 4) Statistics.
            self._measure_cpu()
            if time.monotonic() >= self._next_stats and hasattr(self.camera, "frame_statistics"):
                self._next_stats = time.monotonic() + 1.0          # once per second is enough
                try:
                    stats = self.camera.frame_statistics()
                    if stats:
                        self.sdk_lost = stats["lost"]
                except Exception:        # noqa: BLE001
                    pass
            self.frame_count += 1
            self.line_count += frame.shape[0]
            self.frame_size = (frame.shape[1], frame.shape[0])
            # Average pixel value 0 (black) ... 255 (white), only for the info line:
            # once per second (every 300th frame), every 16th pixel is enough.
            if self.frame_count % 300 == 1:
                self.brightness = float(frame[:, ::16].mean())

            if self.paused:
                self._check_frame(arrived, None, frame.shape[0], status=None, paused=True)
                continue
            if self.mirror:
                frame = mirror_frame(frame)                      # flip left <-> right

            # 5) Exact time of every line (camera clock), then: which channels have
            #    something in WHICH line? (EVERY line of EVERY frame, so no grain is missed.)
            frame_end = None
            status = None                # ejector status of this frame: ONE 64-bit number
            per_line = None
            if self.detector is not None:
                info = getattr(self.camera, "last_frame_info", None) or {}
                lines = frame.shape[0]
                frame_end, line_time = self.clock.update(info.get("camera_ts"),
                                                         info.get("arrived", arrived), lines)
                line_times = self.clock.line_times(frame_end, line_time, lines)
                if self.history is not None:
                    self.history.add(frame_end, line_time, frame)     # no copy: just keep it
                rows, self.background_level, pixels = self.detector.detect_rows(frame)
                status = ejector_status(rows)
                logger = self.line_logger
                if logger is not None:           # Line log: every line's status (written by its own thread)
                    logger.add(self.name, self.clock.camera_seconds, line_time, rows)
                check = self.frame_check
                if check is not None and check.per_line and not check.done:
                    per_line = line_statuses(rows)          # separate experiment: one number per LINE
                self.activity.mark_rows(rows, line_times, time.perf_counter(), pixels)

            # 6) Hand it to the screen.
            self.frames.put(frame)
            self._check_frame(arrived, frame_end, frame.shape[0], status=status, paused=False, per_line=per_line)

        self.camera.close()
        self._set_status("Stopped", False)

    # --------------------------------------------------------------- helpers
    def start_frame_check(self, frames=1000, per_line=False):
        """Count the next `frames` frames (see core/frame_check.py). Returns the FrameCheck.
        per_line=True: also keep the ejector status of every LINE (separate experiment)."""
        with self._lock:
            rate = self._settings.get("line_rate")
        self.frame_check = FrameCheck(frames, self.name, line_rate_setting=rate, per_line=per_line)
        return self.frame_check

    def _check_frame(self, arrived, frame_end, lines, status, paused, per_line=None):
        check = self.frame_check
        if check is not None and not check.done:
            camera_s = self.clock.camera_seconds if frame_end is not None else None
            check.record(time.perf_counter() - arrived, camera_s, lines, status, paused,
                         self.sdk_lost, camera_clock=getattr(self.clock, "use_camera_timestamp", True),
                         arrived=arrived, line_statuses=per_line)
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
            if "line_rate" in requests:
                self.camera.set_line_rate(int(requests["line_rate"]))
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

    def _measure_cpu(self):
        """CPU time of this thread per second of real time (thread_time = only this thread)."""
        now, cpu = time.monotonic(), time.thread_time()
        if self._cpu_mark is None:
            self._cpu_mark = (now, cpu)
        elif now - self._cpu_mark[0] >= 1.0:
            self.cpu_percent = (cpu - self._cpu_mark[1]) / (now - self._cpu_mark[0]) * 100
            self._cpu_mark = (now, cpu)

    def _sleep(self, seconds):
        """Sleep in small steps so stop() stays fast."""
        end = time.monotonic() + seconds
        while self._running and time.monotonic() < end:
            time.sleep(0.1)
