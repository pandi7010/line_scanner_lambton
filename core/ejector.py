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

Real valves (ESP32): set_valves(board) + arm(True). Then every LED ON / OFF is also sent
to the valve: ON at the same moment as the LED, OFF when the blow is over. A valve that
must stay open longer than 100 ms gets its ON re-sent every 100 ms (the board's safety
watchdog closes any valve after 250 ms without a new ON). Not armed = display only.
No PyQt5 here.
"""

import threading
import time
from collections import deque

import numpy as np

from core import realtime


SPIN_S = 0.0007     # the last 0.7 ms before an LED is due: check continuously (sleep is not exact)
FULL_CHECK_S = 0.001  # full check (new objects, object updates, log) at most every 1 ms; in between
                      # only the quick fire_due() - the full check 2,750x per second took the time
                      # the cameras needed (feeder load, 20 Nov 2026)
RESEND_S = 0.100    # open valve: send ON again every 100 ms (ESP32 watchdog closes it after 250 ms)


class EjectScheduler:

    def __init__(self, activities, event_log, delay_ms=10.0, blow_ms=10.0, same_object_ms=30.0,
                 late_tolerance_ms=1.0, max_wait_s=1.0, histories=None):
        """
        activities        : {"FRONT": ChannelActivity, "REAR": ChannelActivity}
        event_log         : core.EventLog - one row per object
        delay_ms          : touch -> LED ON
        blow_ms           : extra LED ON time after the object's last line (+ delay)
        same_object_ms    : FRONT + REAR in one channel within this time = the same object
        late_tolerance_ms : up to this much late still counts as ON TIME
        max_wait_s        : write the log row at the latest this long after the touch
        histories         : {"FRONT": FrameHistory, ...} - for object snaps in the log (None = no snaps)
        """
        self.activities = activities
        self.event_log = event_log
        self.channels = next(iter(activities.values())).channels
        self.delay = delay_ms / 1000.0
        self.blow = blow_ms / 1000.0
        self.same_object = same_object_ms / 1000.0
        self.late_tolerance = late_tolerance_ms / 1000.0
        self.max_wait = max_wait_s
        self.histories = {cam: h for cam, h in (histories or {}).items() if h is not None}

        self._checked = {cam: np.zeros(self.channels, dtype=np.int64) for cam in activities}
        self._last_object = {}           # channel index -> newest object in that channel
        self._pending = []               # objects waiting for their LED ON time
        self._to_log = []                # fired / missed objects, logged when they are finished
        self._write_queue = deque()      # finished objects -> the log-writer thread (pictures, numbers)
        self._writer = None
        self._fired_at = np.full(self.channels, -1e9)   # when each LED last switched ON
        self._on_until = np.full(self.channels, -1e9)   # when each LED switches OFF
        self._fired_by = [""] * self.channels           # "FRONT" / "REAR" / "BOTH"

        # Real valves (hardware.Esp32Ejector). None / not armed = LEDs on screen only.
        self.valves = None
        self.armed = False
        self._valve_open = np.zeros(self.channels, dtype=bool)
        self._valve_sent = np.full(self.channels, -1e9)  # when ON was last sent
        self.valve_count = 0                             # valve openings since the start

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

    # -------------------------------------------------------- real valves
    def set_valves(self, board):
        """board: hardware.Esp32Ejector (or None). Valves fire only after arm(True)."""
        with self._lock:
            self._close_all_valves()
            self.valves = board

    def arm(self, on):
        """True = fire the real valves, False = LEDs on screen only (all valves closed at once)."""
        with self._lock:
            self.armed = bool(on) and self.valves is not None
            if not self.armed:
                self._close_all_valves()
        self._wake.set()
        return self.armed

    def _close_all_valves(self):
        if self.valves is not None:
            self.valves.all_off()
        self._valve_open[:] = False

    def _switch_valves(self, now):
        """Close valves whose blow is over; re-send ON for valves that stay open (watchdog)."""
        for i in np.flatnonzero(self._valve_open):
            if now >= self._on_until[i]:
                self.valves.valve_off(int(i) + 1)
                self._valve_open[i] = False
            elif now - self._valve_sent[i] >= RESEND_S:
                self.valves.valve_on(int(i) + 1)
                self._valve_sent[i] = now

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
                       "on": None, "result": None, "valve": False}
                self._last_object[i] = obj
                self._pending.append(obj)

            # 2) Keep every waiting / fired object up to date (last line, still there?, pixels).
            for obj in self._pending + self._to_log:
                self._update_object(obj, snap)

            # 3) LED ON when the delay is over (or at once if the PC was late).
            self._fire_due(now)

            # Long object still passing: keep its LED ON (blow follows the object's length).
            for obj in self._to_log:
                if obj["on"] is not None:
                    i = obj["channel"] - 1
                    self._on_until[i] = max(self._on_until[i], obj["last"] + obj["delay_set"] + self.blow)

            # Real valves: OFF when the blow is over, ON re-sent for long blows.
            if self.armed:
                self._switch_valves(now)

            # 4) Log row when the object is finished (both cameras done) - at the latest after max_wait.
            #    The row itself (picture, object number) is written by the log-writer thread,
            #    NOT here: this thread only does the timing.
            keep = []
            for obj in self._to_log:
                finished = now >= obj["seen"] + self.same_object and not any(obj["open"].values())
                if finished or now - obj["seen"] > self.max_wait:
                    if self._writer is not None:
                        self._write_queue.append(obj)
                    else:
                        self._log(obj)       # tests without threads: write at once
                else:
                    keep.append(obj)
            self._to_log = keep

    def _fire_due(self, now):
        """Switch ON every waiting object whose time has come (lock must be held)."""
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
                if self.armed:
                    if not self._valve_open[i]:          # already open (same channel)? keep it open
                        self.valves.valve_on(obj["channel"])
                        self._valve_open[i] = True
                        self._valve_sent[i] = now
                        self.valve_count += 1
                    obj["valve"] = True
            self._to_log.append(obj)
        self._pending = still_waiting

    def fire_due(self, now=None):
        """QUICK check (the last moment before an LED is due): only fire what is due and
        switch the valves - no new objects, no log rows. ~10x cheaper than check_once."""
        now = time.perf_counter() if now is None else now
        with self._lock:
            self._fire_due(now)
            if self.armed:
                self._switch_valves(now)

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
            valve=obj.get("valve", False),
            snaps=self._make_snaps(obj),
        )

    def _make_snaps(self, obj):
        """Small pictures of this object from both cameras (a camera that did not see it:
        cut at the other camera's pixels, so you can see WHY it missed it)."""
        if not self.histories or not obj["pixels"]:
            return None
        snaps = {}
        fallback = next(iter(obj["pixels"].values()))
        for cam, history in self.histories.items():
            seen = cam in obj["pixels"]
            px_from, px_to = obj["pixels"].get(cam, fallback)
            try:
                snap = history.crop(obj["seen"], obj["last"], px_from, px_to, seen=seen)
            except Exception:            # noqa: BLE001  (a snap must never stop the ejector)
                snap = None
            if snap is not None:
                snaps[cam] = snap
        return snaps or None

    def next_fire_time(self):
        """Next moment something must happen: an LED / valve ON, a valve OFF or a valve re-send."""
        with self._lock:
            due = min((o["fire_at"] for o in self._pending), default=None)
            if self.armed and self._valve_open.any():
                is_open = self._valve_open
                valve_due = min(float(self._on_until[is_open].min()),
                                float(self._valve_sent[is_open].min()) + RESEND_S)
                due = valve_due if due is None else min(due, valve_due)
            return due

    @staticmethod
    def _label(cameras):
        return "BOTH" if len(cameras) > 1 else next(iter(cameras))

    # ---------------------------------------------------------- start / stop
    def start(self):
        self._running = True
        self._writer = threading.Thread(target=self._write_loop, name="ejector-log", daemon=True)
        self._writer.start()
        self._thread = threading.Thread(target=self._run, name="ejector", daemon=True)
        self._thread.start()

    def _write_loop(self):
        """Log-writer thread: picture + object number + row, every 20 ms. Normal priority:
        it may wait, the timing thread may not. Pictures need the frames of the last 0.6 s,
        so 20 ms is soon enough."""
        while self._running or self._write_queue:
            while self._write_queue:
                self._log(self._write_queue.popleft())
            if self._running:
                time.sleep(0.02)

    def stop(self):
        self._running = False
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._writer is not None:
            self._writer.join(timeout=2)
        with self._lock:                 # program ends: no valve may stay open
            self.armed = False
            self._close_all_valves()

    def _run(self):
        """
        Check about every 1 ms. When an LED is due soon, sleep until 0.7 ms before it,
        then check continuously (Windows sleep is not exact enough) - and after every
        check STEP ASIDE (time.sleep(0)) so the camera threads can run. Without that,
        this loop kept the camera threads waiting and frames were lost.
        """
        realtime.boost_this_thread("ejector")      # Windows: run this thread first
        mark = (time.monotonic(), time.thread_time())
        last_full = 0.0
        while self._running:
            now = time.perf_counter()
            if self._wake.is_set() or now - last_full >= FULL_CHECK_S:
                self._wake.clear()
                self.check_once(now)         # full: new objects, updates, LEDs, valves, log rows
                last_full = now
            else:
                self.fire_due(now)           # quick: only what is due right now
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
            elif left > 0.002:
                time.sleep(0.001)            # an LED is due later: 1 ms rhythm
            elif left > SPIN_S:
                time.sleep(left - SPIN_S)    # sleep until shortly before the LED is due
            else:
                time.sleep(0)                # last moment: check again at once, but let others run
