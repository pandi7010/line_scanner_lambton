"""
ui/calibration_window.py - "Calibrate channels": make FRONT and REAR agree on the valves.

Why: with equal channels (start..end / 63) the two cameras do not land on exactly the
same valves (each camera has its own lens and view), so one object can be channel 3
in one camera and 2 or 4 in the other. Calibration measures where the valves really are.

Steps (the window shows them too):
  1. Empty chute, lights ON  -> click "Learn empty chute"
  2. Hold a thin wire / needle exactly in front of the CENTRE of a valve
     (start with 1, then 16, 32, 48, 63), type its number, click "Measure"
     -> the wire's pixel is measured in BOTH cameras (median of the last second)
  3. "Apply" -> both cameras use the measured channels at once (grid + LEDs)
  4. "Save"  -> stored in camera_config.json (used at every start)

    +----------------------------------------------------------------------+
    | Live:  FRONT pixel 1911.4 (strength 14) -> channel 30                |
    |        REAR  pixel 1939.0 (strength 12) -> channel 31   <- disagree! |
    | [Learn empty chute]    Valve [ 1 ]  [Measure]                        |
    | Valve | FRONT pixel | REAR pixel                                      |
    | [Remove selected] [Remove all]                                        |
    | [Apply] [Save to camera_config.json] [Back to equal channels]        |
    +----------------------------------------------------------------------+
"""

import time
from collections import deque

import numpy as np
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                             QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from core import ChannelMap, column_profile, find_object

READ_EVERY_MS = 100         # live reading 10x per second
MEASURE_SECONDS = 1.0       # "Measure" uses the median of the readings of the last second
SEARCH_MARGIN = 60          # look this many pixels beyond the current chute edges
# Valves are ~62 px apart in the picture. Between two measured valves the wire must have
# moved between MIN and MAX pixels per valve - otherwise it was not moved, or the valve
# number is wrong.
MIN_PX_PER_VALVE, MAX_PX_PER_VALVE = 30.0, 110.0


class CalibrationWindow(QWidget):

    def __init__(self, workers, camera_configs, channels, apply_map, save_points):
        """
        workers        : {"FRONT": CameraWorker, "REAR": CameraWorker}
        camera_configs : config["cameras"] (start_pixel / end_pixel / channel_points)
        channels       : 63
        apply_map      : function(name, ChannelMap) - use a map now (detection + grid)
        save_points    : function({name: {channel: pixel}}) - write to camera_config.json
        """
        super().__init__()
        self.workers = workers
        self.cfg = camera_configs
        self.channels = channels
        self.apply_map = apply_map
        self.save_points = save_points
        self.names = list(workers)

        self.empty = {}                                   # name -> profile of the empty chute
        self.readings = {n: deque() for n in self.names}  # name -> (time, centre)
        # measured points: {valve: {name: pixel}}  - start with what is saved already
        self.points = {}
        for n in self.names:
            for ch, px in (self.cfg[n].get("channel_points") or {}).items():
                self.points.setdefault(int(ch), {})[n] = float(px)

        self.setWindowTitle("Lambton - Calibrate channels")
        self.resize(760, 640)

        steps = QLabel(
            "<b>1.</b> Empty chute, lights ON: click <b>Learn empty chute</b>.<br>"
            "<b>2.</b> Hold a thin wire / needle exactly in front of the <b>centre of a valve</b>, "
            "type the valve number, click <b>Measure</b>. Do valves <b>1, 16, 32, 48, 63</b> "
            "(more = better, at least 2).<br>"
            "<b>3.</b> <b>Apply</b> = use it now.  <b>4.</b> <b>Save</b> = keep it for the next start.")
        steps.setWordWrap(True)

        # --- live reading -------------------------------------------------
        self.live = {}
        live_box = QVBoxLayout()
        for n in self.names:
            self.live[n] = QLabel(f"{n}: -")
            self.live[n].setStyleSheet("font-family: Consolas, monospace; font-size: 13px;")
            live_box.addWidget(self.live[n])
        self.agree_label = QLabel("")
        live_box.addWidget(self.agree_label)

        # --- measure row --------------------------------------------------
        self.learn_button = QPushButton("Learn empty chute")
        self.learn_button.setToolTip("Nothing in the chute! Stores the empty picture, so dust spots are ignored.")
        self.learn_button.clicked.connect(self.on_learn_empty)
        self.empty_label = QLabel("not learned (dust spots may disturb)")
        self.empty_label.setStyleSheet("color: #c0392b;")

        self.valve_box = QSpinBox()
        self.valve_box.setRange(1, channels)
        self.valve_box.valueChanged.connect(self.update_hint)
        self.hint_label = QLabel("")
        self.hint_label.setStyleSheet("color: gray;")
        measure_button = QPushButton("Measure")
        measure_button.clicked.connect(self.on_measure)

        measure_row = QHBoxLayout()
        measure_row.addWidget(self.learn_button)
        measure_row.addWidget(self.empty_label)
        measure_row.addStretch()
        measure_row.addWidget(QLabel("Valve:"))
        measure_row.addWidget(self.valve_box)
        measure_row.addWidget(measure_button)

        # --- points table ---------------------------------------------------
        self.table = QTableWidget(0, 1 + len(self.names))
        self.table.setHorizontalHeaderLabels(["Valve"] + [f"{n} pixel" for n in self.names])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        remove_button = QPushButton("Remove selected")
        remove_button.clicked.connect(self.on_remove)
        clear_button = QPushButton("Remove all")
        clear_button.clicked.connect(self.on_clear)
        table_row = QHBoxLayout()
        table_row.addWidget(remove_button)
        table_row.addWidget(clear_button)
        table_row.addStretch()

        # --- result + apply/save ------------------------------------------------
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        apply_button = QPushButton("Apply")
        apply_button.setToolTip("Use the measured channels now (grid + detection + LEDs)")
        apply_button.clicked.connect(self.on_apply)
        save_button = QPushButton("Save to camera_config.json")
        save_button.clicked.connect(self.on_save)
        equal_button = QPushButton("Back to equal channels")
        equal_button.setToolTip("Forget the calibration: 63 equal channels between start and end pixel")
        equal_button.clicked.connect(self.on_equal)
        bottom = QHBoxLayout()
        bottom.addWidget(apply_button)
        bottom.addWidget(save_button)
        bottom.addStretch()
        bottom.addWidget(equal_button)

        layout = QVBoxLayout(self)
        layout.addWidget(steps)
        layout.addLayout(live_box)
        layout.addLayout(measure_row)
        layout.addWidget(self.hint_label)
        layout.addWidget(self.table)
        layout.addLayout(table_row)
        layout.addWidget(self.result_label)
        layout.addLayout(bottom)

        self.refresh_table()
        self.update_hint()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.read_live)
        self.timer.start(READ_EVERY_MS)

    # --------------------------------------------------------- live reading
    def _search_range(self, name):
        cmap = self.workers[name].detector.channel_map
        return cmap.start - SEARCH_MARGIN, cmap.end + SEARCH_MARGIN

    def read_live(self):
        now = time.monotonic()
        channel_now = {}
        for n in self.names:
            worker = self.workers[n]
            frame, _ = worker.frames.get()
            cmap = worker.detector.channel_map
            found = None
            if frame is not None:
                found = find_object(frame, colour_index=worker.detector.colour_index,
                                    search=self._search_range(n), reference=self.empty.get(n))
            readings = self.readings[n]
            if found:
                readings.append((now, found["centre"]))
                ch = int(cmap.channel_of(found["centre"]))
                channel_now[n] = ch
                self.live[n].setText(f"{n:5}  pixel {found['centre']:7.1f}  (strength {found['strength']:4.0f},"
                                     f" {found['width']:2d} px wide)  ->  channel {ch}")
            else:
                self.live[n].setText(f"{n:5}  no object found")
            while readings and now - readings[0][0] > MEASURE_SECONDS:
                readings.popleft()

        if len(channel_now) == len(self.names) and len(set(channel_now.values())) == 1:
            self.agree_label.setText("<span style='color:#27ae60'>Both cameras: same channel</span>")
        elif len(channel_now) == len(self.names):
            self.agree_label.setText("<span style='color:#c0392b'><b>Cameras disagree</b> on the channel "
                                     "- calibration needed</span>")
        else:
            self.agree_label.setText("")

    # ------------------------------------------------------------- buttons
    def on_learn_empty(self):
        missing = []
        for n in self.names:
            frame, _ = self.workers[n].frames.get()
            if frame is None:
                missing.append(n)
                continue
            self.empty[n] = column_profile(frame, self.workers[n].detector.colour_index)
        if missing:
            QMessageBox.warning(self, "Learn empty chute", "No picture from: " + ", ".join(missing))
        if self.empty:
            self.empty_label.setText("learned " + time.strftime("%H:%M:%S"))
            self.empty_label.setStyleSheet("color: #27ae60;")

    def update_hint(self, *_):
        """Where valve N roughly is now (with the channels in use), to help find it."""
        valve = self.valve_box.value()
        parts = [f"{n} ~ pixel {self.workers[n].detector.channel_map.centre(valve):.0f}" for n in self.names]
        self.hint_label.setText(f"Put the wire at the centre of valve {valve}, then wait ~1 s before "
                                f"Measure. Roughly expected: " + ",  ".join(parts))

    def _check_against_others(self, name, valve, pixel):
        """Wire must have moved by a sensible distance compared with the other measured valves."""
        for other_valve, pts in self.points.items():
            if other_valve == valve or name not in pts:
                continue
            per_valve = (pixel - pts[name]) / (valve - other_valve)
            if not MIN_PX_PER_VALVE <= per_valve <= MAX_PX_PER_VALVE:
                return (f"{name}: valve {valve} at pixel {pixel:.0f} does not fit valve {other_valve} at "
                        f"pixel {pts[name]:.0f} ({per_valve:.0f} px per valve, expected ~62).\n"
                        f"Did you move the wire to valve {valve}? Is the valve number right?")
        return None

    def on_measure(self):
        valve = self.valve_box.value()
        measured, missing = {}, []
        for n in self.names:
            values = [c for _, c in self.readings[n]]
            if len(values) < 3:
                missing.append(n)
            else:
                measured[n] = round(float(np.median(values)), 1)
        if missing:
            QMessageBox.warning(self, "Measure", "The wire was not found (clearly) in: " + ", ".join(missing)
                                + "\n\nCheck the live reading above. Hold the wire still for a second.")
        if not measured:
            return
        problems = [p for p in (self._check_against_others(n, valve, px) for n, px in measured.items()) if p]
        if problems:
            QMessageBox.warning(self, "Measure - not stored", "\n\n".join(problems))
            return
        self.points.setdefault(valve, {}).update(measured)
        self.refresh_table()
        if valve < self.channels:
            self.valve_box.setValue(min(self.channels, valve + 15 if valve == 1 else valve + 16))

    def on_remove(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        valves = sorted(self.points)
        for r in rows:
            self.points.pop(valves[r], None)
        self.refresh_table()

    def on_clear(self):
        self.points.clear()
        self.refresh_table()

    def _maps(self):
        """Build a ChannelMap per camera from the measured points (or explain why not)."""
        maps, problems = {}, []
        for n in self.names:
            pts = {v: p[n] for v, p in self.points.items() if n in p}
            if len(pts) < 2:
                problems.append(f"{n}: only {len(pts)} point(s), need at least 2")
                continue
            try:
                maps[n] = ChannelMap.from_points(pts, self.channels)
            except ValueError as e:
                problems.append(f"{n}: {e}")
        return maps, problems

    def on_apply(self):
        maps, problems = self._maps()
        for n, cmap in maps.items():
            self.apply_map(n, cmap)
        lines = [f"<b>{n}</b>: {m.describe()}" for n, m in maps.items()] + problems
        self.result_label.setText("<br>".join(lines))
        if problems:
            QMessageBox.warning(self, "Apply", "\n".join(problems))

    def on_save(self):
        maps, problems = self._maps()
        if problems:
            QMessageBox.warning(self, "Save", "Not saved:\n" + "\n".join(problems))
            return
        for n, cmap in maps.items():
            self.apply_map(n, cmap)
        self.save_points({n: {str(ch): px for ch, px in sorted(m.points.items())} for n, m in maps.items()})
        QMessageBox.information(self, "Save", "Calibration saved to camera_config.json and in use.")

    def on_equal(self):
        if QMessageBox.question(self, "Back to equal channels",
                                "Use 63 equal channels again (start..end pixel) and remove the saved "
                                "calibration from camera_config.json?") != QMessageBox.Yes:
            return
        for n in self.names:
            self.apply_map(n, ChannelMap.equal(self.cfg[n]["start_pixel"], self.cfg[n]["end_pixel"],
                                               self.channels))
        self.save_points({n: {} for n in self.names})
        self.result_label.setText("Equal channels in use; calibration removed from camera_config.json.")

    # --------------------------------------------------------------- table
    def refresh_table(self):
        self.table.setRowCount(0)
        for valve in sorted(self.points):
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [str(valve)] + [f"{self.points[valve][n]:.1f}" if n in self.points[valve] else "-"
                                     for n in self.names]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, col, item)
