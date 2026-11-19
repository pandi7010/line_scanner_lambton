"""
core/event_log.py - The list of ejections (one row per object).

    touched line at  seen by  ch  PC saw at     reaction  LED ON at     delay set  delay meas.  late by  result
    16:52:03.1250    BOTH     16  16:52:03.1281  3.1      16:52:03.1350  10.0       10.0         0.0      ON TIME

    touched line at = exact moment the object touched the decision line (camera clock)
    PC saw at       = when the program found it in the frame
    PC reaction     = PC saw at - touched line at   (frame filling + network + Windows)
    LED ON at       = when the ejector LED switched ON
    delay measured  = LED ON at - touched line at
    late by         = LED ON at - planned LED ON (touched + delay set); 0 = exactly on time
    result          = ON TIME (late <= 1 ms), LATE, or MISSED (PC too late: no LED at all)
    object length   = time from the object's first to its last line
    FRONT / REAR pixels = first - last pixel where that camera saw the object
                     (REAR pixels are counted after mirroring, like the picture on screen)

Only the newest `max_events` rows are kept in memory (default 20,000).
core/ejector.py writes the rows; ui/log_window.py shows them. No PyQt5 here.
"""

import csv
import datetime
import threading
from collections import deque


def clock_text(t, with_date=False):
    """PC time -> '16:52:03.1271' (0.1 ms) or with date. None -> '-'."""
    if t is None:
        return "-"
    fmt = "%Y-%m-%d %H:%M:%S.%f" if with_date else "%H:%M:%S.%f"
    return datetime.datetime.fromtimestamp(t).strftime(fmt)[:-2]


def _round(value):
    return None if value is None else round(float(value), 1)


class EjectEvent:
    """One object (one ejection, or one MISSED ejection)."""

    __slots__ = ("seq", "seen_time", "seen_by", "channel", "found_time", "on_time",
                 "delay_set_ms", "delay_ms", "late_ms", "result", "length_ms", "pixels")

    def __init__(self, seq, seen_time, seen_by, channel, found_time, on_time,
                 delay_set_ms, delay_ms, late_ms, result, length_ms, pixels=None):
        self.seq = seq                    # running number 1, 2, 3 ... (never repeats)
        self.seen_time = seen_time        # PC clock (s): object touched the decision line
        self.seen_by = seen_by            # "FRONT" / "REAR" / "BOTH"
        self.channel = channel            # 1 ... 63
        self.found_time = found_time      # PC clock (s): program found it
        self.on_time = on_time            # PC clock (s): LED ON, None = MISSED
        self.delay_set_ms = delay_set_ms
        self.delay_ms = delay_ms          # measured, None = MISSED
        self.late_ms = late_ms            # None = MISSED
        self.result = result              # "ON TIME" / "LATE" / "MISSED"
        self.length_ms = length_ms        # first to last line of the object
        self.pixels = pixels or {}        # {"FRONT": (1000, 1060), "REAR": (1012, 1071)}

    @property
    def reaction_ms(self):
        """How long the PC needed to find the object after it touched the line."""
        return round((self.found_time - self.seen_time) * 1000.0, 1)

    def pixels_text(self, camera):
        """e.g. '1000 - 1060', or '-' if this camera did not see it."""
        p = self.pixels.get(camera)
        return f"{p[0]} - {p[1]}" if p else "-"


class EventLog:

    def __init__(self, max_events=20000):
        self._events = deque(maxlen=max_events)
        self._seq = 0
        self._lock = threading.Lock()    # one thread writes, the window reads

    def add(self, seen_time, seen_by, channel, found_time, on_time, delay_set_ms, delay_ms,
            late_ms, result, length_ms, pixels=None):
        with self._lock:
            self._seq += 1
            self._events.append(EjectEvent(
                self._seq, float(seen_time), seen_by, int(channel), float(found_time),
                None if on_time is None else float(on_time), _round(delay_set_ms), _round(delay_ms),
                _round(late_ms), result, _round(length_ms), dict(pixels or {})))

    def events_since(self, seq):
        """All stored rows with a running number greater than `seq`."""
        with self._lock:
            if not self._events or self._events[-1].seq <= seq:
                return []
            return [e for e in self._events if e.seq > seq]

    def clear(self):
        with self._lock:
            self._events.clear()


def write_csv(path, events):
    """Save rows to a CSV file (opens in Excel)."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["touched_line_at", "seen_by", "channel", "pc_saw_at", "pc_reaction_ms",
                         "led_on_at", "delay_set_ms", "delay_measured_ms", "late_by_ms", "result",
                         "object_length_ms",
                         "front_pixel_from", "front_pixel_to", "rear_pixel_from", "rear_pixel_to"])
        for e in events:
            front = e.pixels.get("FRONT", ("", ""))
            rear = e.pixels.get("REAR", ("", ""))
            writer.writerow([clock_text(e.seen_time, True), e.seen_by, e.channel,
                             clock_text(e.found_time, True), e.reaction_ms,
                             clock_text(e.on_time, True) if e.on_time is not None else "",
                             e.delay_set_ms, "" if e.delay_ms is None else e.delay_ms,
                             "" if e.late_ms is None else e.late_ms, e.result, e.length_ms,
                             front[0], front[1], rear[0], rear[1]])
