"""
tests/test_core.py - automatic test, NO cameras needed.

    python tests\test_core.py      -> must print  ALL TESTS PASSED
"""
import os
import sys
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # project folder
import sys, time, numpy as np
sys.path.insert(0, os.getcwd())
import core
assert 'PyQt5' not in sys.modules, "core must not import PyQt5"
from core import CameraWorker, ChannelMap
from config import load_config, save_camera_settings

# grid: 64 lines -> 63 channels
p = list(ChannelMap.equal(19, 4046).boundaries)
assert len(p) == 64 and p[0] == 19 and abs(p[-1] - 4046) < 1e-9
print("grid ok: channel width %.2f px" % (p[1] - p[0]))

# config
c = load_config()
assert c["channels"] == 63 and c["cameras"]["REAR"]["start_pixel"] == 60
assert c["cameras"]["FRONT"]["frame_lines"] == 16
print("config ok")

class FakeCam:
    handle = None; info = "exposure 65 us"
    def __init__(s): s.lines = 16; s.exp = 65.0; s.opened = 0
    def open(s): s.handle = 1; s.opened += 1
    def close(s): s.handle = None
    def read_frame(s, timeout_ms=2000):
        time.sleep(0.002)
        f = np.zeros((s.lines, 4096, 3), np.uint8); f[:, :10] = 200   # bright left edge
        return f
    def set_frame_lines(s, n): s.lines = n
    def set_exposure(s, e): s.exp = e
    def get_current_settings(s): return {"frame_lines": s.lines, "exposure_us": s.exp, "exposure_min": 1.0, "exposure_max": 5000.0}

cam = FakeCam(); w = CameraWorker("REAR", cam, mirror=True); w.start()
time.sleep(0.3)
f, n = w.frames.get(); st = w.get_status(); s, v = w.get_settings()
assert f.shape == (16, 4096, 3) and n > 10 and st[1] and s["frame_lines"] == 16, (f.shape, n, st, s)
assert f[0, -1, 0] == 200 and f[0, 0, 0] == 0, "mirror failed"
w.request_setting("frame_lines", 64); w.request_setting("exposure_us", 150)
time.sleep(0.2)
f, n2 = w.frames.get(); s, v2 = w.get_settings()
assert f.shape[0] == 64 and s["exposure_us"] == 150 and v2 > v, (f.shape, s)
print("worker ok: frames", n2, "status:", w.get_status()[0], "| settings:", s)
w.stop(); assert cam.handle is None and w.get_status()[0] == "Stopped"
print("stop ok")
print("ALL TESTS PASSED")
