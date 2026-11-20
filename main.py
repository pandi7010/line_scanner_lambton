"""
main.py - START HERE.  Shows the FRONT and REAR line-scan cameras live.

    python main.py

main.py only CONNECTS the layers; it contains no camera or window logic:

    config/    reads camera_config.json
    hardware/  MindVisionCamera  - talks to the camera (SDK)
    core/      CameraWorker      - reads the camera in the background (no PyQt5)
               ChannelMap        - which pixels belong to which valve (equal or calibrated)
               ChannelDetector   - which of the 63 channels has something in it
               CameraClock       - exact time of every camera line (camera timestamps)
               EjectScheduler    - ejector LEDs: ON at touch time + delay; ON TIME / LATE / MISSED
               EventLog          - one row per ejection (Logs window)
    hardware/  Esp32Ejector      - the 63 real valves (ESP32 over USB), off until you connect + arm
    ui/        MainWindow        - shows everything (PyQt5)

IMPORTANT: a camera can be opened by ONE program at a time.
           Close lambton-main (the sorter) and MVDCP before starting this viewer.
"""

import os
import sys

# Make "import config", "import hardware", ... work from any start folder.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import QApplication  # noqa: E402

from config import load_config  # noqa: E402
from core import (CameraClock, CameraWorker, ChannelDetector, ChannelMap,  # noqa: E402
                  EjectScheduler, EventLog, realtime)
from hardware import SDK_AVAILABLE, SDK_ERROR, Esp32Ejector, MindVisionCamera  # noqa: E402
from ui import MainWindow  # noqa: E402
from ui.theme import apply_dark_theme  # noqa: E402


def main():
    # Let Python switch between threads every 0.5 ms (default 5 ms). Otherwise one busy
    # thread (e.g. the ejector timing) can keep a camera thread waiting -> lost frames.
    # (lambton-main does the same: gil_switch_interval_ms 0.5)
    sys.setswitchinterval(0.0005)

    # The Qt application must exist before any window is made.
    app = QApplication(sys.argv)

    # 1) Config layer
    config = load_config()
    if config["display"].get("dark_theme", True):
        apply_dark_theme(app)            # easier on the eyes ("dark_theme": false = light look)

    # Windows: camera + ejector threads run FIRST when the PC is busy (switch: "realtime").
    realtime.configure(high_priority=config["realtime"]["high_priority"],
                       timer_1ms=config["realtime"]["timer_1ms"])

    if not SDK_AVAILABLE:
        print(f"[ERROR] MindVision SDK not available: {SDK_ERROR}")
        print("        The window will open, but no camera can connect.")

    # 2) Hardware layer + 3) Core layer: one camera + one worker per camera.
    workers = {}
    for name, cam_cfg in config["cameras"].items():
        camera = MindVisionCamera(
            str(cam_cfg["serial"]).strip(),
            frame_lines=int(cam_cfg["frame_lines"]),
            sdk_buffers=int(cam_cfg["sdk_buffers"]),
            settings=cam_cfg,               # exposure + gains, applied like lambton-main
        )
        channel_map = ChannelMap.from_config(cam_cfg, config["channels"])   # calibrated if measured
        print(f"[{name}] channels: {channel_map.describe()}")
        detector = ChannelDetector(
            channel_map,
            lower=cam_cfg["lower"], upper=cam_cfg["upper"], band_mode=cam_cfg["band_mode"],
            colour=config["detection"]["colour"],
            min_pixels=config["detection"]["min_pixels"],
        )
        clock = CameraClock(default_line_rate_hz=config["clock"]["default_line_rate_hz"],
                            offset_window_frames=config["clock"]["offset_window_frames"],
                            use_camera_timestamp=config["clock"]["use_camera_timestamp"])
        workers[name] = CameraWorker(name, camera, mirror=bool(cam_cfg["mirror"]), detector=detector,
                                     clock=clock, end_gap=config["ejector"]["end_gap_lines"],
                                     snap_seconds=config["snap"]["history_s"] if config["snap"]["enabled"] else 0)

    # Ejector: either camera triggers; LED ON after the delay, for the blow time.
    ej = config["ejector"]
    event_log = EventLog(object_join_ms=ej["object_join_ms"],     # rows of one object share a number
                         keep_snaps=config["snap"]["keep"])
    ejector = EjectScheduler({name: w.activity for name, w in workers.items()}, event_log,
                             delay_ms=ej["delay_ms"], blow_ms=ej["blow_ms"],
                             same_object_ms=ej["same_object_ms"],
                             late_tolerance_ms=ej["late_tolerance_ms"],
                             histories={name: w.history for name, w in workers.items()})   # object snaps

    # Real valves: the ejector ESP32. NOT connected at start, valves fire only after
    # "Connect" + "Fire valves from the cameras" in the ESP32 Ejector window.
    valve_board = Esp32Ejector()
    ejector.set_valves(valve_board)

    # 4) UI layer
    window = MainWindow(workers, config, ejector, event_log)
    window.show()

    for worker in workers.values():
        worker.start()
    ejector.start()

    exit_code = app.exec_()             # runs until the window is closed
    ejector.stop()                      # all valves OFF
    valve_board.close()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
