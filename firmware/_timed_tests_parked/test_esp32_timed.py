"""
tests/test_esp32_timed.py - automatic test, NO ESP32 needed: a fake board that works like
firmware/ESP32_63CH_Timed_v1 (same commands, same pulse joining, its own clock).

    python tests\\test_esp32_timed.py      -> must print  ALL TESTS PASSED

Checks the TIMED firmware path ("the ESP32 does the timing"):
  * connect: the board answers 'V' with ESP32_TIMED_V1 -> board.timed = True
  * old esp.ino (no answer to 'V') -> board.timed = False (the PC times the valves as before)
  * pulse bytes: 0xF0, channel, delay, length, check (10 us steps)
  * object found 4 ms after the touch, delay 10 ms -> the ESP32 opens the valve exactly at
    touch + 10 ms, EVEN IF THE PC STOPS for 30 ms right after sending  <- the whole point
  * found after touch + delay (PC late) -> valve at once -> LATE; found after the blow -> MISSED
  * long object: extensions -> one long blast, closed at last line + delay + blow
  * the other camera saw it earlier -> the blast starts earlier
  * damaged command -> thrown away;  disarm -> 'S' cancels every waiting pulse
NEEDS THE REAL MACHINE: compiling + uploading the firmware, the real USB delay (usb_ms).
"""
import os
import sys
import time

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # project folder
sys.path.insert(0, os.getcwd())

import numpy as np  # noqa: E402

from core import CameraClock, ChannelActivity, EjectScheduler, EventLog  # noqa: E402
from hardware import esp32_ejector  # noqa: E402
from hardware.esp32_ejector import Esp32Ejector  # noqa: E402

LT = 1.0 / 4800
USB = 0.001                       # fake USB delay = what the PC assumes (usb_ms 1.0)
failed = []


def check(name, ok):
    print(("  ok    " if ok else "  FAIL  ") + name)
    if not ok:
        failed.append(name)


class FakeTimedBoard:
    """Fake COM port + the logic of ESP32_63CH_Timed_v1.ino. Bytes 'arrive' USB seconds after
    the PC wrote them (PC clock). Records when every valve is open: self.blasts[ch] = [(on, off)]."""
    timed = True

    def __init__(self, port, baud):
        self.answer = b""
        self.frame, self.in_frame = [], False
        self.pulses = {}                 # ch -> [[start, end]]
        self.manual = set()
        self.bad = 0
        self.raw = bytearray()

    # --- what the PC sees
    def write(self, data):
        arrive = time.perf_counter() + USB if not hasattr(self, "clock") else self.clock() + USB
        self.raw += data
        for b in data:
            self._byte(b, arrive)
        return len(data)

    def read(self, n):
        a, self.answer = self.answer, b""
        return a

    def reset_input_buffer(self):
        pass

    def flush(self):
        pass

    def close(self):
        pass

    # --- the firmware
    def _byte(self, b, now):
        if self.in_frame:
            self.frame.append(b)
            if len(self.frame) == 6:
                self.in_frame = False
                self._timed(now)
            return
        if b == 0xF0:
            self.in_frame, self.frame = True, []
        elif b == ord("P"):
            self.answer += b"ACK: ESP32_ALIVE\r\n"
        elif b == ord("V") and self.timed:
            self.answer += b"VERSION: ESP32_TIMED_V1\r\n"
        elif b in (0, ord("R"), ord("S")):
            self.pulses.clear()
            self.manual.clear()
        elif 1 <= b <= 63:
            self.manual.add(b)
        elif 101 <= b <= 163:
            self.manual.discard(b - 100)

    def _timed(self, now):
        ch, dl, dh, ll, lh, chk = self.frame
        if ch ^ dl ^ dh ^ ll ^ lh != chk or not 1 <= ch <= 63:
            self.bad += 1
            return
        start = now + (dl | dh << 8) * 1e-5
        end = start + (ll | lh << 8) * 1e-5
        for p in self.pulses.setdefault(ch, []):
            if start <= p[1] and p[0] <= end:          # overlap / touch -> join
                p[0], p[1] = min(p[0], start), max(p[1], end)
                return
        self.pulses[ch].append([start, end])


class OldBoard(FakeTimedBoard):
    timed = False


def make(timed=True, clock=None):
    esp32_ejector.serial_factory = FakeTimedBoard if timed else OldBoard
    board = Esp32Ejector(threaded=False)
    board.connect("COM6")
    return board


print("Connect:")
b = make(True)
check("timed firmware recognised", b.timed and "ESP32 does the timing" in b.status_text())
b_old = make(False)
check("old esp.ino -> board.timed = False", not b_old.timed and "PC does the timing" in b_old.status_text())

print("Bytes:")
port = esp32_ejector.serial_factory  # noqa: F841
b = make(True)
fake = b._serial
fake.raw.clear()
t = time.perf_counter()
info = {}
b.valve_pulse(17, t + 0.0062 + USB, t + 0.0182 + USB, info)
raw = list(fake.raw)
delay = (raw[2] | raw[3] << 8) * 10
length = (raw[4] | raw[5] << 8) * 10
check(f"frame {raw}: 0xF0, ch 17, delay {delay} us, length {length} us, check ok",
      raw[0] == 0xF0 and raw[1] == 17 and abs(delay - 6200) <= 30 and abs(length - 12000) <= 30
      and raw[6] == raw[1] ^ raw[2] ^ raw[3] ^ raw[4] ^ raw[5])
check("info: when the ESP32 will switch ON / OFF", abs(info["on"] - (t + 0.0062 + USB)) < 0.0002
      and abs(info["off"] - info["on"] - 0.012) < 0.0001)
check("old firmware: valve_pulse refused", b_old.valve_pulse(17, t, t + 0.01) is False)


class Scene:
    def __init__(self, delay_ms=10.0, blow_ms=4.0):
        self.act = {"FRONT": ChannelActivity(63, end_gap=8), "REAR": ChannelActivity(63, end_gap=8)}
        self.log = EventLog()
        self.ej = EjectScheduler(self.act, self.log, delay_ms=delay_ms, blow_ms=blow_ms)
        self.board = make(True)
        self.fake = self.board._serial
        self.now = 0.0
        self.fake.clock = lambda: self.now          # the PC clock of this test
        self.ej.set_valves(self.board)
        self.ej.arm(True)
        self.fake.pulses.clear()

    def frame(self, cam, frame_end, rows_by_channel, found):
        rows = np.zeros((16, 63), dtype=bool)
        for ch, lines in rows_by_channel.items():
            rows[lines, ch - 1] = True
        times = CameraClock.line_times(frame_end, LT, 16)
        self.act[cam].mark_rows(rows, times, found)
        return times

    def check_at(self, t):
        self.now = t
        # the board's own clock: perf_counter is replaced by the test clock
        real = time.perf_counter
        time.perf_counter = lambda: t
        try:
            self.ej.check_once(now=t)
        finally:
            time.perf_counter = real


print("Ejector with the timed board:")
# object touches at line 3 of a frame ending at 100.0; PC checks 4 ms later, then STOPS 30 ms
s = Scene(delay_ms=10.0, blow_ms=4.0)
times = s.frame("FRONT", 100.0, {20: [3, 4, 5, 6]}, found=100.0)
s.frame("FRONT", 100.0 + 16 * LT, {}, found=100.0)          # object over (8 empty lines later)
touch, last = times[3], times[6]
s.check_at(touch + 0.004)                                     # found 4 ms after the touch
s.check_at(touch + 0.040)                                     # next check only 36 ms later (PC stalled)
s.check_at(touch + 0.200)                                     # row written
blast = s.fake.pulses.get(20, [])
check(f"PC stalled 36 ms: ESP32 opens at touch + {(blast[0][0] - touch) * 1000:.2f} ms (10.00 wanted)",
      len(blast) == 1 and abs(blast[0][0] - (touch + 0.010)) < 0.00005)
check(f"closes at last line + delay + blow ({(blast[0][1] - last) * 1000:.2f} ms after the last line, 14 wanted)",
      abs(blast[0][1] - (last + 0.014)) < 0.00005)
row = s.log.events_since(0)[0]
check(f"log: {row.result}, late {row.late_ms} ms, valve fired {row.valve}",
      row.result == "ON TIME" and row.late_ms == 0.0 and row.valve)

# PC finds it 12 ms after the touch (delay 10) -> ESP32 opens as soon as the bytes arrive -> LATE
s = Scene(delay_ms=10.0, blow_ms=4.0)
times = s.frame("FRONT", 200.0, {5: [3, 4, 5, 6, 7, 8, 9, 10]}, found=200.0)
s.frame("FRONT", 200.0 + 16 * LT, {}, found=200.0)
touch = times[3]
s.check_at(touch + 0.012)
s.check_at(touch + 0.2)
blast = s.fake.pulses.get(5, [])
row = s.log.events_since(0)[0]
check(f"PC late (found after 12 ms): valve at once ({(blast[0][0] - touch) * 1000:.1f} ms), log LATE "
      f"{row.late_ms} ms", len(blast) == 1 and row.result == "LATE" and abs(row.late_ms - 3.0) < 0.2)

# found after the whole blow window -> nothing sent, MISSED
s = Scene(delay_ms=10.0, blow_ms=4.0)
times = s.frame("FRONT", 300.0, {7: [3]}, found=300.0)
s.frame("FRONT", 300.0 + 16 * LT, {}, found=300.0)
s.check_at(times[3] + 0.030)
s.check_at(times[3] + 0.2)
row = s.log.events_since(0)[0]
check("found after the blow window -> no blast, MISSED", 7 not in s.fake.pulses and row.result == "MISSED"
      and not row.valve)

# long object: 60 ms in channel 30 -> extensions -> ONE blast to last line + delay + blow
s = Scene(delay_ms=10.0, blow_ms=4.0)
t = 400.0
first = None
for k in range(18):                                         # 18 frames x 3.33 ms = 60 ms
    times = s.frame("FRONT", t, {30: list(range(16))}, found=t)
    first = first if first is not None else times[0]
    s.check_at(t + 0.002)
    t += 16 * LT
s.frame("FRONT", t, {}, found=t)
s.check_at(t + 0.002)
s.check_at(t + 0.3)
blast = s.fake.pulses.get(30, [])
last_line = t - 16 * LT
check(f"long object: {len(blast)} blast, {(blast[0][1] - blast[0][0]) * 1000:.1f} ms long (~64 wanted)",
      len(blast) == 1 and abs(blast[0][0] - (first + 0.010)) < 0.0001
      and abs(blast[0][1] - (last_line + 0.014)) < 0.0003)

# REAR touched 2 ms earlier but was found later -> blast moves earlier
s = Scene(delay_ms=10.0, blow_ms=4.0)
tf = s.frame("FRONT", 500.0, {40: [10, 11]}, found=500.0)
s.check_at(500.003)
tr = s.frame("REAR", 500.0, {40: [0, 1]}, found=500.0)      # REAR's lines 0,1 = ~2 ms earlier
s.frame("FRONT", 500.0 + 16 * LT, {}, found=500.0)
s.frame("REAR", 500.0 + 16 * LT, {}, found=500.0)
s.check_at(500.005)
s.check_at(500.2)
blast = s.fake.pulses.get(40, [])
check(f"other camera earlier: blast starts at REAR touch + 10 ms ({(blast[0][0] - tr[0]) * 1000:.2f})",
      len(blast) == 1 and abs(blast[0][0] - (tr[0] + 0.010)) < 0.00005)

print("Firmware model:")
fake = FakeTimedBoard("X", 0)
fake._byte(0xF0, 1.0)
for x in (17, 0x10, 0x02, 0x60, 0x04, 0x00):                 # wrong check byte
    fake._byte(x, 1.0)
check("damaged command -> thrown away (no blast)", fake.bad == 1 and not fake.pulses)
s = Scene()
s.board.valve_pulse(9, 1.0, 1.01)
s.ej.arm(False)
check("disarm -> 'S' -> every waiting pulse cancelled", not s.fake.pulses)

print()
print("ALL TESTS PASSED" if not failed else f"{len(failed)} TEST(S) FAILED: {failed}")
sys.exit(1 if failed else 0)
