"""
core/event_log.py - The list of ejections (one row per object).

    seen at        seen by  channel  LED ON at      delay set  delay measured  FRONT pixels  REAR pixels
    16:52:03.125   BOTH     16       16:52:03.127   2.0        2.1             1000-1060     1012-1071

    seen at        = when the first camera saw the object (PC clock)
    seen by        = FRONT, REAR or BOTH
    LED ON at      = when the ejector LED switched ON
    delay set      = the delay you chose (ms)
    delay measured = LED ON at - seen at (ms), measured with the PC's precise clock
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
    """PC time -> '16:52:03.127' (or with date '2026-11-19 16:52:03.127')."""
    fmt = "%Y-%m-%d %H:%M:%S.%f" if with_date else "%H:%M:%S.%f"
    return datetime.datetime.fromtimestamp(t).strftime(fmt)[:-3]


class EjectEvent:
    """One ejection."""

    __slots__ = ("seq", "seen_time", "seen_by", "channel", "on_time", "delay_set_ms", "delay_ms",
                 "pixels")

    def __init__(self, seq, seen_time, seen_by, channel, on_time, delay_set_ms, delay_ms, pixels=None):
        self.seq = seq                    # running number 1, 2, 3 ... (never repeats)
        self.seen_time = seen_time        # PC clock (seconds) - first camera saw it
        self.seen_by = seen_by            # "FRONT" / "REAR" / "BOTH"
        self.channel = channel            # 1 ... 63
        self.on_time = on_time            # PC clock (seconds) - LED switched ON
        self.delay_set_ms = delay_set_ms  # delay chosen
        self.delay_ms = delay_ms          # delay measured
        self.pixels = pixels or {}        # {"FRONT": (1000, 1060), "REAR": (1012, 1071)}

    def pixels_text(self, camera):
        """e.g. '1000 - 1060', or '-' if this camera did not see it."""
        p = self.pixels.get(camera)
        return f"{p[0]} - {p[1]}" if p else "-"

    @property
    def difference_ms(self):
        """Measured - set (how late the LED was)."""
        return round(self.delay_ms - self.delay_set_ms, 1)


class EventLog:

    def __init__(self, max_events=20000):
        self._events = deque(maxlen=max_events)
        self._seq = 0
        self._lock = threading.Lock()    # one thread writes, the window reads

    def add(self, seen_time, seen_by, channel, on_time, delay_set_ms, delay_ms, pixels=None):
        with self._lock:
            self._seq += 1
            self._events.append(EjectEvent(self._seq, float(seen_time), seen_by, int(channel),
                                           float(on_time), round(float(delay_set_ms), 1),
                                           round(float(delay_ms), 1), pixels))

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
        writer.writerow(["seen_at", "seen_by", "channel", "led_on_at",
                         "delay_set_ms", "delay_measured_ms", "difference_ms",
                         "front_pixel_from", "front_pixel_to", "rear_pixel_from", "rear_pixel_to"])
        for e in events:
            front = e.pixels.get("FRONT", ("", ""))
            rear = e.pixels.get("REAR", ("", ""))
            writer.writerow([clock_text(e.seen_time, True), e.seen_by, e.channel,
                             clock_text(e.on_time, True), e.delay_set_ms, e.delay_ms,
                             e.difference_ms, front[0], front[1], rear[0], rear[1]])
