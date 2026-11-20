"""
tests/test_esp32.py - automatic test, NO ESP32 and NO cameras needed (a fake COM port).

    python tests\\test_esp32.py      -> must print  ALL TESTS PASSED

Checks the ejector board connection (hardware/esp32_ejector.py) and the real-valve
switching in core/ejector.py:
  * connect: tries 230400, then 115200; after connect: ALL OFF ('S') + watchdog ON ('W')
  * a port that does not answer is not used; a busy port gives a clear message
  * valve 17 ON = byte 17, OFF = byte 117; wrong numbers are never sent
  * NOT armed: LEDs only, no valve byte
  * armed: valve ON exactly at touch + delay, OFF when the blow is over
  * long object (> 100 ms): ON re-sent every 100 ms (board watchdog closes after 250 ms)
  * two objects in the same channel: the open valve stays open (no second ON)
  * disarm / stop: ALL OFF
  * the log says "valve fired" YES only when armed
NEEDS THE REAL MACHINE: the right COM port, the baud rate of the board, real valves.
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

assert "PyQt5" not in sys.modules, "core / hardware must not import PyQt5"
LT = 1.0 / 4800
failed = []


def check(name, ok):
    print(("  ok    " if ok else "  FAIL  ") + name)
    if not ok:
        failed.append(name)


class FakePort:
    """A fake COM port. `board_baud` = baud rate of the ESP32 on it (None = nothing answers)."""
    boards = {}            # port name -> baud rate of the ESP32 on it
    busy = set()
    opened = []

    def __init__(self, port, baud):
        if port in FakePort.busy:
            raise PermissionError("Access is denied")
        self.port, self.baud = port, baud
        self.written = bytearray()
        self.answer = b""
        self.closed = False
        FakePort.opened.append(self)

    def write(self, data):
        if self.closed:
            raise OSError("port closed")
        self.written += data
        if b"P" in data and FakePort.boards.get(self.port) == self.baud:
            self.answer += b"ACK: ESP32_ALIVE\r\n"
        return len(data)

    def read(self, n):
        out, self.answer = self.answer[:n], self.answer[n:]
        return bytes(out)

    def reset_input_buffer(self):
        self.answer = b""

    def flush(self):
        pass

    def close(self):
        self.closed = True


esp32_ejector.serial_factory = FakePort

# ------------------------------------------------------------------ 1) connect
print("Connect:")
FakePort.boards = {"COM24": 115200}
board = Esp32Ejector(threaded=False)
ok = board.connect("COM24")
port = FakePort.opened[-1]
check("board at 115200 found (230400 tried first)", ok and board.baud == 115200
      and [p.baud for p in FakePort.opened] == [230400, 115200])
check("after connect: all OFF + watchdog ON", bytes(port.written).endswith(b"SW"))

FakePort.boards = {"COM24": 230400}
board2 = Esp32Ejector()
check("board at 230400 found at once", board2.connect("COM24") and board2.baud == 230400)
board2.close()

board3 = Esp32Ejector()
check("silent port (feeder / nothing) -> not connected", not board3.connect("COM7") and not board3.connected)
FakePort.busy = {"COM9"}
check("busy port -> clear message", not board3.connect("COM9") and "another program" in board3.error)
FakePort.busy = set()

found = esp32_ejector.find_ejector_ports(["COM3", "COM24"])
check(f"find: only COM24 answers -> {found}", found == [("COM24", 230400)])

# ------------------------------------------------------------------ 2) valve bytes
print("Valve bytes:")
port.written.clear()
board.valve_on(17)
board.valve_off(17)
board.valve_on(0)
board.valve_on(64)
board.valve_off(99)
check("valve 17 ON = 17, OFF = 117, wrong numbers ignored", bytes(port.written) == bytes([17, 117]))

# sender thread: valve_on returns at once even if the USB write is slow, order is kept
print("Sender thread:")


class SlowPort(FakePort):
    def write(self, data):
        time.sleep(0.005)                      # a slow USB write: 5 ms
        return super().write(data)


esp32_ejector.serial_factory = SlowPort
FakePort.boards = {"COM30": 230400}
tb = Esp32Ejector()                            # threaded (normal use)
tb.connect("COM30")
tport = FakePort.opened[-1]
tb.wait_sent()
tport.written.clear()
t = time.perf_counter()
for ch in (5, 6, 7):
    tb.valve_on(ch)
for ch in (5, 6, 7):
    tb.valve_off(ch)
call_ms = (time.perf_counter() - t) * 1000
tb.wait_sent()
check(f"6 valve commands took {call_ms:.2f} ms for the caller (USB write 5 ms each)", call_ms < 2)
check(f"order kept: {list(tport.written)}", list(tport.written) == [5, 6, 7, 105, 106, 107])
tport.written.clear()
tb.valve_on(9)
tb.all_off()
tb.wait_sent()
check("ALL OFF still arrives last", bytes(tport.written).endswith(b"S"))
slowest, waiting = tb.read_write_stats()
check(f"write stats: slowest {slowest:.1f} ms, waiting {waiting}", slowest >= 4 and waiting == 0)
tb.close()
check("close: sender stopped, ALL OFF sent", tb._sender is None and bytes(tport.written).endswith(b"S"))
esp32_ejector.serial_factory = FakePort

# ------------------------------------------------------------------ 3) scheduler + real valves


class Scene:
    def __init__(self, delay_ms=10.0, blow_ms=10.0):
        self.act = {"FRONT": ChannelActivity(63), "REAR": ChannelActivity(63)}
        self.log = EventLog()
        self.ej = EjectScheduler(self.act, self.log, delay_ms=delay_ms, blow_ms=blow_ms)
        FakePort.boards = {"COM24": 230400}
        self.board = Esp32Ejector(threaded=False)      # write at once: exact times for the checks
        self.board.connect("COM24")
        self.port = FakePort.opened[-1]
        self.ej.set_valves(self.board)
        self.events = []          # (time, byte) written by the scheduler
        self.port.written.clear()

    def frame(self, frame_end, channel_rows, found):
        rows = np.zeros((16, 63), dtype=bool)
        for ch, lines in channel_rows.items():
            rows[lines, ch - 1] = True
        times = CameraClock.line_times(frame_end, LT, 16)
        self.act["FRONT"].mark_rows(rows, times, found)
        return times

    def run(self, t_from, t_to, step=0.0001):
        for t in np.arange(t_from, t_to, step):
            before = len(self.port.written)
            self.ej.check_once(now=float(t))
            for b in self.port.written[before:]:
                self.events.append((float(t), b))


print("Scheduler:")
s = Scene()
times = s.frame(100.0, {17: [5, 6, 7]}, found=100.003)
s.run(100.003, 100.1)
check("NOT armed: no valve byte (LEDs only)", s.events == [] and s.log.events_since(0)[0].valve is False)

s = Scene(delay_ms=10.0, blow_ms=10.0)
s.ej.arm(True)
s.port.written.clear()
times = s.frame(200.0, {17: [5, 6, 7]}, found=200.003)
s.frame(200.0 + 16 * LT, {}, found=200.0 + 16 * LT)
s.run(200.003, 200.1)
on = [t for t, b in s.events if b == 17]
off = [t for t, b in s.events if b == 117]
touched, last_line = times[5], times[7]
check(f"armed: valve ON at touch + 10 ms (late {(on[0] - touched - 0.010) * 1000:.2f} ms)",
      len(on) == 1 and abs(on[0] - (touched + 0.010)) <= 0.0002)
check(f"valve OFF after last line + delay + blow (open {(off[0] - on[0]) * 1000:.1f} ms)",
      len(off) == 1 and abs(off[0] - (last_line + 0.020)) <= 0.0002)
check("log: valve fired = YES", s.log.events_since(0)[0].valve is True)

# long object: 300 ms in channel 30 -> ON re-sent every 100 ms
s = Scene()
s.ej.arm(True)
s.port.written.clear()
t = 300.0
for k in range(int(0.3 / (16 * LT))):
    s.frame(t, {30: list(range(16))}, found=t)
    s.run(t, t + 16 * LT, step=0.0005)
    t += 16 * LT
s.frame(t, {}, found=t)
s.run(t, t + 0.1, step=0.0005)
ons = [x for x, b in s.events if b == 30]
gaps = np.diff(ons) if len(ons) > 1 else np.array([1.0])
check(f"long object: ON sent {len(ons)} times, max gap {gaps.max() * 1000:.0f} ms (< 250 ms watchdog)",
      len(ons) >= 3 and gaps.max() <= 0.105)
check("long object: closed once at the end", [b for _, b in s.events].count(130) == 1)

# two objects in channel 40, the second while the valve is still open -> stays open
s = Scene(delay_ms=10.0, blow_ms=10.0)
s.ej.arm(True)
s.port.written.clear()
s.frame(400.0, {40: [13, 14]}, found=400.002)                # first object, valve open ~399.9998 + 10 ... + 20 ms
s.frame(400.0 + 16 * LT, {}, found=400.0 + 16 * LT)
s.run(400.002, 400.0068)
s.frame(400.0067, {40: [14, 15]}, found=400.0068)             # second object ~6 ms later
s.frame(400.0067 + 16 * LT, {}, found=400.0068 + 16 * LT)
s.run(400.0068, 400.1)
codes = [b for _, b in s.events]
check(f"two objects, valve still open: one ON, one OFF {codes}", codes.count(40) == 1 and codes.count(140) == 1)

# disarm + stop -> ALL OFF
s.port.written.clear()
s.ej.arm(False)
check("disarm -> ALL OFF", bytes(s.port.written) == b"S")
s.ej.arm(True)
s.port.written.clear()
s.ej.stop()
check("program stops -> ALL OFF", b"S" in bytes(s.port.written) and not s.ej.armed)

# arm without a board does nothing
e2 = EjectScheduler({"FRONT": ChannelActivity(63)}, EventLog())
check("arm without a board -> stays display only", e2.arm(True) is False)

# lost connection while armed -> no crash
s = Scene()
s.ej.arm(True)
s.port.closed = True
s.frame(500.0, {5: [3]}, found=500.002)
s.run(500.002, 500.05)
check("USB cable pulled -> no crash, board shows lost connection", not s.board.connected
      and "lost" in s.board.error)

print()
print("ALL TESTS PASSED" if not failed else f"{len(failed)} TEST(S) FAILED: {failed}")
sys.exit(1 if failed else 0)
