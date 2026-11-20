"""
hardware/mindvision_camera.py - HARDWARE LAYER: talk to one MindVision camera.

This file only knows about the camera and the MindVision SDK.
It knows nothing about threads, windows or the grid.

Same method as lambton-main (GigECameraWorker / auto_connect_cameras in
LAMTON_LINE_NEW_ESP32.py), in small steps:

    1. CameraEnumerateDevice()      -> list every camera on the network
    2. pick the one whose serial number matches camera_config.json
    3. CameraInit()                 -> open it, get a "handle" (an ID number)
    4. CameraSetIspOutFormat(BGR8)  -> ask the SDK for colour pixels (Blue, Green, Red)
    5. CameraSetTriggerMode(0)      -> continuous mode: the camera streams by itself
    5b. frame height (lines per frame)
    5c. exposure + gains from camera_config.json
    6. CameraPlay()                 -> start streaming
    7. loop:  CameraGetImageBuffer     -> raw data from the camera
              CameraImageProcess       -> raw data -> colour (BGR) picture
              CameraReleaseImageBuffer -> give the raw buffer back to the SDK
    8. CameraUnInit()               -> close it

The cameras are line-scan cameras: every "frame" is a short, very wide strip
(e.g. 4096 pixels wide x 16 lines tall).
"""

from ctypes import c_ubyte

import time

import numpy as np

# ---------------------------------------------------------------------------
# Load the MindVision SDK (hardware/sdk/mvsdk.py + MVCAMSDK_X64.dll).
# If it cannot be loaded, SDK_ERROR explains why (printed at start).
# ---------------------------------------------------------------------------
try:
    from hardware.sdk import mvsdk
    if getattr(mvsdk, "_sdk", None) is None:
        raise ImportError("MVCAMSDK_X64.dll could not be loaded "
                          "(is the MindVision SDK installed? is Python 64-bit?)")
    SDK_AVAILABLE = True
    SDK_ERROR = ""
except Exception as error:          # noqa: BLE001  (we want to catch everything here)
    mvsdk = None
    SDK_AVAILABLE = False
    SDK_ERROR = str(error)


class CameraError(Exception):
    """Raised when a camera cannot be found, opened or read."""


def serial_number(device_info):
    """Serial number of a camera from the device list, e.g. '075002923007'."""
    return device_info.acSn.decode("utf-8", "ignore").strip("\x00").strip()


def list_cameras():
    """
    Return a list of dicts, one per connected camera:
        {"serial": ..., "name": ..., "model": ..., "port": ..., "ip": ...}
    """
    if not SDK_AVAILABLE:
        raise CameraError(f"MindVision SDK not available: {SDK_ERROR}")

    cameras = []
    for dev in mvsdk.CameraEnumerateDevice():
        try:
            ip = mvsdk.CameraGigeGetIp(dev)[0]      # only GigE cameras have an IP
        except Exception:                           # noqa: BLE001
            ip = ""
        cameras.append({
            "serial": serial_number(dev),
            "name": dev.GetFriendlyName(),
            "model": dev.GetProductName(),
            "port": dev.GetPortType(),
            "ip": ip,
        })
    return cameras


class MindVisionCamera:
    """
    One MindVision camera.

    Example:
        cam = MindVisionCamera("075002923007")
        cam.open()
        frame = cam.read_frame()   # numpy array, shape (lines, width, 3), BGR
        cam.close()
    """

    def __init__(self, serial, frame_lines=16, settings=None):
        """
        serial      : the camera's serial number (from camera_config.json)
        frame_lines : how many lines the camera puts in one frame (lambton-main uses 64).
                      Small = fast, smooth picture. Large (e.g. 10000) = one frame per second!
        settings    : dict with exposure_us, analog_gain, r_gain, g_gain, b_gain.
                      Written to the camera at open - exactly like lambton-main does at
                      its start. Without them the camera keeps its power-on values,
                      which can give a (nearly) black picture.
        """
        self.serial = serial
        self.frame_lines = frame_lines
        self.settings = settings or {}
        self.info = ""              # e.g. "exposure 65 us | analog gain 3 | RGB gain 100/100/140"
        # Timing of the last frame (used by core/camera_clock.py):
        #   camera_ts : the camera's own clock when the frame was finished (0.1 ms steps)
        #   arrived   : PC time (time.perf_counter) when the frame reached the program
        #   lines     : lines in the frame
        self.last_frame_info = None
        self.handle = None          # SDK handle, None = camera is closed
        self.buffer = None          # memory where the SDK writes the colour picture

    # ------------------------------------------------------------------ open
    def open(self):
        if not SDK_AVAILABLE:
            raise CameraError(f"MindVision SDK not available: {SDK_ERROR}")
        if not self.serial:
            raise CameraError("No serial number in camera_config.json")

        # Steps 1 + 2: find our camera by serial number.
        devices = mvsdk.CameraEnumerateDevice()
        device = next((d for d in devices if serial_number(d) == self.serial), None)
        if device is None:
            found = ", ".join(serial_number(d) for d in devices) or "none"
            raise CameraError(f"Camera {self.serial} not found (cameras found: {found})")

        # Step 3: open it. -1, -1 = load the settings the camera used last time.
        try:
            self.handle = mvsdk.CameraInit(device, -1, -1)
        except mvsdk.CameraException as e:
            raise CameraError(f"Cannot open camera {self.serial}: {e}  "
                              f"(is lambton-main or MVDCP still running?)") from e

        try:
            # Step 4: we want colour pixels in B, G, R order (same as lambton-main).
            mvsdk.CameraSetIspOutFormat(self.handle, mvsdk.CAMERA_MEDIA_TYPE_BGR8)
            # Step 5: continuous / free-run (same as lambton-main).
            mvsdk.CameraSetTriggerMode(self.handle, 0)
            # Step 5b: lines per frame (same as lambton-main's _set_camera_hardware_height).
            if self.frame_lines:
                self.set_frame_lines(int(self.frame_lines))

            # Step 5c: exposure + gains from camera_config.json (same as lambton-main).
            print(f"[CAMERA {self.serial}] before: {self._read_settings()}")
            self._apply_settings(self.settings)
            self.info = self._read_settings()
            print(f"[CAMERA {self.serial}] now   : {self.info}")

            # Reserve memory big enough for the LARGEST picture this camera can make.
            cap = mvsdk.CameraGetCapability(self.handle)
            size = cap.sResolutionRange.iWidthMax * cap.sResolutionRange.iHeightMax * 3
            self.buffer = mvsdk.CameraAlignMalloc(size, 16)

            # Step 6: start streaming.
            mvsdk.CameraPlay(self.handle)
        except Exception:
            self.close()
            raise

    # ------------------------------------------- change settings while running
    def set_frame_lines(self, lines):
        """
        A line-scan camera collects `lines` scan lines, then sends them as one frame.
        Example at ~8,800 lines/s:   64 lines -> ~138 frames/s (smooth)
                                  10000 lines -> ~0.9 frames/s (looks frozen)
        Width and left offset stay as they are; only the height changes.
        """
        res = mvsdk.CameraGetImageResolution(self.handle)
        mvsdk.CameraSetImageResolutionEx(self.handle, 0xFF, 0, 0,
                                         res.iHOffsetFOV, 0, res.iWidth, lines, 0, 0)
        self.frame_lines = lines
        print(f"[CAMERA {self.serial}] frame height set to {lines} lines")

    def set_exposure(self, exposure_us):
        """Exposure time in microseconds. Longer = brighter (but more motion blur)."""
        mvsdk.CameraSetExposureTime(self.handle, float(exposure_us))
        self.settings["exposure_us"] = float(exposure_us)
        self.info = self._read_settings()

    def get_current_settings(self):
        """
        What the camera is REALLY using now, read back from the camera:
            {"frame_lines": 16, "exposure_us": 65.0, "exposure_min": .., "exposure_max": ..}
        """
        res = mvsdk.CameraGetImageResolution(self.handle)
        exp_min, exp_max, _step = mvsdk.CameraGetExposureTimeRange(self.handle)
        return {
            "frame_lines": int(res.iHeight),
            "exposure_us": float(mvsdk.CameraGetExposureTime(self.handle)),
            "exposure_min": float(exp_min),
            "exposure_max": float(exp_max),
        }

    def _read_settings(self):
        """Ask the camera what exposure and gains it is REALLY using now."""
        try:
            exposure = mvsdk.CameraGetExposureTime(self.handle)
            analog = mvsdk.CameraGetAnalogGain(self.handle)
            r, g, b = mvsdk.CameraGetGain(self.handle)
            return f"exposure {exposure:.0f} us | analog gain {analog} | RGB gain {r}/{g}/{b}"
        except Exception as e:                  # noqa: BLE001
            return f"(could not read settings: {e})"

    def _apply_settings(self, s):
        """Write exposure and gains (lambton-main: GigECameraWorker.set_param)."""
        h = self.handle
        if "exposure_us" in s:
            mvsdk.CameraSetExposureTime(h, float(s["exposure_us"]))
        if "analog_gain" in s:
            mvsdk.CameraSetAnalogGain(h, int(s["analog_gain"]))
        if all(k in s for k in ("r_gain", "g_gain", "b_gain")):
            mvsdk.CameraSetGain(h, int(s["r_gain"]), int(s["g_gain"]), int(s["b_gain"]))

    # ------------------------------------------------------------------ read
    def read_frame(self, timeout_ms=2000):
        """
        Wait for the next strip from the camera.
        Returns a numpy array (height, width, 3) in BGR order,
        or None if nothing arrived within timeout_ms.
        Raises CameraError if the camera is lost.
        """
        if self.handle is None:
            raise CameraError("Camera is not open")

        # Step 7a: get raw data.
        try:
            raw_data, frame_head = mvsdk.CameraGetImageBuffer(self.handle, timeout_ms)
        except mvsdk.CameraException as e:
            if e.error_code == mvsdk.CAMERA_STATUS_TIME_OUT:
                return None                     # no new data yet - not an error
            raise CameraError(f"Camera {self.serial} read error: {e}") from e
        arrived = time.perf_counter()           # first moment the PC has this frame
        self.last_frame_info = {"camera_ts": int(frame_head.uiTimeStamp), "arrived": arrived,
                                "lines": int(frame_head.iHeight)}

        # Step 7b + 7c: convert to colour, then ALWAYS give the raw buffer back.
        try:
            mvsdk.CameraImageProcess(self.handle, raw_data, self.buffer, frame_head)
        finally:
            mvsdk.CameraReleaseImageBuffer(self.handle, raw_data)

        # Turn the SDK memory into a numpy array.
        width, height = frame_head.iWidth, frame_head.iHeight
        size = width * height * 3
        data = (c_ubyte * size).from_address(self.buffer)
        frame = np.frombuffer(data, dtype=np.uint8).reshape((height, width, 3))

        # .copy() is important: the SDK overwrites self.buffer with the next frame.
        return frame.copy()

    def frame_statistics(self):
        """The camera SDK's own counters: {"total", "captured", "lost"} frames since opening."""
        if self.handle is None:
            return None
        st = mvsdk.CameraGetFrameStatistic(self.handle)
        return {"total": int(st.iTotal), "captured": int(st.iCapture), "lost": int(st.iLost)}

    # ----------------------------------------------------------------- close
    def close(self):
        """Step 8: close the camera and free the memory. Safe to call twice."""
        if self.handle is not None:
            try:
                mvsdk.CameraUnInit(self.handle)
            except Exception:                   # noqa: BLE001
                pass
            self.handle = None
        if self.buffer is not None:
            try:
                mvsdk.CameraAlignFree(self.buffer)
            except Exception:                   # noqa: BLE001
                pass
            self.buffer = None
