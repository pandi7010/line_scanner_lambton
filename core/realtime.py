"""
core/realtime.py - Ask Windows to run the camera and ejector threads FIRST.

Why? When Windows is busy for a moment (another program, an update, the antivirus),
it can make our threads wait their turn. Then a camera frame is read late and the
ejector LED comes ON too late (seen on 20 Nov 2026: one object 17.9 ms LATE).

What it does (only on Windows, switch: "realtime" in camera_config.json):
  * high_priority : the 2 camera threads + the ejector thread get priority HIGHEST
                    (like an ambulance: other programs' threads wait for them).
                    The screen thread stays normal, so the mouse and windows stay smooth.
  * timer_1ms     : Windows timer every 1 ms instead of every 15.6 ms
                    (short waits like 1 ms are then really about 1 ms).

Turn it OFF:  "realtime": {"high_priority": false, "timer_1ms": false}

    realtime.configure(high_priority=True, timer_1ms=True)   # main.py, once
    realtime.boost_this_thread("FRONT camera")               # first line of a thread
    realtime.status_text()   -> "FRONT camera HIGH, REAR camera HIGH, ejector HIGH, timer 1 ms"

No PyQt5 here.
"""

import atexit
import ctypes
import os
import threading

THREAD_PRIORITY_HIGHEST = 2      # Windows: normal = 0, highest = 2, time critical = 15

_enabled = False
_timer = "off"
_status = {}                     # thread name -> "HIGH" / "normal" / "failed"
_lock = threading.Lock()


def configure(high_priority=True, timer_1ms=True):
    """Call once at start (main.py)."""
    global _enabled, _timer
    _enabled = bool(high_priority)
    if timer_1ms and os.name == "nt":
        try:
            winmm = ctypes.WinDLL("winmm")
            if winmm.timeBeginPeriod(1) == 0:          # 0 = OK
                _timer = "1 ms"
                atexit.register(winmm.timeEndPeriod, 1)
            else:
                _timer = "failed"
        except Exception:        # noqa: BLE001
            _timer = "failed"
    else:
        _timer = "off"


def boost_this_thread(name):
    """Give the CALLING thread high priority (if switched on). Returns the status text."""
    if not _enabled:
        result = "normal"
    elif os.name != "nt":
        result = "normal (not Windows)"
    else:
        try:
            kernel32 = ctypes.WinDLL("kernel32")
            kernel32.GetCurrentThread.restype = ctypes.c_void_p
            kernel32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
            ok = kernel32.SetThreadPriority(kernel32.GetCurrentThread(), THREAD_PRIORITY_HIGHEST)
            result = "HIGH" if ok else "failed"
        except Exception:        # noqa: BLE001
            result = "failed"
    with _lock:
        _status[name] = result
    return result


def status_text():
    """e.g. 'FRONT camera HIGH, REAR camera HIGH, ejector HIGH, timer 1 ms'."""
    with _lock:
        parts = [f"{name} {value}" for name, value in _status.items()]
    return ", ".join(parts + [f"timer {_timer}"])
