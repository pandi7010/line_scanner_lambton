"""
tools/esp32_check.py - Which COM port is the ejector ESP32? (read-only check, switches NO valve)

    .venv\\Scripts\\python.exe tools\\esp32_check.py

1. Lists every COM port Windows knows (with USB chip and description).
2. For every ESP32-type port (CP210x / CH340 / Espressif):
     - just LISTENS 1 s (does the board print something by itself?)
     - sends 'P' ("are you there?") at 230400 and 115200 baud and shows the answer.
   The ejector board answers "ACK: ESP32_ALIVE". 'P' never switches a valve.
Close lambton-main and the Arduino serial monitor first.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hardware import esp32_ejector  # noqa: E402

if not esp32_ejector.SERIAL_AVAILABLE:
    print(esp32_ejector.SERIAL_ERROR)
    sys.exit(1)

import serial  # noqa: E402
import serial.tools.list_ports  # noqa: E402

ports = list(serial.tools.list_ports.comports())
print(f"COM ports on this PC: {len(ports)}")
for p in ports:
    chip = esp32_ejector.ESP32_USB_CHIPS.get(p.vid, "")
    vid = f"{p.vid:04X}:{p.pid:04X}" if p.vid is not None else "-"
    print(f"  {p.device:7s} usb {vid:10s} {chip:10s} {p.description}")

candidates = [p.device for p in ports if p.vid in esp32_ejector.ESP32_USB_CHIPS]
print()
if not candidates:
    print("No ESP32-type port (CP210x / CH340). Is the ejector USB cable plugged into THIS PC?")
    sys.exit(0)

for port in candidates:
    print(f"=== {port}")
    for baud in esp32_ejector.BAUDS:
        # listen only
        try:
            s = esp32_ejector._open(port, baud)
        except Exception as exc:          # noqa: BLE001
            print(f"  {baud}: cannot open: {exc}")
            break
        time.sleep(1.0)
        try:
            heard = s.read(400)
        except Exception as exc:          # noqa: BLE001
            heard = f"read error {exc}".encode()
        s.close()
        print(f"  {baud}: listening 1 s -> {heard[:120]!r}")
        ok, s, text = esp32_ejector.ping(port, baud, wait_s=1.5)
        print(f"  {baud}: 'P' -> {'ESP32 EJECTOR ANSWERED' if ok else text}")
        if ok:
            s.close()
            break
print()
print("Send me this whole text.")
