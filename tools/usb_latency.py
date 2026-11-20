"""
tools/usb_latency.py - How long does a byte take from the PC to the ejector ESP32? (NO valve fires)

    .venv\\Scripts\\python.exe tools\\usb_latency.py            (port from camera_config.json, 200 pings)
    .venv\\Scripts\\python.exe tools\\usb_latency.py COM6 500   (port and number of pings)

Close the viewer first (a COM port can be used by one program only).

How it works:
    PC sends 'P'  ---- USB ---->  ESP32 answers "ACK: ESP32_ALIVE"  ---- USB ---->  PC
    |<----------------------------- round trip ---------------------------------->|
One way (PC -> ESP32) is about half of the round trip. The answer text itself (18
characters) needs ~0.8 ms on the wire at 230400 baud, so:  one way ~ (round trip - 0.8 ms) / 2.
'P' only blinks the ESP32's own blue LED - no valve is switched.
The firmware waits 30 ms after every answer (LED blink), so the pings are 50 ms apart.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from config import CONFIG_FILE  # noqa: E402
from hardware import esp32_ejector  # noqa: E402

if not esp32_ejector.SERIAL_AVAILABLE:
    print(esp32_ejector.SERIAL_ERROR)
    sys.exit(1)


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else ""
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    baud = 0
    if not port:
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                saved = json.load(f).get("esp32", {})
            port, baud = saved.get("port", ""), int(saved.get("baud", 0) or 0)
        except Exception:                 # noqa: BLE001
            pass
    if not port:
        print("No port saved yet. Run:  tools\\usb_latency.py COM6")
        return 1

    ok, s, text = (False, None, "")
    for b in ((baud,) if baud else esp32_ejector.BAUDS):
        ok, s, text = esp32_ejector.ping(port, b)
        if ok:
            baud = b
            break
    if not ok:
        print(f"{port}: {text}")
        return 1
    print(f"ESP32 on {port} at {baud} baud. {count} pings, 50 ms apart (~{count * 0.05:.0f} s) ...")
    time.sleep(0.1)
    s.timeout = 0.0005
    s.reset_input_buffer()

    trips, lost = [], 0
    for i in range(count):
        buf = b""
        t0 = time.perf_counter()
        s.write(esp32_ejector.CMD_PING)
        while time.perf_counter() - t0 < 0.2:
            buf += s.read(64)
            if esp32_ejector.ALIVE in buf and buf.endswith(b"\n"):
                trips.append((time.perf_counter() - t0) * 1000)
                break
        else:
            lost += 1
        time.sleep(max(0.0, 0.05 - (time.perf_counter() - t0)))
        if (i + 1) % 50 == 0:
            print(f"  {i + 1} / {count}")
    s.close()

    if not trips:
        print("No answers - is it the ejector ESP32 with esp.ino?")
        return 1
    t = np.array(trips)
    wire = 18 * 10 / baud * 1000            # the answer text on the wire
    print()
    print(f"Round trip PC -> ESP32 -> PC  ({len(t)} answers, {lost} lost):")
    print(f"  fastest {t.min():.2f} ms | typical (median) {np.median(t):.2f} ms | "
          f"95 % below {np.percentile(t, 95):.2f} ms | 99 % below {np.percentile(t, 99):.2f} ms | "
          f"slowest {t.max():.2f} ms")
    one_way = (np.median(t) - wire) / 2
    worst = (np.percentile(t, 99) - wire) / 2
    print()
    print(f"=> PC -> ESP32 one way: about {max(0, one_way):.1f} ms typical, about {max(0, worst):.1f} ms "
          f"in the worst 1 %")
    print("   (this is part of every valve delay: the 100-piece delay test already includes it)")
    print()
    print("Send me this text.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
