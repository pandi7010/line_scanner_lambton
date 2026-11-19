"""
ui/main_window.py - The main window: FRONT camera on top, REAR camera below.

How the screen is updated:
    A QTimer fires `refresh_fps` times per second (default 25). Each time,
    for every camera worker (core layer) we check:
        - new status?    -> show it
        - new settings?  -> show them in the controls
        - new frame?     -> draw it
    Once per second the frames/s and lines/s numbers are updated.
"""

import os
import time

from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QImage
from PyQt5.QtWidgets import (QDoubleSpinBox, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                             QPushButton, QVBoxLayout, QWidget)

from config import SNAPSHOT_DIR, save_camera_settings
from ui.calibration_window import CalibrationWindow
from ui.camera_panel import CameraPanel
from ui.led_bar import LedBar
from ui.log_window import LogWindow


class MainWindow(QMainWindow):

    def __init__(self, workers, config, ejector, event_log):
        """
        workers   : {"FRONT": core.CameraWorker, "REAR": core.CameraWorker}
        config    : the loaded camera_config.json (config.load_config())
        ejector   : core.EjectScheduler - decides when each ejector LED is ON
        event_log : core.EventLog - one row per ejection (for the Logs window)
        """
        super().__init__()
        self.workers = workers
        self.ejector = ejector
        self.event_log = event_log
        self.channels = config["channels"]
        self.camera_configs = config["cameras"]
        self.log_window = None           # created the first time "Logs" is clicked
        self.calibration_window = None   # created the first time "Calibrate" is clicked
        display = config["display"]

        self.setWindowTitle("Lambton - Camera Viewer")
        self.resize(1400, 850)

        # --- one panel per camera -------------------------------------------
        self.panels = {}
        for name, worker in self.workers.items():
            cam_cfg = config["cameras"][name]
            panel = CameraPanel(f"{name} CAMERA", cam_cfg["serial"],
                                v_stretch=cam_cfg["v_stretch"],
                                show_grid=display["show_grid"],
                                lower=cam_cfg["lower"], upper=cam_cfg["upper"])

            # Grid: the 64 channel boundaries of this camera's ChannelMap (core layer).
            panel.view.set_grid(list(worker.detector.channel_map.boundaries), display["show_grid"])
            panel.grid_check.toggled.connect(
                lambda show, v=panel.view, w=worker: v.set_grid(list(w.detector.channel_map.boundaries), show))

            # Panel -> core: the user changed a camera setting.
            panel.frame_lines_requested.connect(
                lambda lines, w=worker: w.request_setting("frame_lines", lines))
            panel.exposure_requested.connect(
                lambda exposure, w=worker: w.request_setting("exposure_us", exposure))
            panel.thresholds_changed.connect(
                lambda lower, upper, w=worker: w.set_thresholds(lower, upper))

            self.panels[name] = panel

        # --- 63 ejector LEDs: FRONT + REAR combined ------------------------------
        self.led_bar = LedBar(config["channels"])
        self.led_min_visible_ms = config["detection"]["led_hold_ms"]
        led_title = QLabel("<b>EJECTOR LEDs</b> &nbsp; "
                           "<span style='color:#ff2828'>■</span> both cameras &nbsp; "
                           "<span style='color:#ff9600'>■</span> FRONT only &nbsp; "
                           "<span style='color:#00c8ff'>■</span> REAR only &nbsp; "
                           "<span style='color:gray'>(display only - no valves are fired)</span>")

        # Delay + blow time (sent to core.EjectScheduler).
        delay_ms, blow_ms = ejector.get_timing()
        self.delay_box = QDoubleSpinBox()
        self.delay_box.setRange(0.0, 2000.0)
        self.delay_box.setDecimals(1)
        self.delay_box.setSingleStep(0.5)
        self.delay_box.setSuffix(" ms")
        self.delay_box.setValue(delay_ms)
        self.delay_box.setToolTip("Time from 'camera saw the object' to 'ejector LED ON'")
        self.blow_box = QDoubleSpinBox()
        self.blow_box.setRange(0.5, 2000.0)
        self.blow_box.setDecimals(1)
        self.blow_box.setSingleStep(0.5)
        self.blow_box.setSuffix(" ms")
        self.blow_box.setValue(blow_ms)
        self.blow_box.setToolTip("How long the ejector LED stays ON")
        for box in (self.delay_box, self.blow_box):
            box.setKeyboardTracking(False)       # apply on Enter / arrows, not every key
            box.setMinimumWidth(95)
        self.delay_box.valueChanged.connect(lambda v: self.ejector.set_timing(delay_ms=v))
        self.blow_box.valueChanged.connect(lambda v: self.ejector.set_timing(blow_ms=v))

        led_row = QHBoxLayout()
        led_row.addWidget(led_title)
        led_row.addStretch()
        led_row.addWidget(QLabel("Delay:"))
        led_row.addWidget(self.delay_box)
        led_row.addSpacing(12)
        led_row.addWidget(QLabel("Blow time:"))
        led_row.addWidget(self.blow_box)

        # What we already showed (to only redraw when something is new).
        self.seen = {name: {"status": -1, "settings": -1, "frame": -1} for name in workers}

        # --- buttons ---------------------------------------------------------
        self.pause_button = QPushButton("Pause")
        self.pause_button.setCheckable(True)
        self.pause_button.toggled.connect(self.on_pause)

        snapshot_button = QPushButton("Save snapshot")
        snapshot_button.setToolTip("Save the current frame of each camera (full size) as PNG")
        snapshot_button.clicked.connect(self.on_snapshot)

        save_button = QPushButton("Save settings")
        save_button.setToolTip("Write frame height, exposure, V-stretch, lower/upper, delay and blow time "
                               "into camera_config.json (used at the next start)")
        save_button.clicked.connect(self.on_save_settings)

        logs_button = QPushButton("Logs")
        logs_button.setToolTip("Open the ejection log: seen at, LED ON at, delay set / measured")
        logs_button.clicked.connect(self.on_logs)

        calibrate_button = QPushButton("Calibrate channels")
        calibrate_button.setToolTip("Measure where the valves are in each camera, so FRONT and REAR "
                                    "agree on the channel")
        calibrate_button.clicked.connect(self.on_calibrate)

        buttons = QHBoxLayout()
        buttons.addWidget(self.pause_button)
        buttons.addWidget(snapshot_button)
        buttons.addWidget(save_button)
        buttons.addWidget(logs_button)
        buttons.addWidget(calibrate_button)
        buttons.addStretch()

        # --- put it all together ---------------------------------------------
        layout = QVBoxLayout()
        for panel in self.panels.values():
            layout.addWidget(panel, stretch=1)
        layout.addLayout(led_row)
        layout.addWidget(self.led_bar)
        layout.setSpacing(4)
        layout.addLayout(buttons)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        # --- screen refresh timer ----------------------------------------------
        fps = max(1, int(display["refresh_fps"]))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_screen)
        self.timer.start(int(1000 / fps))

        self.last_stats_time = time.monotonic()
        self.last_counts = {name: (0, 0) for name in workers}

    # ---------------------------------------------------------------- refresh
    def refresh_screen(self):
        for name, worker in self.workers.items():
            panel, seen = self.panels[name], self.seen[name]

            text, ok, version = worker.get_status()
            if version != seen["status"]:
                panel.set_status(text, ok)
                seen["status"] = version

            settings, version = worker.get_settings()
            if version != seen["settings"]:
                if settings:
                    panel.show_settings(settings)
                seen["settings"] = version

            frame, number = worker.frames.get()
            if frame is not None and number != seen["frame"]:
                panel.view.set_frame(frame)
                seen["frame"] = number

        # Ejector LEDs (ON after the delay; shown at least led_hold_ms so you can see them).
        self.led_bar.set_lit(self.ejector.lit_channels(self.led_min_visible_ms))


        now = time.monotonic()
        if now - self.last_stats_time >= 1.0:
            self.update_stats(now - self.last_stats_time)
            self.last_stats_time = now

    def update_stats(self, seconds):
        for name, worker in self.workers.items():
            old_frames, old_lines = self.last_counts[name]
            fps = (worker.frame_count - old_frames) / seconds
            lines_per_s = (worker.line_count - old_lines) / seconds
            self.last_counts[name] = (worker.frame_count, worker.line_count)

            width, lines = worker.frame_size
            view = self.panels[name].view
            text = (f"{width} x {lines} px per frame   |   {fps:,.1f} frames/s   |   "
                    f"{lines_per_s:,.0f} lines/s   |   brightness {worker.brightness:.0f} / 255")
            if worker.detector is not None and worker.frame_count:
                text += f"   |   background level {worker.background_level} (lower/upper must be around it)"
            if worker.detector is not None:
                calibrated = worker.detector.channel_map.calibrated
                text += "   |   channels: " + ("CALIBRATED" if calibrated else "equal (not calibrated)")
            if worker.frame_count and worker.brightness < 10:
                text += "   <- very dark"
            if not view.fits:
                text += "   |   picture shrunk to fit (lower the V-stretch)"
            self.panels[name].set_info(text)

    # ---------------------------------------------------------------- buttons
    def on_pause(self, paused):
        for worker in self.workers.values():
            worker.paused = paused
        self.pause_button.setText("Resume" if paused else "Pause")

    def on_snapshot(self):
        """Save the newest frame of each camera (full size, no grid) as PNG."""
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        saved = []
        for name, worker in self.workers.items():
            frame, _ = worker.frames.get()
            if frame is None:
                continue
            path = os.path.join(SNAPSHOT_DIR, f"{stamp}_{name}.png")
            lines, width = frame.shape[:2]
            QImage(frame.data, width, lines, frame.strides[0], QImage.Format_BGR888).save(path)
            saved.append(path)

        if saved:
            QMessageBox.information(self, "Snapshot", "Saved:\n" + "\n".join(saved))
        else:
            QMessageBox.warning(self, "Snapshot", "No picture yet - nothing saved.")

    def on_save_settings(self):
        """Store frame height, exposure and V-stretch in camera_config.json."""
        values = {}
        for name, worker in self.workers.items():
            panel = self.panels[name]
            entry = {"v_stretch": panel.stretch_box.value(),
                     "lower": panel.lower_box.value(),
                     "upper": panel.upper_box.value()}
            current, _ = worker.get_settings()
            if "frame_lines" in current:
                entry["frame_lines"] = current["frame_lines"]
            if "exposure_us" in current:
                entry["exposure_us"] = round(current["exposure_us"], 1)
            values[name] = entry
        ejector = {"delay_ms": round(self.delay_box.value(), 1),
                   "blow_ms": round(self.blow_box.value(), 1)}
        try:
            save_camera_settings(values, ejector=ejector)
        except Exception as e:           # noqa: BLE001
            QMessageBox.critical(self, "Save settings", f"Could not save:\n{e}")
            return
        lines = [f"{name}: " + ", ".join(f"{k} = {v}" for k, v in entry.items())
                 for name, entry in values.items()]
        lines.append("EJECTOR: " + ", ".join(f"{k} = {v}" for k, v in ejector.items()))
        QMessageBox.information(self, "Save settings",
                                "Saved to camera_config.json:\n\n" + "\n".join(lines))

    def on_calibrate(self):
        """Open the channel calibration window."""
        if self.calibration_window is None:
            self.calibration_window = CalibrationWindow(
                self.workers, self.camera_configs, self.channels,
                apply_map=self.set_channel_map, save_points=self.save_channel_points)
        self.calibration_window.show()
        self.calibration_window.raise_()
        self.calibration_window.activateWindow()

    def set_channel_map(self, name, channel_map):
        """Use a new ChannelMap for one camera: detection + grid change at once."""
        self.workers[name].detector.channel_map = channel_map
        panel = self.panels[name]
        panel.view.set_grid(list(channel_map.boundaries), panel.grid_check.isChecked())

    def save_channel_points(self, points_per_camera):
        """{name: {channel: pixel}} -> camera_config.json (empty dict = not calibrated)."""
        save_camera_settings({name: {"channel_points": pts} for name, pts in points_per_camera.items()})
        for name, pts in points_per_camera.items():
            self.camera_configs[name]["channel_points"] = pts

    def on_logs(self):
        """Open the Logs window (or bring it to the front if it is already open)."""
        if self.log_window is None:
            self.log_window = LogWindow(self.event_log, self.channels)
        self.log_window.show()
        self.log_window.raise_()
        self.log_window.activateWindow()

    # ------------------------------------------------------------------ close
    def closeEvent(self, event):
        """Window closing: stop the timer and close the cameras cleanly."""
        self.timer.stop()
        for window in (self.log_window, self.calibration_window):
            if window is not None:
                window.close()
        for worker in self.workers.values():
            worker.stop()
        event.accept()
