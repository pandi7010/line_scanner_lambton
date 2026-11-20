"""
core/ejector.py - Decides WHEN each ejector LED switches ON (from the EXACT touch time).

What happens to one object (example: delay 10 ms, blow 10 ms):

    10:00:00.000  object touches the decision line (exact line time from the camera clock)
    10:00:00.003  the PC finds it in the frame                 -> PC reaction 3.0 ms
    10:00:00.010  ejector LED ON  (touch + delay)              -> ON TIME, late by 0.0 ms
    10:00:00.021  ejector LED OFF (last line of the object + delay + blow)

    If the PC finds it only at 10:00:00.012, the LED comes ON at once, 2 ms late -> LATE.
    If the PC finds it after the whole blow window is over, the LED stays OFF -> MISSED
    (a blast now would only hit the wrong grain - same rule as lambton-main capture_timing).

Rules:
  * EITHER camera triggers. FRONT and REAR touching the same channel within
    `same_object_ms` (30 ms, real camera times) = ONE object = one ejection.
  * The delay is counted from the camera that touched FIRST (also if that camera's
    frame reached the PC later).
  * Blow time grows with the object's length: LED OFF = last line + delay + blow.
  * Result: ON TIME (late <= late_tolerance_ms), LATE, or MISSED.

Display only - no valve is fired. No PyQt5 here.
"""

import threading
import time

import numpy as np


SPIN_S = 0.0007     # the last 0.7 ms before an LED is due: check continuously (sleep is not exact)


class EjectScheduler:

    def __init__(self, activities, event_log, delay_ms=10.0, blow_ms=10.0, same_object_ms=30.0,
                 late_tolerance_ms=1.0, max_wait_s=1.0):
        """
        activities        : {"FRONT": ChannelActivity, "REAR": ChannelActivity}
        event_log         : core.EventLog - one row per object
        delay_ms          : touch -> LED ON
        blow_ms           : extra LED ON time after the object's last line (+ delay)
        same_object_ms    : FRONT + REAR in one channel within this time = the same object
        late_tolerance_ms : up to this much late still counts as ON TIME
        max_wait_s        : write the log row at the latest this long after the touch
        """
        self.activities = activities
        self.event_log = event_log
        self.channels = next(iter(activities.values())).channels
        self.delay = delay_ms / 1000.0
        self.blow = blow_ms / 1000.0
        self.same_object = same_object_ms / 1000.0
        self.late_tolerance = late_tolerance_ms / 1000.0
        self.max_wait = max_wait_s

        self._checked = {cam: np.zeros(self.channels, dtype=np.int64) for cam in activities}
        self._last_object = {}           # channel index -> newest object in that channel
        self._pending = []               # objects waiting for their LED ON time
        self._to_log = []                # fired / missed objects, logged when they are finished
        self._fired_at = np.full(self.channels, -1e9)   # when each LED last switched ON
        self._on_until = np.full(self.channels, -1e9)   # when each LED switches OFF
        self._fired_by = [""] * self.channels           # "FRONT" / "REAR" / "BOTH"

        # perf_counter (precise) -> PC clock time, for the times shown in the log
        self._clock_offset = time.time() - time.perf_counter()
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self.cpu_percent = 0.0           # CPU used by the ejector thread (100 = one full core)
        # The cameras wake us when a NEW object starts, so we do not have to check 1000x per second.
        self._wake = threading.Event()
        for activity in activities.values():
            if hasattr(activity, "listeners"):
                activity.listeners.append(self._wake.set)

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
        Shown while really ON, and at least `min_visible_ms` (a 10 ms blow is too short to see).
        """
        now = time.perf_counter() if now is None else now
        with self._lock:
            show = (now <= self._on_until) | (now - self._fired_at <= min_visible_ms / 1000.0)
            show &= self._fired_at > -1e8
            return {int(i) + 1: self._fired_by[i] for i in np.flatnonzero(show)}

    # -------------------------------------------------------------- the work
    def check_once(self, now=None):
        """1) new objects  2) keep objects up to date  3) LED ON when due  4) write log rows."""
        now = time.perf_counter() if now is None else now

        # 1) New objects from both cameras, in real (camera) time order.
        snap = {cam: activity.snapshot() for cam, activity in self.activities.items()}
        starts = []
        for cam, (_, start_time, start_count, _, _, found_time, _) in snap.items():
            for i in np.flatnonzero(start_count > self._checked[cam]):
                starts.append((float(start_time[i]), cam, int(i), int(start_count[i]), float(found_time[i])))
            self._checked[cam] = start_count
        starts.sort()

        with self._lock:
            for touched, cam, i, count, found in starts:
                obj = self._last_object.get(i)
                if obj and cam not in obj["count"] and abs(touched - obj["seen"]) <= self.same_object:
                    obj["count"][cam] = count            # the other camera, same object
                    obj["found"] = min(obj["found"], found)
                    if touched < obj["seen"] and obj["on"] is None and obj["result"] is None:
                        obj["seen"] = touched            # this camera touched FIRST: count from here
                        obj["fire_at"] = touched + obj["delay_set"]
                    continue
                obj = {"channel": i + 1, "seen": touched, "found": found, "last": touched,
                       "count": {cam: count}, "open": {cam: True}, "pixels": {},
                       "delay_set": self.delay, "fire_at": touched + self.delay,
                       "on": None, "result": None}
                self._last_object[i] = obj
                self._pending.append(obj)

            # 2) Keep every waiting / fired object up to date (last line, still there?, pixels).
            for obj in self._pending + self._to_log:
                self._update_object(obj, snap)

            # 3) LED ON when the delay is over (or at once if the PC was late).
            still_waiting = []
            for obj in self._pending:
                if now < obj["fire_at"]:
                    still_waiting.append(obj)
                    continue
                i = obj["channel"] - 1
                window_end = obj["last"] + obj["delay_set"] + self.blow       # planned LED OFF
                if now > window_end and not any(obj["open"].values()):
                    obj["result"] = "MISSED"             # too late for this object: no LED
                else:
                    obj["on"] = now
                    late = now - obj["fire_at"]
                    obj["result"] = "ON TIME" if late <= self.late_tolerance else "LATE"
                    self._fired_at[i] = now
                    self._fired_by[i] = self._label(obj["count"])
                    self._on_until[i] = max(self._on_until[i], window_end, now + self.blow)
                self._to_log.append(obj)
            self._pending = still_waiting

            # Long object still passing: keep its LED ON (blow follows the object's length).
            for obj in self._to_log:
                if obj["on"] is not None:
                    i = obj["channel"] - 1
                    self._on_until[i] = max(self._on_until[i], obj["last"] + obj["delay_set"] + self.blow)

            # 4) Log row when the object is finished (both cameras done) - at the latest after max_wait.
            keep = []
            for obj in self._to_log:
                finished = now >= obj["seen"] + self.same_object and not any(obj["open"].values())
                if finished or now - obj["seen"] > self.max_wait:
                    self._log(obj)
                else:
                    keep.append(obj)
            self._to_log = keep

    def _update_object(self, obj, snap):
        i = obj["channel"] - 1
        for cam, count in obj["count"].items():
            last_seen, _, start_count, pixel_from, pixel_to, _, is_open = snap[cam]
            if int(start_count[i]) == count:             # still this object in that camera
                obj["last"] = max(obj["last"], float(last_seen[i]))
                obj["open"][cam] = bool(is_open[i])
                if not np.isnan(pixel_from[i]):
                    obj["pixels"][cam] = (int(pixel_from[i]), int(pixel_to[i]))
            else:
                obj["open"][cam] = False                 # a newer object started -> this one ended

    def _log(self, obj):
        off = self._clock_offset
        on = obj["on"]
        self.event_log.add(
            seen_time=obj["seen"] + off,
            seen_by=self._label(obj["count"]),
            channel=obj["channel"],
            found_time=obj["found"] + off,
            on_time=None if on is None else on + off,
            delay_set_ms=obj["delay_set"] * 1000.0,
            delay_ms=None if on is None else (on - obj["seen"]) * 1000.0,
            late_ms=None if on is None else max(0.0, on - obj["fire_at"]) * 1000.0,
            result=obj["result"],
            length_ms=(obj["last"] - obj["seen"]) * 1000.0,
            pixels=obj["pixels"],
        )

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
        Check about every 1 ms. When an LED is due soon, sleep until 0.7 ms before it,
        then check continuously (Windows sleep is not exact enough) - and after every
        check STEP ASIDE (time.sleep(0)) so the camera threads can run. Without that,
        this loop kept the camera threads waiting and frames were lost.
        """
        mark = (time.monotonic(), time.thread_time())
        while self._running:
            self.check_once()
            now = time.monotonic()
            if now - mark[0] >= 1.0:
                cpu = time.thread_time()
                self.cpu_percent = (cpu - mark[1]) / (now - mark[0]) * 100
                mark = (now, cpu)
            due = self.next_fire_time()
            left = None if due is None else due - time.perf_counter()
            if left is None:
                # Nothing waiting: sleep until a camera reports a new object
                # (look again every 10 ms while log rows are still open, else every 100 ms).
                self._wake.wait(0.01 if self._to_log else 0.1)
                self._wake.clear()
            elif left > 0.002:
                time.sleep(0.001)            # an LED is due later: 1 ms rhythm
            elif left > SPIN_S:
                time.sleep(left - SPIN_S)    # sleep until shortly before the LED is due
            else:
                time.sleep(0)                # last moment: check again at once, but let others run
