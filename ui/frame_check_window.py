"""
ui/frame_check_window.py - The "Frame check" window.

    Frames per camera: [1000]  [Start]                       [Save report]
    +-------------------------------------------------------------------+
    | === FRONT camera: 1000 frames in 3.33 s (300.3 frames/s) ===       |
    | 1. Ejector status calculated: 1000 of 1000 frames   [OK ]          |
    | 2. Missed frames (camera timestamps): 0  ... (camera SDK): 0 [OK ]  |
    |    Lines: received 16,000, expected 16,000 -> missed 0   [OK ]      |
    | 3. Time per frame: average 0.210 ms, ... max 1.904 ms               |
    |    Budget = one frame = 3.33 ms. Frames over budget: 0 of 1000 [OK ]|
    | === REAR camera ...                                                 |
    +-------------------------------------------------------------------+
Runs while the machine works normally (feeder ON or OFF) - it only counts and times.
"Save report" -> logs\\frame_check_<date_time>.txt + ..._frames.csv (time of every frame)
"""

import os
import time

from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
                             QSpinBox, QVBoxLayout, QWidget)

from config import PROJECT_DIR


class FrameCheckWindow(QWidget):

    def __init__(self, workers):
        super().__init__()
        self.workers = workers
        self.checks = {}
        self.setWindowTitle("Lambton - Frame check")
        self.resize(900, 420)

        self.frames_box = QSpinBox()
        self.frames_box.setRange(10, 1_000_000)
        self.frames_box.setValue(1000)
        self.frames_box.setSingleStep(1000)
        self.per_line_check = QCheckBox("Also log the status of every LINE (experiment)")
        self.per_line_check.setToolTip("Separate experiment: one 64-bit number for EVERY line (16 per frame),\n"
                                       "saved as ..._lines.csv")
        self.start_button = QPushButton("Start")
        self.start_button.clicked.connect(self.on_start)
        self.save_button = QPushButton("Save report")
        self.save_button.clicked.connect(self.on_save)
        self.save_button.setEnabled(False)
        self.progress = QLabel("Click Start. The machine keeps working normally - this only counts and times.")

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QFont("Consolas", 10))

        top = QHBoxLayout()
        top.addWidget(QLabel("Frames per camera:"))
        top.addWidget(self.frames_box)
        top.addWidget(self.per_line_check)
        top.addWidget(self.start_button)
        top.addStretch()
        top.addWidget(self.save_button)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.progress)
        layout.addWidget(self.text, stretch=1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)

    def on_start(self):
        n = self.frames_box.value()
        per_line = self.per_line_check.isChecked()
        self.checks = {name: w.start_frame_check(n, per_line=per_line) for name, w in self.workers.items()}
        self.text.clear()
        self.save_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self.timer.start(200)

    def poll(self):
        parts = [f"{name} {c.received:,} / {c.target:,}" for name, c in self.checks.items()]
        self.progress.setText("Counting ...  " + "   |   ".join(parts))
        if all(c.done for c in self.checks.values()):
            self.timer.stop()
            self.start_button.setEnabled(True)
            self.save_button.setEnabled(True)
            self.progress.setText(f"Done ({self.started_at}).")
            self.text.setPlainText(self.report_text())
        elif time.perf_counter() - min(c.started for c in self.checks.values()) > 3 + self.frames_box.value() / 50:
            self.timer.stop()                    # a camera sends no frames
            self.start_button.setEnabled(True)
            self.progress.setText("Stopped: a camera did not send enough frames (" + ", ".join(parts) + ")")

    def report_text(self):
        head = [f"Frame check {self.started_at} - {self.frames_box.value():,} frames per camera", ""]
        return "\n".join(head + [c.text() + "\n" for c in self.checks.values()])

    def on_save(self):
        folder = os.path.join(PROJECT_DIR, "logs")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, time.strftime("frame_check_%Y%m%d_%H%M%S.txt"))
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.report_text())
        frames_path = path[:-4] + "_frames.csv"
        lines_path = path[:-4] + "_lines.csv"
        line_rows = 0
        for i, check in enumerate(self.checks.values()):
            check.write_csv(frames_path, append=i > 0)
            if check.per_line:
                line_rows += check.write_lines_csv(lines_path, append=line_rows > 0)
        QMessageBox.information(self, "Save report", f"Saved:\n{path}\n\nEvery frame (for a graph):\n{frames_path}"
                                + (f"\n\nEvery line ({line_rows:,} rows):\n{lines_path}" if line_rows else ""))
