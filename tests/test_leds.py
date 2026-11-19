"""
tests/test_leds.py - automatic test, NO cameras needed.

    python tests\test_leds.py      -> must print  ALL TESTS PASSED
"""
import os
import sys
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # project folder
import sys, time, numpy as np
sys.path.insert(0, os.getcwd())
from core import ChannelDetector, ChannelActivity, CameraWorker, ChannelMap
assert 'PyQt5' not in sys.modules

# lambton-main's algorithm (evaluate_ejectors_instant), copied for comparison
def lambton(frame, sp, ep, n, lo, hi, band=False):
    mn = frame.min(axis=0); mx = frame.max(axis=0)
    breach = np.zeros(mn.shape[0], bool)
    c = 0
    breach |= ((mn[:, c] > lo) & (mx[:, c] < hi)) if band else ((mn[:, c] < lo) | (mx[:, c] > hi))
    breach[:sp] = False; breach[ep:] = False
    px = np.where(breach)[0]; out = set()
    if len(px):
        w = float(ep - sp) / n
        u, cnt = np.unique(((px - sp) / w).astype(np.int32), return_counts=True)
        out = {int(i) + 1 for i, k in zip(u, cnt) if 0 <= i < n and k >= 2}
    return out

rng = np.random.default_rng(1)
for cam, sp, ep, lo, hi in [("FRONT", 19, 4046, 68, 221), ("REAR", 60, 3981, 60, 164)]:
    det = ChannelDetector(ChannelMap.equal(sp, ep), lo, hi)
    # empty chute: blue ~93 +- noise -> nothing
    bg = np.zeros((16, 4096, 3), np.uint8); bg[..., 0] = rng.integers(85, 101, (16, 4096))
    a, level = det.detect(bg); assert a == set() and 85 <= level <= 100, (a, level)
    for trial in range(300):
        f = bg.copy()
        for _ in range(rng.integers(0, 6)):
            x = int(rng.integers(0, 4096)); w = int(rng.integers(1, 80))
            f[:, x:x + w, 0] = rng.choice([20, 250])
        mine, _ = det.detect(f); ref = lambton(f, sp, ep, 63, lo, hi)
        assert mine == ref, (cam, trial, mine ^ ref)
    for trial in range(100):   # band mode too
        f = bg.copy(); x = int(rng.integers(0, 4000)); f[:, x:x + 50, 0] = 30
        d2 = ChannelDetector(ChannelMap.equal(sp, ep), 80, 110, band_mode=True)
        assert d2.detect(f)[0] == lambton(f, sp, ep, 63, 80, 110, band=True)
    print(f"{cam}: detector == lambton-main on 400 random frames")

# a dark grain at pixels 1000-1060 on FRONT -> channel 16 (and 17 if it crosses)
det = ChannelDetector(ChannelMap.equal(19, 4046), 68, 221)
f = bg.copy(); f[:, 1000:1061, 0] = 40
print("grain 1000-1060 ->", sorted(det.detect(f)[0]), "(channel width 63.9, (1000-19)/63.9 = 15.3 -> ch 16, (1060-19)/63.9 = 16.3 -> ch 17)")

# pixel ranges: one object over channels 16+17 -> both get the WHOLE object; a 2-pixel gap = same object
f = bg.copy(); f[:, 1000:1030, 0] = 40; f[:, 1032:1061, 0] = 40; f[:, 2500:2510, 0] = 40
active, _, pixels = det.detect_with_pixels(f)
assert pixels[16] == (1000, 1060) and pixels[17] == (1000, 1060), pixels
assert set(pixels) == active and all(p == (2500, 2509) for ch, p in pixels.items() if ch not in (16, 17)), pixels
print("pixel ranges ok:", {ch: pixels[ch] for ch in sorted(pixels)})

# hold time
act = ChannelActivity(63); act.mark({5, 63, 0, 64}, now=10.0)
assert act.active(0.15, now=10.1) == {5, 63} and act.active(0.15, now=10.2) == set()
print("hold ok")

# speed (worst case: lots of hits)
f = bg.copy(); f[:, ::7, 0] = 20
t = time.perf_counter(); N = 2000
for _ in range(N): det.detect(f)
ms = (time.perf_counter() - t) / N * 1000
print(f"detect: {ms:.3f} ms per 16-line frame -> max {1000/ms:,.0f} frames/s (cameras send ~300/s)")

# worker integration with a fake camera that shows a grain every 50 frames
class Cam:
    handle=None; info=""
    def __init__(s): s.n=0
    def open(s): s.handle=1
    def close(s): s.handle=None
    def read_frame(s, timeout_ms=2000):
        time.sleep(0.003); s.n+=1; fr=bg.copy()
        if s.n % 50 == 0: fr[:, 100:160, 0] = 30   # pixels 100-159 (camera); mirrored for REAR
        return fr
    def set_frame_lines(s,n): pass
    def set_exposure(s,e): pass
    def get_current_settings(s): return {}
for mirror, exp in [(False, {2, 3}), (True, None)]:
    w = CameraWorker("X", Cam(), mirror=mirror, detector=ChannelDetector(ChannelMap.equal(19, 4046), 68, 221)); w.start()
    seen=set(); t0=time.monotonic()
    while time.monotonic()-t0 < 1.0:
        seen |= w.activity.active(0.15); time.sleep(0.04)   # like the 25 Hz screen
    w.stop()
    print(f"worker mirror={mirror}: LEDs seen {sorted(seen)}, background level {w.background_level}, hits {int(w.activity.hits().sum())}")
    if exp: assert seen == exp
    else: assert seen and min(seen) > 55   # mirrored -> right side of the chute
print("ALL TESTS PASSED")
