"""
core/object_finder.py - Where is the thin object (wire / needle) in this frame?

Used for channel calibration: you hold a thin object in front of one valve,
and this finds its centre pixel in each camera - also when it is only a little
darker or brighter than the background (like the faint stripe you can see on screen).

How:
  1. one colour (blue), averaged over all lines of the frame -> one value per pixel column
  2. "background" = the EMPTY chute learned before (best: fixed dust spots cancel out),
     or, if not learned, a running median over 101 columns (follows slow light changes)
  3. difference = column value - background; the biggest difference is the object
  4. centre = weighted middle of the columns around it that differ at least half as much

    empty = column_profile(empty_frame)             # once, with nothing in the chute
    find_object(frame, reference=empty)  ->  {"centre": 1911.4, "strength": 23.0, "width": 9}  or  None

No PyQt5 here.
"""

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

SMOOTH = 101            # columns used for the running-median background (odd number)


def column_profile(frame, colour_index=0):
    """One value per pixel column: the colour averaged over all lines of the frame."""
    return frame[:, :, colour_index].astype(np.float32).mean(axis=0)


def find_object(frame, colour_index=0, search=None, min_strength=6.0, reference=None):
    """
    frame        : numpy array (lines, width, 3), BGR
    colour_index : 0 = blue (the background colour), 1 = green, 2 = red
    search       : (first, last) pixel range to look in, e.g. the chute; None = whole width
    min_strength : smallest difference from the background that counts as an object
    reference    : column_profile() of the EMPTY chute, or None
    Returns {"centre", "strength", "width"} or None if nothing clear was found.
    """
    profile = column_profile(frame, colour_index)
    if reference is not None and len(reference) == len(profile):
        background = reference
    else:
        half = SMOOTH // 2
        padded = np.pad(profile, half, mode="edge")
        background = np.median(sliding_window_view(padded, SMOOTH), axis=1)
    diff = profile - background

    width = profile.shape[0]
    first, last = (0, width) if search is None else (max(0, int(search[0])), min(width, int(search[1])))
    if last - first < 3:
        return None
    part = diff[first:last]
    i = int(np.argmax(np.abs(part)))
    strength = float(abs(part[i]))
    if strength < min_strength:
        return None

    # columns next to the peak that differ at least half as much (same direction)
    sign = 1.0 if part[i] > 0 else -1.0
    signal = part * sign
    left = i
    while left > 0 and signal[left - 1] >= strength / 2:
        left -= 1
    right = i
    while right < len(part) - 1 and signal[right + 1] >= strength / 2:
        right += 1
    cols = np.arange(left, right + 1)
    weights = signal[left:right + 1]
    centre = first + float(np.sum(cols * weights) / np.sum(weights))
    return {"centre": centre, "strength": strength, "width": int(right - left + 1)}
