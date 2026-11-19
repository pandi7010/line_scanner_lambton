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
MAX_GAP = 2         # object pixels with a gap of up to 2 pixels belong to the same object


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
        active, level, _ = self.detect_with_pixels(frame)
        return active, level

    def detect_with_pixels(self, frame):
        """
        Like detect(), plus WHERE the object is:
            pixels = {channel: (first pixel, last pixel) of the object in that channel}
        If one object covers channels 16 and 17 (pixels 1000-1060), BOTH channels get
        (1000, 1060) - the whole object, not only the part inside the channel.
        Object pixels with a gap of up to MAX_GAP pixels count as one object.
        """
        cmap = self.channel_map               # one map for the whole frame
        width = frame.shape[1]
        sp = max(0, min(int(np.ceil(cmap.start)), width))   # first column inside the chute
        ep = max(sp, min(int(np.ceil(cmap.end)), width))     # first column after the chute
        if ep - sp < cmap.channels:
            return set(), 0, {}

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
            return set(), level, {}
        channel = cmap.channel_of(hit_columns)     # 1..63 (0 = outside)
        counts = np.bincount(channel, minlength=cmap.channels + 1)[1:cmap.channels + 1]
        active = {int(i) + 1 for i in np.flatnonzero(counts >= self.min_pixels)}

        return active, level, _object_pixels(hit_columns, channel, active)

    def detect_rows(self, frame):
        """
        LINE BY LINE: which channels have something in EACH line of the frame?
        Returns (rows, level, pixels):
            rows   : True/False table, one row per camera line, one column per channel
                     (rows[r, k] = channel k+1 has at least `min_pixels` hit pixels in line r)
            level  : background level (as in detect)
            pixels : {channel: (first pixel, last pixel)} of the objects in this frame
        With this the exact LINE where an object starts is known -> exact time.
        """
        cmap = self.channel_map
        lines, width = frame.shape[:2]
        sp = max(0, min(int(np.ceil(cmap.start)), width))
        ep = max(sp, min(int(np.ceil(cmap.end)), width))
        if ep - sp < cmap.channels:
            return np.zeros((lines, cmap.channels), dtype=bool), 0, {}

        colour = frame[:, sp:ep, self.colour_index]
        if self.band_mode:
            hit = (colour > self.lower) & (colour < self.upper)
        else:
            hit = (colour < self.lower) | (colour > self.upper)
        level = int(np.median(colour.min(axis=0)))

        # Where does each channel start in these columns? (kept until the map changes)
        key = (id(cmap), sp, ep)
        if getattr(self, "_row_cache_key", None) != key:
            channel_of_col = cmap.channel_of(np.arange(sp, ep))
            starts = np.searchsorted(channel_of_col, np.arange(1, cmap.channels + 1), side="left")
            empty = np.bincount(channel_of_col, minlength=cmap.channels + 1)[1:cmap.channels + 1] == 0
            self._row_cache_key = key
            self._row_cache = (channel_of_col, np.minimum(starts, ep - sp - 1), empty)
        channel_of_col, starts, empty = self._row_cache

        counts = np.add.reduceat(hit.astype(np.uint16), starts, axis=1)   # hit pixels per line+channel
        counts[:, empty] = 0
        rows = counts >= self.min_pixels

        active = {int(k) + 1 for k in np.flatnonzero(rows.any(axis=0))}
        hit_columns = np.flatnonzero(hit.any(axis=0))
        pixels = _object_pixels(hit_columns + sp, channel_of_col[hit_columns], active) if active else {}
        return rows, level, pixels


def _object_pixels(hit_columns, channel, active):
    """
    {channel: (first pixel, last pixel)} - split the hit pixels into groups ("runs") that
    touch (gap up to MAX_GAP); every active channel a run covers gets the WHOLE run.
    """
    pixels = {}
    if not active or len(hit_columns) == 0:
        return pixels
    breaks = np.flatnonzero(np.diff(hit_columns) > MAX_GAP + 1)
    starts = np.concatenate(([0], breaks + 1))
    ends = np.concatenate((breaks, [len(hit_columns) - 1]))
    for a, b in zip(starts, ends):
        first, last = int(hit_columns[a]), int(hit_columns[b])
        for ch in range(int(channel[a]), int(channel[b]) + 1):
            if ch in active:
                old = pixels.get(ch)
                pixels[ch] = (first, last) if old is None else (min(old[0], first), max(old[1], last))
    return pixels
