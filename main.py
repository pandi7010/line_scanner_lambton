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
                  EjectScheduler, EventLog)
from hardware import SDK_AVAILABLE, SDK_ERROR, MindVisionCamera  # noqa: E402
from ui import MainWindow  # noqa: E402


def main():
    # The Qt application must exist before any window is made.
    app = QApplication(sys.argv)

    # 1) Config layer
    config = load_config()

    if not SDK_AVAILABLE:
        print(f"[ERROR] MindVision SDK not available: {SDK_ERROR}")
        print("        The window will open, but no camera can connect.")

    # 2) Hardware layer + 3) Core layer: one camera + one worker per camera.
    workers = {}
    for name, cam_cfg in config["cameras"].items():
        camera = MindVisionCamera(
            str(cam_cfg["serial"]).strip(),
            frame_lines=int(cam_cfg["frame_lines"]),
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
                                     clock=clock, end_gap=config["ejector"]["end_gap_lines"])

    # Ejector: either camera triggers; LED ON after the delay, for the blow time.
    event_log = EventLog()
    ej = config["ejector"]
    ejector = EjectScheduler({name: w.activity for name, w in workers.items()}, event_log,
                             delay_ms=ej["delay_ms"], blow_ms=ej["blow_ms"],
                             same_object_ms=ej["same_object_ms"],
                             late_tolerance_ms=ej["late_tolerance_ms"])

    # 4) UI layer
    window = MainWindow(workers, config, ejector, event_log)
    window.show()

    for worker in workers.values():
        worker.start()
    ejector.start()

    exit_code = app.exec_()             # runs until the window is closed
    ejector.stop()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
