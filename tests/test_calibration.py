"""
tests/test_calibration.py - automatic test, NO cameras needed.

    python tests\\test_calibration.py      -> must print  ALL TESTS PASSED

Simulates the real problem: FRONT and REAR see the SAME valves through different
lenses, so equal channels (start..end / 63) do not agree between the cameras.
Checks that:
  * the object finder finds a faint thin wire (even with a dust spot + uneven light)
  * calibration from 5 measured valves makes both cameras agree on the channel
  * equal channels map exactly like before (63 equal slices)
"""
import os
import sys

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # project folder
sys.path.insert(0, os.getcwd())

import numpy as np  # noqa: E402

from core import ChannelDetector, ChannelMap, column_profile, find_object  # noqa: E402

assert "PyQt5" not in sys.modules, "core must not import PyQt5"
rng = np.random.default_rng(7)

# --- 1) ChannelMap basics
eq = ChannelMap.equal(19, 4046)
assert len(eq.boundaries) == 64 and eq.boundaries[0] == 19 and eq.boundaries[-1] == 4046
px = np.arange(0, 4096)
old_rule = np.where((px >= 19) & (px < 4046), ((px - 19) / ((4046 - 19) / 63)).astype(int) + 1, 0)
assert np.array_equal(eq.channel_of(px), old_rule), "equal map must equal the old rule"
lin = ChannelMap.from_points({1: 100.0, 63: 3944.0})                  # 62 channels -> 62 px each
assert abs(lin.centre(1) - 100) < 1e-6 and abs(lin.centre(63) - 3944) < 1e-6
assert np.allclose(lin.widths(), 62.0)
print("channel map ok:", lin.describe())

# --- 2) two cameras that see the valves differently (lens + position)
# pixel of the CENTRE of valve `ch` (lens bends the outer valves a little)
true_front = lambda ch: 40 + 62.5 * ch + 0.05 * (ch - 32) ** 2      # noqa: E731   valve 1 = 150, 63 = 4025
true_rear = lambda ch: 95 + 61.5 * ch - 0.04 * (ch - 32) ** 2       # noqa: E731   valve 1 = 118, 63 = 3931


def frame_with(pixel_positions, cam_seed, faint=14.0):
    """Blue background with uneven light, a dust spot, noise, and thin faint wires."""
    x = np.arange(4096)
    blue = 88 + 10 * np.sin(x / 700.0 + cam_seed) - 6 * ((x - 2048) / 2048) ** 2
    blue = np.tile(blue, (16, 1))
    blue[:, 3900:3906] -= 25                      # dust spot near the edge (like FRONT ch 60-62)
    for p in pixel_positions:                     # wire, about 5 px wide, soft edges
        blue -= faint * np.exp(-0.5 * ((x - p) / 2.0) ** 2)
    blue += rng.normal(0, 2.0, blue.shape)
    f = np.zeros((16, 4096, 3), np.uint8)
    f[..., 0] = np.clip(blue, 0, 255)
    return f


empty = {"FRONT": column_profile(frame_with([], 0.0)), "REAR": column_profile(frame_with([], 1.0))}
truth = {"FRONT": true_front, "REAR": true_rear}

# finder: wire at valve 30, with and without the learned empty chute
for cam, seed in (("FRONT", 0.0), ("REAR", 1.0)):
    f = frame_with([truth[cam](30)], seed)
    with_ref = find_object(f, reference=empty[cam])
    assert abs(with_ref["centre"] - truth[cam](30)) < 1.0, (cam, with_ref)
print(f"finder ok: wire at valve 30 found within 1 px (FRONT strength {with_ref['strength']:.0f})")
no_ref = find_object(frame_with([true_front(30)], 0.0))
print("  (without 'learn empty chute' it found pixel", round(no_ref["centre"]),
      "- the dust spot is at 3903; this is why learning the empty chute is recommended)")

# --- 3) measure valves 1, 16, 32, 48, 63 in both cameras -> calibrate
maps = {}
for cam, seed in (("FRONT", 0.0), ("REAR", 1.0)):
    points = {}
    for valve in (1, 16, 32, 48, 63):
        found = find_object(frame_with([truth[cam](valve)], seed), reference=empty[cam])
        points[valve] = found["centre"]
    maps[cam] = ChannelMap.from_points(points)
    print(f"{cam}: {maps[cam].describe()}")

# --- 4) objects at random places inside valves: do both cameras agree?
equal_maps = {"FRONT": ChannelMap.equal(19, 4046), "REAR": ChannelMap.equal(60, 3981)}
agree_cal = agree_eq = 0
N = 2000
for _ in range(N):
    valve = int(rng.integers(1, 64))
    pos = valve + rng.uniform(-0.35, 0.35)          # somewhere inside the valve (not on the edge)
    ch_cal = {cam: int(maps[cam].channel_of(truth[cam](pos))) for cam in maps}
    ch_eq = {cam: int(equal_maps[cam].channel_of(truth[cam](pos))) for cam in maps}
    agree_cal += ch_cal["FRONT"] == ch_cal["REAR"] == valve
    agree_eq += ch_eq["FRONT"] == ch_eq["REAR"]
print(f"objects inside a valve: equal channels -> cameras agree {agree_eq / N:.0%}, "
      f"calibrated -> both cameras on the RIGHT valve {agree_cal / N:.0%}")
assert agree_cal == N and agree_eq < N

# --- 5) the detector uses the calibrated map
det = ChannelDetector(maps["REAR"], 68, 221)
f = np.zeros((16, 4096, 3), np.uint8)
f[..., 0] = 93
c = int(round(maps["REAR"].centre(40)))
f[:, c - 10:c + 10, 0] = 30
assert det.detect(f)[0] == {40}, det.detect(f)
det.channel_map = equal_maps["REAR"]                 # can be swapped while running
assert det.detect(f)[0] != set()
print("detector with calibrated map ok")
print("ALL TESTS PASSED")
