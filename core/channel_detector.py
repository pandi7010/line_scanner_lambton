"""
core/channel_detector.py - Which of the 63 channels has something in it?

Same "background" detection as lambton-main (evaluate_ejectors_instant in
LAMTON_LINE_NEW_ESP32.py), written step by step:

    The chute background is blue. We look at ONE colour (default BLUE) of
    every pixel column of the frame:

      1. darkest value per column  = min over all lines of the frame
         brightest value per column = max over all lines of the frame
      2. a column is "hit" when
             darkest  < lower      (something darker than the background)
          or brightest > upper     (something brighter than the background)
         (band_mode = the opposite: hit when the value stays INSIDE lower..upper)
      3. only columns inside the chute count (first to last channel boundary)
      4. column -> channel with the ChannelMap (core/channel_map.py):
         equal slices between start_pixel and end_pixel (like lambton-main),
         or calibrated boundaries measured on the real valves
      5. a channel is ON when it has at least `min_pixels` hit columns
         (filters out single noisy pixels)

Example (FRONT, equal map 19-4046, lower 68, upper 221): background blue ~93 -> nothing ON.
A dark grain (blue 40) over pixels 1000-1060 -> channels 16 and 17 ON.

No PyQt5, no camera SDK: just numpy.
"""

import numpy as np

COLOUR_INDEX = {"BLUE": 0, "GREEN": 1, "RED": 2}     # frames are B, G, R


class ChannelDetector:

    def __init__(self, channel_map, lower=68, upper=221,
                 band_mode=False, colour="BLUE", min_pixels=2):
        """channel_map: core.ChannelMap. It may be replaced while running (calibration)."""
        self.channel_map = channel_map
        self.lower = int(lower)
        self.upper = int(upper)
        self.band_mode = bool(band_mode)
        self.colour_index = COLOUR_INDEX.get(str(colour).upper(), 0)
        self.min_pixels = int(min_pixels)

    @property
    def channels(self):
        return self.channel_map.channels

    def detect(self, frame):
        """
        frame: numpy array (lines, width, 3), BGR.
        Returns (set of channel numbers 1..63 that are ON, background level).
        background level = median of the colour in the chute (to help choose lower/upper).
        """
        cmap = self.channel_map               # one map for the whole frame
        width = frame.shape[1]
        sp = max(0, min(int(np.ceil(cmap.start)), width))   # first column inside the chute
        ep = max(sp, min(int(np.ceil(cmap.end)), width))     # first column after the chute
        if ep - sp < cmap.channels:
            return set(), 0

        # Step 1: one colour, only the chute columns.
        colour = frame[:, sp:ep, self.colour_index]
        darkest = colour.min(axis=0)
        brightest = colour.max(axis=0)

        # Step 2: hit columns.
        if self.band_mode:
            hit = (darkest > self.lower) & (brightest < self.upper)
        else:
            hit = (darkest < self.lower) | (brightest > self.upper)

        level = int(np.median(darkest))

        # Steps 3-5: hit columns -> channels with enough hits.
        hit_columns = np.flatnonzero(hit) + sp     # camera pixel positions
        if hit_columns.size == 0:
            return set(), level
        channel = cmap.channel_of(hit_columns)     # 1..63 (0 = outside)
        counts = np.bincount(channel, minlength=cmap.channels + 1)[1:cmap.channels + 1]
        active = {int(i) + 1 for i in np.flatnonzero(counts >= self.min_pixels)}
        return active, level
