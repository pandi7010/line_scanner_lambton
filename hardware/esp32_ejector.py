"""
hardware/esp32_ejector.py - Talks to the ejector ESP32 (63 valves) over USB (COM port).

Firmware on the machine: lambton-main esp/esp.ino ("ESP_BYTE" protocol, 8 x 74HC595).
One byte = one command:

    1 ... 63      valve n ON          (example: byte 17  -> valve 17 ON)
    101 ... 163   valve n OFF         (example: byte 117 -> valve 17 OFF)
    'S'           ALL valves OFF
    'P'           "are you there?"    -> the board answers "ACK: ESP32_ALIVE"
    'W'           safety watchdog ON: the board itself closes any valve that is ON
                  longer than 250 ms (if the PC hangs or the USB cable is pulled,
                  no valve stays open). The program re-sends ON every 100 ms for
                  valves that must stay open longer.

Baud rate: 230400 (lambton-main since 18 Nov 2026) or 115200 (older esp.ino).
connect() tries both and keeps the one where the board answers.

    ej = Esp32Ejector()
    ej.connect("COM24")          # -> True, ej.baud = 230400
    ej.valve_on(17); ej.valve_off(17)
    ej.all_off(); ej.close()

    find_ejector_ports()          # which COM port answers "ESP32_ALIVE"?

SENDING: valve_on / valve_off only put the byte in a queue and return at once (microseconds).
A small "sender" thread writes the queue to USB. A USB write takes ~1 ms or more; if the
ejector timing waited for every write, LEDs / valves came late when many objects fell
(20 Nov 2026: 5 LATE + 2 MISSED in a busy test). Order is kept: ON 17, OFF 17 arrive in
that order. Bytes waiting together are sent in one USB write.

No PyQt5 here. The core layer decides WHEN to switch a valve.
Opening the port does NOT reset the ESP32 (DTR / RTS stay low).
"""

import threading
import time

from core import realtime

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE, SERIAL_ERROR = True, ""
except Exception as _exc:             # noqa: BLE001
    serial = None
    SERIAL_AVAILABLE, SERIAL_ERROR = False, f"pyserial not installed ({_exc}) - run: pip install pyserial"

NUM_CHANNELS = 63
BAUDS = (230400, 115200)          # tried in this order
ALIVE = b"ESP32_ALIVE"
CMD_ALL_OFF = b"S"
CMD_PING = b"P"
CMD_WATCHDOG_ON = b"W"

# USB-serial chips used by ESP32 boards. "Find" only knocks on these ports, so the
# feeder / other devices never receive a byte.
ESP32_USB_CHIPS = {0x10C4: "CP210x", 0x1A86: "CH340", 0x303A: "Espressif"}

serial_factory = None             # tests: callable(port, baud) -> fake serial object


class EjectorError(Exception):
    pass


def list_ports():
    """[(port, label)] e.g. [("COM24", "COM24 [CP210x] Silicon Labs CP210x USB to UART Bridge")]."""
    if not SERIAL_AVAILABLE:
        return []
    out = []
    for p in serial.tools.list_ports.comports():
        chip = ESP32_USB_CHIPS.get(getattr(p, "vid", None), "")
        label = p.device + (f" [{chip}]" if chip else "") + (f" {p.description}" if p.description else "")
        out.append((p.device, label, bool(chip)))
    out.sort(key=lambda x: (len(x[0]), x[0]))
    return [(dev, label) for dev, label, _ in out]


def esp32_like_ports():
    """Only ports with an ESP32 USB chip (CP210x / CH340 / Espressif)."""
    if not SERIAL_AVAILABLE:
        return []
    return [p.device for p in serial.tools.list_ports.comports()
            if getattr(p, "vid", None) in ESP32_USB_CHIPS]


def _open(port, baud):
    if serial_factory:
        return serial_factory(port, baud)
    if not SERIAL_AVAILABLE:
        raise EjectorError(SERIAL_ERROR)
    s = serial.Serial()
    s.port, s.baudrate, s.timeout, s.write_timeout = port, baud, 0.02, 0.5
    s.dtr = False                 # do not reset the ESP32 when the port opens
    s.rts = False
    s.open()
    return s


def ping(port, baud, wait_s=1.0):
    """Open `port`, send 'P' (a few times), wait for "ESP32_ALIVE". Returns (ok, serial_or_None, text)."""
    try:
        s = _open(port, baud)
    except Exception as exc:      # noqa: BLE001
        text = str(exc)
        if "denied" in text.lower() or "PermissionError" in text:
            text = "port is used by another program (close lambton-main / Arduino serial monitor)"
        return False, None, text
    buf, t_end, t_ping = b"", time.time() + wait_s, 0.0
    try:
        s.reset_input_buffer()
        while time.time() < t_end:
            if time.time() - t_ping > 0.3:
                s.write(CMD_PING)
                t_ping = time.time()
            chunk = s.read(256)
            if chunk:
                buf += chunk
                if ALIVE in buf:
                    return True, s, "ESP32 answered"
            else:
                time.sleep(0.005)
        text = "no answer" + (f" (it sent: {buf[:60]!r})" if buf else "")
    except Exception as exc:      # noqa: BLE001
        if "timeout" in str(exc).lower():
            text = ("write timeout: the port does not take data (board stuck or not powered, "
                    "or not the ESP32) - unplug + replug the USB cable")
        else:
            text = f"error: {exc}"
    try:
        s.close()
    except Exception:             # noqa: BLE001
        pass
    return False, None, text


def find_ejector_ports(ports=None, progress=None, report=None):
    """
    Knock on every ESP32-like port. Returns [(port, baud)] of ports that answered ESP32_ALIVE.
    report (a list): gets (port, baud, text) for every try, e.g. ("COM5", 230400, "no answer").
    """
    found = []
    for port in (ports if ports is not None else esp32_like_ports()):
        for baud in BAUDS:
            if progress:
                progress(f"{port} at {baud} baud ...")
            ok, s, text = ping(port, baud, wait_s=0.8)
            if report is not None:
                report.append((port, baud, text))
            if ok:
                s.close()
                found.append((port, baud))
                break
            if "another program" in text:
                break
    return found


class Esp32Ejector:
    """One ejector board. Every method is safe to call from any thread."""

    def __init__(self, threaded=True):
        """threaded=False: write at once in the caller (only for tests)."""
        self.port = None
        self.baud = None
        self.error = ""
        self._serial = None
        self._lock = threading.Lock()
        self.bytes_sent = 0
        self.threaded = threaded
        self._queue = bytearray()
        self._queue_ready = threading.Condition()
        self._sender = None
        self._writing = False            # sender is in the middle of a USB write
        self.max_write_ms = 0.0          # slowest USB write since the last read_write_stats()

    @property
    def connected(self):
        return self._serial is not None

    # ------------------------------------------------------------ connect
    def connect(self, port, baud=None, watchdog=True):
        """Open `port` (baud None = try 230400, then 115200), check the board answers, all valves OFF."""
        self.close()
        last = "no answer"
        for b in ((baud,) if baud else BAUDS):
            ok, s, text = ping(port, b)
            if ok:
                with self._lock:
                    self._serial, self.port, self.baud, self.error = s, port, b, ""
                if self.threaded:
                    self._sender = threading.Thread(target=self._send_loop, name="esp32-sender", daemon=True)
                    self._sender.start()
                self._send(CMD_ALL_OFF)
                if watchdog:
                    self._send(CMD_WATCHDOG_ON)
                return True
            last = text
            if "another program" in text:
                break
        self.error = f"{port}: {last}"
        return False

    def close(self):
        """All valves OFF, then close the port."""
        with self._queue_ready:
            self._queue.clear()
        with self._lock:
            s, self._serial = self._serial, None
        with self._queue_ready:
            self._queue_ready.notify_all()        # sender thread: port gone -> stop
        if self._sender is not None:
            self._sender.join(timeout=1)
            self._sender = None
        if s is not None:
            try:
                s.write(CMD_ALL_OFF)
                s.flush()
            except Exception:     # noqa: BLE001
                pass
            try:
                s.close()
            except Exception:     # noqa: BLE001
                pass

    # ------------------------------------------------------------ valves
    def valve_on(self, channel):
        if 1 <= channel <= NUM_CHANNELS:
            self._send(bytes([channel]))

    def valve_off(self, channel):
        if 1 <= channel <= NUM_CHANNELS:
            self._send(bytes([100 + channel]))

    def all_off(self):
        with self._queue_ready:
            self._queue.clear()          # forget waiting ON bytes: OFF is more important
        self._send(CMD_ALL_OFF)

    def watchdog_on(self):
        self._send(CMD_WATCHDOG_ON)

    def _send(self, data):
        if self._serial is None:
            return False
        if not self.threaded:
            return self._write_now(data)
        with self._queue_ready:          # just queue it - returns at once
            self._queue += data
            self._queue_ready.notify()
        return True

    def _send_loop(self):
        """Sender thread: write whatever is queued (several bytes in one USB write)."""
        realtime.boost_this_thread("ESP32 sender")
        while self._serial is not None:
            with self._queue_ready:
                while not self._queue and self._serial is not None:
                    self._queue_ready.wait(0.5)
                data = bytes(self._queue)
                self._queue.clear()
                self._writing = bool(data)
            if data:
                self._write_now(data)
            self._writing = False

    def _write_now(self, data):
        with self._lock:
            s = self._serial
            if s is None:
                return False
            try:
                t = time.perf_counter()
                s.write(data)
                self.max_write_ms = max(self.max_write_ms, (time.perf_counter() - t) * 1000)
                self.bytes_sent += len(data)
                return True
            except Exception as exc:      # noqa: BLE001  (USB cable pulled, board reset ...)
                self.error = f"lost connection: {exc}"
                self._serial = None
                try:
                    s.close()
                except Exception:         # noqa: BLE001
                    pass
                return False

    def read_write_stats(self):
        """Slowest USB write (ms) since the last call, and bytes still waiting."""
        slowest, self.max_write_ms = self.max_write_ms, 0.0
        return slowest, len(self._queue)

    def wait_sent(self, timeout=1.0):
        """Wait until the queue is empty (tests)."""
        t_end = time.time() + timeout
        while (self._queue or self._writing) and time.time() < t_end:
            time.sleep(0.001)

    def status_text(self):
        if self.connected:
            return f"connected {self.port} ({self.baud} baud)"
        return "not connected" + (f" - {self.error}" if self.error else "")
