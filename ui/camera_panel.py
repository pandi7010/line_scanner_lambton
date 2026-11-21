"""
ui/camera_panel.py - The box for ONE camera.

    +----------------------------------------------------------------------+
    | FRONT CAMERA   SN 075002923007                       ● Streaming     |
    |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  | |  <- CameraView
    |  1  2  3  4  5  ...                                                63 |
    | Frame height [16 lines v]  Exposure [65.0 us]  V-stretch [3x]  [x] Grid |
    |                                  Detect: lower [68]  upper [221]     |
    | 4096 x 16 px per frame | 550 frames/s | ... | blue level 93          |
    +----------------------------------------------------------------------+

The panel never talks to the camera. When you change a value it sends a
signal; main_window.py passes it on to the core layer.
"""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout,
                             QLabel, QSpinBox, QVBoxLayout)

from ui.camera_view import CameraView

GREEN = "#2ecc71"
RED = "#e74c3c"

# Frame heights offered in the drop-down (lines per frame).
FRAME_HEIGHT_CHOICES = [8, 16, 32, 64, 128, 256, 512, 1024]


class CameraPanel(QFrame):

    # Signals to the window (camera settings).
    frame_lines_requested = pyqtSignal(int)
    exposure_requested = pyqtSignal(float)
    line_rate_requested = pyqtSignal(int)
    # Signal to the window (detection thresholds: lower, upper).
    thresholds_changed = pyqtSignal(int, int)

    def __init__(self, title, serial, v_stretch=3, show_grid=True, lower=68, upper=221):
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)

        # --- top row: title + status -------------------------------------
        title_label = QLabel(f"<b>{title}</b> &nbsp; <span style='color:gray'>SN {serial or '?'}</span>")
        title_label.setStyleSheet("font-size: 16px;")
        self.status_label = QLabel("Waiting...")
        self.status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        top_row = QHBoxLayout()
        top_row.addWidget(title_label)
        top_row.addStretch()
        top_row.addWidget(self.status_label)

        # --- the picture with grid -------------------------------------------
        self.view = CameraView()
        self.view.set_v_stretch(v_stretch)

        # --- settings row -----------------------------------------------------
        # Camera settings (sent to the camera):
        self.frame_combo = QComboBox()
        for lines in FRAME_HEIGHT_CHOICES:
            self.frame_combo.addItem(f"{lines} lines", lines)
        self.frame_combo.activated.connect(self._on_frame_height_chosen)   # only user clicks

        self.exposure_box = QDoubleSpinBox()
        self.exposure_box.setDecimals(1)
        self.exposure_box.setSuffix(" us")
        self.exposure_box.setRange(1, 100000)
        self.exposure_box.setSingleStep(5)
        self.exposure_box.setMinimumWidth(110)
        # Send only on Enter / arrow click / leaving the box, not on every key typed.
        self.exposure_box.setKeyboardTracking(False)
        self.exposure_box.valueChanged.connect(lambda v: self.exposure_requested.emit(float(v)))

        # Line rate: lines per second the camera scans (0 = maximum). Line-to-line time = 1 / rate.
        self.line_rate_box = QSpinBox()
        self.line_rate_box.setRange(0, 20000)
        self.line_rate_box.setSingleStep(500)
        self.line_rate_box.setSuffix(" lines/s")
        self.line_rate_box.setSpecialValueText("max")
        self.line_rate_box.setMinimumWidth(120)
        self.line_rate_box.setKeyboardTracking(False)
        self.line_rate_box.setToolTip("Camera line rate setting (0 = maximum for this exposure).\n"
                                      "Line-to-line time = 1 / line rate, e.g. 4000 lines/s = 250 us per line.\n"
                                      "All camera settings are backed up to camera_backups\\ before the first change.")
        self.line_rate_box.valueChanged.connect(lambda v: self.line_rate_requested.emit(int(v)))
        self.line_time_label = QLabel("")
        self.line_time_label.setStyleSheet("color: gray;")

        self.range_label = QLabel("")
        self.range_label.setStyleSheet("color: gray;")

        # View settings (only change the screen, not the camera):
        self.stretch_box = QSpinBox()
        self.stretch_box.setRange(1, 20)
        self.stretch_box.setSuffix(" x")
        self.stretch_box.setValue(int(v_stretch))
        self.stretch_box.setToolTip("Screen pixels per camera line (picture height = lines x this)")
        self.stretch_box.valueChanged.connect(self.view.set_v_stretch)

        self.grid_check = QCheckBox("Grid")
        self.grid_check.setChecked(show_grid)

        # Detection thresholds (0-255, on the blue colour):
        # darker than LOWER or brighter than UPPER = something is in that channel.
        self.lower_box = QSpinBox()
        self.lower_box.setRange(0, 255)
        self.lower_box.setValue(int(lower))
        self.lower_box.setToolTip("Darker than this = something in the channel (LED ON)")
        self.upper_box = QSpinBox()
        self.upper_box.setRange(0, 255)
        self.upper_box.setValue(int(upper))
        self.upper_box.setToolTip("Brighter than this = something in the channel (LED ON)")
        for box in (self.lower_box, self.upper_box):
            box.setKeyboardTracking(False)
            box.valueChanged.connect(self._on_thresholds_changed)

        settings_row = QHBoxLayout()
        settings_row.addWidget(QLabel("Frame height:"))
        settings_row.addWidget(self.frame_combo)
        settings_row.addSpacing(15)
        settings_row.addWidget(QLabel("Exposure:"))
        settings_row.addWidget(self.exposure_box)
        settings_row.addWidget(self.range_label)
        settings_row.addSpacing(15)
        settings_row.addWidget(QLabel("Line rate:"))
        settings_row.addWidget(self.line_rate_box)
        settings_row.addWidget(self.line_time_label)
        settings_row.addSpacing(15)
        settings_row.addWidget(QLabel("V-stretch:"))
        settings_row.addWidget(self.stretch_box)
        settings_row.addSpacing(15)
        settings_row.addWidget(self.grid_check)
        settings_row.addSpacing(25)
        settings_row.addWidget(QLabel("Detect:  lower"))
        settings_row.addWidget(self.lower_box)
        settings_row.addWidget(QLabel("upper"))
        settings_row.addWidget(self.upper_box)
        settings_row.addStretch()
        self.set_camera_controls_enabled(False)   # until the camera is connected

        # --- bottom row: numbers ------------------------------------------------
        self.info_label = QLabel("-")
        self.info_label.setStyleSheet("color: gray;")

        layout = QVBoxLayout(self)
        layout.addLayout(top_row)
        layout.addWidget(self.view, stretch=1)
        layout.addLayout(settings_row)
        layout.addWidget(self.info_label)

    # ------------------------------------------------------------- controls
    def set_camera_controls_enabled(self, enabled):
        self.frame_combo.setEnabled(enabled)
        self.exposure_box.setEnabled(enabled)
        self.line_rate_box.setEnabled(enabled)

    def _on_thresholds_changed(self, _value):
        self.thresholds_changed.emit(self.lower_box.value(), self.upper_box.value())

    def _on_frame_height_chosen(self, index):
        self.frame_lines_requested.emit(int(self.frame_combo.itemData(index)))

    def show_settings(self, settings):
        """
        Show the camera's REAL values (read back from the camera).
        blockSignals: changing the widgets here must not send a new request.
        """
        lines = settings.get("frame_lines")
        if lines:
            index = self.frame_combo.findData(lines)
            if index < 0:                # height not in our list -> add it
                self.frame_combo.addItem(f"{lines} lines", lines)
                index = self.frame_combo.count() - 1
            self.frame_combo.setCurrentIndex(index)

        exp_min, exp_max = settings.get("exposure_min"), settings.get("exposure_max")
        self.exposure_box.blockSignals(True)
        if exp_min is not None and exp_max is not None and exp_max > exp_min:
            self.exposure_box.setRange(exp_min, exp_max)
            self.range_label.setText(f"(range {exp_min:g} - {exp_max:g} us)")
        if "exposure_us" in settings:
            self.exposure_box.setValue(settings["exposure_us"])
        self.exposure_box.blockSignals(False)

        rate = settings.get("line_rate")
        if rate is not None:
            self.line_rate_box.blockSignals(True)
            self.line_rate_box.setValue(max(0, int(rate)))
            self.line_rate_box.blockSignals(False)
            self.line_time_label.setText(f"({1e6 / rate:.0f} us per line)" if rate > 0 else "(max)")

        self.set_camera_controls_enabled(True)

    # ----------------------------------------------------------- status/info
    def set_status(self, text, ok):
        colour = GREEN if ok else RED
        self.status_label.setText(f"<span style='color:{colour}'>●</span> {text}")
        if not ok:
            self.set_camera_controls_enabled(False)

    def set_info(self, text):
        self.info_label.setText(text)
