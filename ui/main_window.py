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

from config import SNAPSHOT_DIR, save_camera_settings, save_esp32_port
from core import realtime
from ui.calibration_window import CalibrationWindow
from ui.camera_panel import CameraPanel
from ui.esp32_window import Esp32Window
from ui.frame_check_window import FrameCheckWindow
from ui.led_bar import LedBar
from ui.log_window import LogWindow


def _memory_mb():
    """Memory used by this program (Windows), in MB - or None."""
    try:
        import ctypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        c = Counters()
        c.cb = ctypes.sizeof(Counters)
        kernel32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
        if psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return c.WorkingSetSize / 1e6
    except Exception:                    # noqa: BLE001  (not Windows, ...)
        pass
    return None


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
        self.esp32_window = None         # created the first time "ESP32 Ejector" is clicked
        self.frame_check_window = None   # created the first time "Frame check" is clicked
        self.esp32_port = config["esp32"]["port"]
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
            panel.line_rate_requested.connect(
                lambda rate, w=worker: w.request_setting("line_rate", rate))
            panel.thresholds_changed.connect(
                lambda lower, upper, w=worker: w.set_thresholds(lower, upper))

            self.panels[name] = panel

        # --- 63 ejector LEDs: FRONT + REAR combined ------------------------------
        self.led_bar = LedBar(config["channels"])
        self.led_min_visible_ms = config["detection"]["led_hold_ms"]
        self.led_title_text = ("<b>EJECTOR LEDs</b> &nbsp; "
                               "<span style='color:#ff2828'>■</span> both cameras &nbsp; "
                               "<span style='color:#ff9600'>■</span> FRONT only &nbsp; "
                               "<span style='color:#00c8ff'>■</span> REAR only &nbsp; ")
        led_title = self.led_title = QLabel()
        self._armed_shown = None         # redraw the title only when the arm state changes

        # Delay + blow time (sent to core.EjectScheduler).
        delay_ms, blow_ms = ejector.get_timing()
        self.delay_box = QDoubleSpinBox()
        self.delay_box.setRange(0.0, 2000.0)
        self.delay_box.setDecimals(1)
        self.delay_box.setSingleStep(0.5)
        self.delay_box.setSuffix(" ms")
        self.delay_box.setValue(delay_ms)
        self.delay_box.setToolTip("Time from 'object touched the decision line' (exact camera time) "
                                  "to 'ejector LED ON'. Must be longer than the PC reaction (see Logs).")
        self.blow_box = QDoubleSpinBox()
        self.blow_box.setRange(0.5, 2000.0)
        self.blow_box.setDecimals(1)
        self.blow_box.setSingleStep(0.5)
        self.blow_box.setSuffix(" ms")
        self.blow_box.setValue(blow_ms)
        self.blow_box.setToolTip("LED stays ON until the object's last line + delay + this time "
                                 "(long objects get a longer blow)")
        for box in (self.delay_box, self.blow_box):
            box.setKeyboardTracking(False)       # apply on Enter / arrows, not every key
            box.setMinimumWidth(95)
        # LIVE: the next object already uses the new value.
        # AUTOSAVE: 0.5 s after the last click it is written to camera_config.json
        # (several quick clicks = one save), so it is kept for the next start.
        self.delay_box.valueChanged.connect(lambda v: self.ejector.set_timing(delay_ms=v))
        self.blow_box.valueChanged.connect(lambda v: self.ejector.set_timing(blow_ms=v))
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(500)
        self.autosave_timer.timeout.connect(self.autosave_timing)
        for box in (self.delay_box, self.blow_box):
            box.valueChanged.connect(lambda _v: self.autosave_timer.start())
        self.autosave_label = QLabel("")
        self.autosave_label.setStyleSheet("color:gray")

        led_row = QHBoxLayout()
        led_row.addWidget(led_title)
        led_row.addStretch()
        led_row.addWidget(QLabel("Delay:"))
        led_row.addWidget(self.delay_box)
        led_row.addSpacing(12)
        led_row.addWidget(QLabel("Blow time:"))
        led_row.addWidget(self.blow_box)
        led_row.addWidget(self.autosave_label)

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
        esp32_button = QPushButton("ESP32 Ejector")
        esp32_button.setToolTip("Connect the valve board, test valves, switch real firing on / off")
        esp32_button.clicked.connect(self.on_esp32)
        buttons.addWidget(esp32_button)
        frame_check_button = QPushButton("Frame check")
        frame_check_button.setToolTip("Count the next 1000 frames: ejector status for every frame? "
                                      "missed frames / lines? time per frame?")
        frame_check_button.clicked.connect(self.on_frame_check)
        buttons.addWidget(frame_check_button)
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
        self.last_lost = {}              # lost-frame counter at the last update (per camera)

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
        if self.ejector.armed != self._armed_shown:
            self._armed_shown = self.ejector.armed
            self.led_title.setText(self.led_title_text + (
                "<b style='color:#c0392b'>VALVES FIRE (ESP32 armed)</b>" if self.ejector.armed
                else "<span style='color:gray'>(display only - no valves are fired)</span>"))


        now = time.monotonic()
        if now - self.last_stats_time >= 1.0:
            self.update_stats(now - self.last_stats_time)
            self.last_stats_time = now

    def update_stats(self, seconds):
        # How much CPU this program uses (100 % = one full CPU core), and which part uses it.
        cpu_now, ui_now = time.process_time(), time.thread_time()     # whole program / this (screen) thread
        total = (cpu_now - getattr(self, "_last_cpu", cpu_now)) / seconds * 100
        screen = (ui_now - getattr(self, "_last_ui_cpu", ui_now)) / seconds * 100
        self._last_cpu, self._last_ui_cpu = cpu_now, ui_now
        parts = {f"{name} camera": w.cpu_percent for name, w in self.workers.items()}
        parts["ejector"] = getattr(self.ejector, "cpu_percent", 0.0)
        parts["screen"] = screen
        rest = max(0.0, total - sum(parts.values()))
        memory = _memory_mb()
        buffers = ", ".join(f"{n} {getattr(w.camera, 'sdk_buffers_in_use', None) or '?'}"
                            for n, w in self.workers.items())
        self.statusBar().showMessage(
            (f"Memory: {memory:,.0f} MB   |   " if memory else "") + f"SDK buffers: {buffers}   |   "
            f"Program CPU: {total:.0f} % of one core  =  "
            + "  +  ".join(f"{k} {v:.0f} %" for k, v in parts.items())
            + f"  +  camera SDK / other {rest:.0f} %      (CPU cores on this PC: {os.cpu_count()})"
            + f"   |   Priority: {realtime.status_text()}"
            + f"   |   ESP32: {self._esp32_text()}")
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
            clock = getattr(worker, "clock", None)
            if clock is not None and worker.frame_count:
                # Two counters:
                #   SDK  = frames the camera SDK itself counts as lost
                #   gaps = frames missing in the camera clock (every lost frame, any reason)
                sdk, gaps = worker.sdk_lost, clock.missed_frames
                old_sdk, old_gaps = self.last_lost.get(name, (sdk, gaps))
                self.last_lost[name] = (sdk, gaps)
                new_sdk, new_gaps = sdk - old_sdk, gaps - old_gaps
                text += (f"   |   lost frames last second: SDK {new_sdk}, clock gaps {new_gaps}"
                         f"   (total: SDK {sdk:,}, gaps {gaps:,})")
                if new_sdk > 0 or new_gaps > 0:
                    text += "  <- FRAMES LOST!"
                text += ("   |   line times: CAMERA CLOCK" if clock.using_camera_clock
                         else "   |   line times: PC clock (no camera timestamp)")
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
            if current.get("line_rate") is not None and getattr(worker.camera, "backup_file", None):
                entry["line_rate"] = int(current["line_rate"])     # only once you changed it here
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

    def autosave_timing(self):
        """Delay / blow time changed: write them into camera_config.json (no question asked)."""
        ejector = {"delay_ms": round(self.delay_box.value(), 1), "blow_ms": round(self.blow_box.value(), 1)}
        try:
            save_camera_settings({}, ejector=ejector)
        except Exception as e:           # noqa: BLE001
            self.autosave_label.setText(f"<span style='color:#c0392b'>NOT saved: {e}</span>")
            return
        self.autosave_label.setText(f"saved {time.strftime('%H:%M:%S')}")

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

    def on_frame_check(self):
        if self.frame_check_window is None:
            self.frame_check_window = FrameCheckWindow(self.workers)
        self.frame_check_window.show()
        self.frame_check_window.raise_()
        self.frame_check_window.activateWindow()

    def on_esp32(self):
        """Open the ESP32 Ejector window (connect, test valves, arm)."""
        if self.esp32_window is None:
            self.esp32_window = Esp32Window(self.ejector.valves, self.ejector, saved_port=self.esp32_port,
                                            on_port_saved=self.remember_esp32_port)
        self.esp32_window.show()
        self.esp32_window.raise_()
        self.esp32_window.activateWindow()

    def remember_esp32_port(self, port, baud):
        self.esp32_port = port
        try:
            save_esp32_port(port, baud)
        except Exception as e:           # noqa: BLE001
            print(f"[ESP32] could not save the port: {e}")

    def _esp32_text(self):
        board = self.ejector.valves
        if board is None or not board.connected:
            return "not connected"
        state = "VALVES FIRING" if self.ejector.armed else "connected, not armed"
        slowest, waiting = board.read_write_stats()
        return (f"{board.port} {state} ({self.ejector.valve_count} valve openings, "
                f"slowest USB write {slowest:.1f} ms" + (f", {waiting} bytes waiting" if waiting else "") + ")")

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
        if self.autosave_timer.isActive():   # a change in the last 0.5 s: save it now
            self.autosave_timer.stop()
            self.autosave_timing()
        self.ejector.arm(False)          # all valves OFF
        for window in (self.log_window, self.calibration_window, self.esp32_window, self.frame_check_window):
            if window is not None:
                window.close()
        for worker in self.workers.values():
            worker.stop()
        event.accept()
