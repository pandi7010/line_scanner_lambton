"""
core/ejector.py - Decides WHEN each ejector LED switches ON (delay + blow time).

What happens to one object (example: delay 2 ms, blow 10 ms):

    10:00:00.000  FRONT camera sees a new object in channel 16   <- "seen at"
                  (REAR may see it too, a few ms later -> same object, "seen by BOTH")
    10:00:00.002  ejector LED 16 switches ON                     <- "LED ON at" (seen + delay)
    10:00:00.012  ejector LED 16 switches OFF                    (ON + blow time)

    Log row:  seen at .000 | BOTH | ch 16 | LED ON at .002 | delay set 2.0 | measured 2.1 ms
              | FRONT pixels 1000-1060 | REAR pixels 1012-1071

Rules:
  * EITHER camera can trigger an ejection (like lambton-main merges both cameras).
  * FRONT and REAR seeing the same channel within `same_object_ms` (150 ms) = ONE object
    -> one ejection, counted from the FIRST camera that saw it.
  * The same camera seeing a channel switch ON again = a NEW object.
  * "Delay measured" = LED ON time - seen time, from the PC's precise clock. It shows how
    exactly the PC keeps the delay (Windows timers are not perfect: expect +0 ... +1 ms).

Display only - no valve is fired. No PyQt5 here.

    scheduler = EjectScheduler({"FRONT": front.activity, "REAR": rear.activity}, event_log)
    scheduler.set_timing(delay_ms=5, blow_ms=10)
    scheduler.start() ... scheduler.lit_channels()  ... scheduler.stop()
"""

import threading
import time

import numpy as np


class EjectScheduler:

    def __init__(self, activities, event_log, delay_ms=2.0, blow_ms=10.0, same_object_ms=150.0):
        """
        activities     : {"FRONT": ChannelActivity, "REAR": ChannelActivity}
        event_log      : core.EventLog that receives one row per ejection
        delay_ms       : time from "camera saw it" to "LED ON"
        blow_ms        : how long the LED (valve) stays ON
        same_object_ms : FRONT + REAR within this time in one channel = the same object
        """
        self.activities = activities
        self.event_log = event_log
        self.channels = next(iter(activities.values())).channels
        self.delay = delay_ms / 1000.0
        self.blow = blow_ms / 1000.0
        self.same_object = same_object_ms / 1000.0

        self._checked = {cam: np.zeros(self.channels, dtype=np.int64) for cam in activities}
        self._last_object = {}           # channel index -> newest object in that channel
        self._pending = []               # objects waiting for their delay
        self._to_log = []                # fired objects, logged once "seen by" is final
        self._fired_at = np.full(self.channels, -1e9)   # when each LED last switched ON
        self._fired_by = [""] * self.channels           # "FRONT" / "REAR" / "BOTH"

        # perf_counter (precise) -> PC clock time, for the times shown in the log
        self._clock_offset = time.time() - time.perf_counter()
        self._lock = threading.Lock()
        self._running = False
        self._thread = None

    # ------------------------------------------------------------- settings
    def set_timing(self, delay_ms=None, blow_ms=None):
        """Change delay / blow time. Objects already waiting keep their old delay."""
        with self._lock:
            if delay_ms is not None:
                self.delay = max(0.0, float(delay_ms)) / 1000.0
            if blow_ms is not None:
                self.blow = max(0.0, float(blow_ms)) / 1000.0

    def get_timing(self):
        with self._lock:
            return self.delay * 1000.0, self.blow * 1000.0

    # ------------------------------------------------------- for the screen
    def lit_channels(self, min_visible_ms=150.0, now=None):
        """
        {channel: "FRONT"/"REAR"/"BOTH"} of LEDs to show ON now.
        A 10 ms blow is too short to see on a 25 Hz screen, so each LED is shown
        for at least `min_visible_ms` after it switched ON.
        """
        now = time.perf_counter() if now is None else now
        with self._lock:
            show_for = max(self.blow, min_visible_ms / 1000.0)
            idx = np.flatnonzero(now - self._fired_at <= show_for)
            return {int(i) + 1: self._fired_by[i] for i in idx}

    # -------------------------------------------------------------- the work
    def check_once(self, now=None):
        """1) find new objects  2) switch LEDs ON when their delay is over  3) write log rows."""
        now = time.perf_counter() if now is None else now

        # 1) New objects from both cameras, in time order.
        snap = {cam: activity.snapshot() for cam, activity in self.activities.items()}
        starts = []
        for cam, (_, start_time, start_count, _, _) in snap.items():
            for i in np.flatnonzero(start_count > self._checked[cam]):
                starts.append((float(start_time[i]), cam, int(i)))
            self._checked[cam] = start_count
        starts.sort()

        with self._lock:
            for seen, cam, i in starts:
                count = int(snap[cam][2][i])            # which object of this camera/channel
                obj = self._last_object.get(i)
                if obj and cam not in obj["by"] and seen - obj["seen"] <= self.same_object:
                    obj["by"].add(cam)                  # other camera, same object
                    obj["count"][cam] = count
                    continue
                obj = {"channel": i + 1, "seen": seen, "by": {cam}, "count": {cam: count},
                       "pixels": {}, "delay_set": self.delay, "fire_at": seen + self.delay, "on": None}
                self._last_object[i] = obj
                self._pending.append(obj)

            # Keep each object's pixel range up to date while that object is still the
            # newest one in its channel (a newer object must not overwrite it).
            for obj in self._pending + self._to_log:
                self._update_pixels(obj, snap)

            # 2) Delay over -> LED ON.
            still_waiting = []
            for obj in self._pending:
                if now >= obj["fire_at"]:
                    i = obj["channel"] - 1
                    obj["on"] = now
                    self._fired_at[i] = now
                    self._fired_by[i] = self._label(obj["by"])
                    self._to_log.append(obj)
                else:
                    still_waiting.append(obj)
            self._pending = still_waiting

            # 3) Log when "seen by" can no longer change (same_object time passed).
            keep = []
            for obj in self._to_log:
                if now >= obj["seen"] + self.same_object:
                    self.event_log.add(
                        pixels=obj["pixels"],
                        seen_time=obj["seen"] + self._clock_offset,
                        seen_by=self._label(obj["by"]),
                        channel=obj["channel"],
                        on_time=obj["on"] + self._clock_offset,
                        delay_set_ms=obj["delay_set"] * 1000.0,
                        delay_ms=(obj["on"] - obj["seen"]) * 1000.0,
                    )
                else:
                    keep.append(obj)
            self._to_log = keep

    @staticmethod
    def _update_pixels(obj, snap):
        """obj["pixels"][camera] = (first pixel, last pixel), while it is still the same object."""
        i = obj["channel"] - 1
        for cam, count in obj["count"].items():
            _, _, start_count, pixel_from, pixel_to = snap[cam]
            if int(start_count[i]) == count and not np.isnan(pixel_from[i]):
                obj["pixels"][cam] = (int(pixel_from[i]), int(pixel_to[i]))

    def next_fire_time(self):
        with self._lock:
            return min((o["fire_at"] for o in self._pending), default=None)

    @staticmethod
    def _label(cameras):
        return "BOTH" if len(cameras) > 1 else next(iter(cameras))

    # ---------------------------------------------------------- start / stop
    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, name="ejector", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self):
        """
        Check about every 1 ms. When an LED is due in less than 2 ms we do not
        sleep (Windows sleep is not precise enough) but check continuously.
        """
        while self._running:
            self.check_once()
            due = self.next_fire_time()
            if due is not None and due - time.perf_counter() < 0.002:
                continue                     # an LED is due very soon: no sleep
            time.sleep(0.001)
