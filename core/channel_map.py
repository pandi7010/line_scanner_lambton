"""
core/channel_map.py - Which camera pixels belong to which ejector channel (valve).

A ChannelMap is just 64 numbers: the pixel positions of the channel BOUNDARIES
(left edge of channel 1 ... right edge of channel 63). Channel k = pixels
between boundary k-1 and boundary k.

Two ways to make one:

1) EQUAL (no calibration) - 63 equal slices between start_pixel and end_pixel:
       ChannelMap.equal(19, 4046)             -> every channel 63.9 px wide
   Simple, but the two cameras look at the valves from different sides through
   different lenses, so equal slices in FRONT and REAR do not land on exactly the
   same valves -> an object can be channel 30 in FRONT but 31 in REAR.

2) CALIBRATED - from measured points (same method as lambton-main channel_map.py):
       a thin object held in front of the CENTRE of valve 1, 16, 32, 48, 63 was seen at
       pixel 131, 1052, 2040, 3009, 3950:
       ChannelMap.from_points({1: 131, 16: 1052, 32: 2040, 48: 3009, 63: 3950})
   A smooth curve  pixel = a + b*channel + c*channel^2  is fitted through the points
   (a straight line if only 2 points). Valve centres are at channel 1, 2, 3 ...;
   boundaries are half-way, at 0.5, 1.5, ... 63.5. The curve corrects the lens.

No PyQt5 here.
"""

import numpy as np


class ChannelMap:

    def __init__(self, boundaries, points=None):
        """boundaries: channels + 1 increasing pixel positions. points: {channel: pixel} or None."""
        self.boundaries = np.asarray(boundaries, dtype=float)
        self.channels = len(self.boundaries) - 1
        self.points = dict(points) if points else {}
        if self.channels < 1 or np.any(np.diff(self.boundaries) <= 0):
            raise ValueError("channel boundaries must be increasing - check the measured points")

    # ------------------------------------------------------------- makers
    @classmethod
    def equal(cls, start_pixel, end_pixel, channels=63):
        """63 equal-width channels from start_pixel to end_pixel (the old rule)."""
        return cls(np.linspace(float(start_pixel), float(end_pixel), channels + 1))

    @classmethod
    def from_points(cls, points, channels=63):
        """
        points: {channel number: pixel of the object at that valve's centre}, at least 2.
        3 or more points -> curve (corrects lens distortion), 2 points -> straight line.
        """
        points = {int(c): float(p) for c, p in points.items()}
        if len(points) < 2:
            raise ValueError("need at least 2 measured points")
        chans = np.array(sorted(points), dtype=float)
        pixels = np.array([points[int(c)] for c in chans])
        degree = 2 if len(points) >= 3 else 1
        coef = np.polyfit(chans, pixels, degree)
        boundaries = np.polyval(coef, np.arange(channels + 1) + 0.5)
        return cls(boundaries, points)

    @classmethod
    def from_config(cls, cam_cfg, channels=63):
        """Calibrated if camera_config.json has >= 2 channel_points, else equal."""
        points = cam_cfg.get("channel_points") or {}
        if len(points) >= 2:
            try:
                return cls.from_points(points, channels)
            except ValueError as e:
                print(f"[CHANNEL MAP] bad channel_points ({e}) - using equal channels")
        return cls.equal(cam_cfg["start_pixel"], cam_cfg["end_pixel"], channels)

    # ------------------------------------------------------------- use
    @property
    def calibrated(self):
        return len(self.points) >= 2

    @property
    def start(self):
        return float(self.boundaries[0])

    @property
    def end(self):
        return float(self.boundaries[-1])

    def channel_of(self, pixels):
        """
        Channel number (1..63) of each pixel position, 0 = outside the chute.
        Works for one number or a numpy array.
        """
        pixels = np.asarray(pixels, dtype=float)
        ch = np.searchsorted(self.boundaries, pixels, side="right")   # 1 = inside channel 1
        return np.where((pixels >= self.boundaries[0]) & (pixels < self.boundaries[-1]), ch, 0)

    def widths(self):
        """Width of every channel in pixels."""
        return np.diff(self.boundaries)

    def centre(self, channel):
        """Pixel position of the centre of a channel."""
        return float((self.boundaries[channel - 1] + self.boundaries[channel]) / 2)

    def describe(self):
        w = self.widths()
        kind = f"calibrated from {len(self.points)} points" if self.calibrated else "equal channels"
        return (f"{kind}: pixels {self.start:.0f} - {self.end:.0f}, "
                f"channel width {w.min():.1f} - {w.max():.1f} px")
