"""
ui/log_window.py - The "Logs" window: one row per object (ejection).

    Channel [All]  Seen by [All v]  Result [All v]  [x] Auto-scroll     [Pause] [Clear] [Export CSV]
    +---------------+---------+----+---------------+----------+---------------+-------+-------+-------+---------+
    | Touched line  | Seen by | Ch | PC saw at     | Reaction | LED ON at     | Delay | Delay | Late  | Result  | ...
    |               |         |    |               |   (ms)   |               |  set  | meas. | by    |         |
    | 16:52:03.1250 | BOTH    | 16 | 16:52:03.1281 |   3.1    | 16:52:03.1350 | 10.0  | 10.0  | 0.0   | ON TIME |
    +---------------+---------+----+---------------+----------+---------------+-------+-------+-------+---------+
    120 objects | ON TIME 118 | LATE 2 (max 2.3 ms) | MISSED 0 | PC reaction: average 3.4 ms, max 9.8 ms

A row appears when the object has completely passed (or after 1 s).
The window reads new rows 4 times per second.
"""

import os
import time

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                             QHeaderView, QLabel, QMessageBox, QPushButton, QSpinBox,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from config import PROJECT_DIR
from core import clock_text, write_csv

MAX_ROWS = 5000          # rows shown in the table (older rows are removed from the top)
MAX_KEPT = 20000         # rows kept by this window (for filters and export)
COLUMNS = ["#", "Touched line at", "Seen by", "Channel", "PC saw at", "PC reaction (ms)",
           "LED ON at", "Delay set (ms)", "Delay measured (ms)", "Late by (ms)", "Result",
           "Object (ms)", "FRONT pixels", "REAR pixels"]
RESULT_COLOURS = {"ON TIME": QColor(39, 174, 96), "LATE": QColor(230, 126, 34), "MISSED": QColor(192, 57, 43)}


def _num(value):
    return "-" if value is None else f"{value:.1f}"


class LogWindow(QWidget):

    def __init__(self, event_log, channels=63):
        super().__init__()
        self.event_log = event_log
        self.events = []                 # all rows this window has received
        self.last_seq = 0                # newest row number already read
        self.paused = False

        self.setWindowTitle("Lambton - Ejection Log")
        self.resize(1400, 680)

        # --- filters and buttons --------------------------------------------
        self.channel_filter = QSpinBox()
        self.channel_filter.setRange(0, channels)
        self.channel_filter.setSpecialValueText("All")     # 0 is shown as "All"
        self.channel_filter.valueChanged.connect(self.rebuild_table)

        self.seen_by_filter = QComboBox()
        self.seen_by_filter.addItems(["All", "BOTH", "FRONT", "REAR"])
        self.seen_by_filter.currentIndexChanged.connect(self.rebuild_table)

        self.result_filter = QComboBox()
        self.result_filter.addItems(["All", "ON TIME", "LATE", "MISSED"])
        self.result_filter.currentIndexChanged.connect(self.rebuild_table)

        self.autoscroll = QCheckBox("Auto-scroll")
        self.autoscroll.setChecked(True)

        self.pause_button = QPushButton("Pause")
        self.pause_button.setCheckable(True)
        self.pause_button.setToolTip("Stop adding new rows (objects are still recorded)")
        self.pause_button.toggled.connect(self.on_pause)

        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self.on_clear)

        export_button = QPushButton("Export CSV")
        export_button.setToolTip("Save the rows shown by the filter to a CSV file (Excel)")
        export_button.clicked.connect(self.on_export)

        top = QHBoxLayout()
        for label, widget in (("Channel:", self.channel_filter), ("Seen by:", self.seen_by_filter),
                              ("Result:", self.result_filter)):
            top.addWidget(QLabel(label))
            top.addWidget(widget)
            top.addSpacing(10)
        top.addWidget(self.autoscroll)
        top.addStretch()
        top.addWidget(self.pause_button)
        top.addWidget(clear_button)
        top.addWidget(export_button)

        info = QLabel(
            "One row = one object. <b>Touched line at</b> = exact moment it touched the decision line "
            "(camera clock). <b>PC reaction</b> = how long until the PC found it. <b>LED ON at</b> = "
            "touched + delay (or later if the PC was late). <b>Late by</b> = LED ON - planned LED ON. "
            "<b>Result</b>: ON TIME (late &le; 1 ms), LATE, or MISSED (PC too late, no LED). "
            "<b>Object</b> = first to last line of the object.")
        info.setWordWrap(True)

        # --- table ----------------------------------------------------------
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        self.summary = QLabel("No objects yet")

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(info)
        layout.addWidget(self.table)
        layout.addWidget(self.summary)

        # --- read new rows 4x per second ---------------------------------------
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.read_new_events)
        self.timer.start(250)

    # ------------------------------------------------------------- filtering
    def _matches(self, e):
        channel = self.channel_filter.value()
        seen_by = self.seen_by_filter.currentText()
        result = self.result_filter.currentText()
        return ((channel == 0 or e.channel == channel)
                and (seen_by == "All" or e.seen_by == seen_by)
                and (result == "All" or e.result == result))

    def _shown_events(self):
        return [e for e in self.events if self._matches(e)]

    # ------------------------------------------------------------- new rows
    def read_new_events(self):
        new = self.event_log.events_since(self.last_seq)
        if new:
            self.last_seq = new[-1].seq
            self.events.extend(new)
            if len(self.events) > MAX_KEPT:
                del self.events[:len(self.events) - MAX_KEPT]
            if not self.paused:
                self._add_rows([e for e in new if self._matches(e)])
            self._update_summary()

    def _add_rows(self, events):
        if not events:
            return
        events = events[-MAX_ROWS:]
        self.table.setUpdatesEnabled(False)      # faster: draw once at the end
        for e in events:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [e.seq, clock_text(e.seen_time), e.seen_by, e.channel, clock_text(e.found_time),
                      _num(e.reaction_ms), clock_text(e.on_time), _num(e.delay_set_ms), _num(e.delay_ms),
                      _num(e.late_ms), e.result, _num(e.length_ms),
                      e.pixels_text("FRONT"), e.pixels_text("REAR")]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignCenter)
                if COLUMNS[col] == "Result" and e.result in RESULT_COLOURS:
                    item.setForeground(RESULT_COLOURS[e.result])
                self.table.setItem(row, col, item)
        extra = self.table.rowCount() - MAX_ROWS
        for _ in range(max(0, extra)):
            self.table.removeRow(0)
        self.table.setUpdatesEnabled(True)
        if self.autoscroll.isChecked():
            self.table.scrollToBottom()

    def rebuild_table(self, *_):
        """Filter changed: show the matching rows again from the start."""
        self.table.setRowCount(0)
        self._add_rows(self._shown_events())
        self._update_summary()

    def _update_summary(self):
        shown = self._shown_events()
        if not shown:
            self.summary.setText("No objects yet" if not self.events else "No rows match the filter")
            return
        on_time = sum(1 for e in shown if e.result == "ON TIME")
        late = [e.late_ms for e in shown if e.result == "LATE"]
        missed = sum(1 for e in shown if e.result == "MISSED")
        reactions = [e.reaction_ms for e in shown]
        late_text = f"LATE {len(late)}" + (f" (max {max(late):.1f} ms)" if late else "")
        self.summary.setText(
            f"<b>{len(shown):,} objects</b> &nbsp;|&nbsp; "
            f"<span style='color:#27ae60'><b>ON TIME {on_time}</b></span> &nbsp;|&nbsp; "
            f"<span style='color:#e67e22'><b>{late_text}</b></span> &nbsp;|&nbsp; "
            f"<span style='color:#c0392b'><b>MISSED {missed}</b></span> &nbsp;|&nbsp; "
            f"PC reaction: average {sum(reactions) / len(reactions):.1f} ms, max {max(reactions):.1f} ms"
            f" &nbsp;&rarr; Delay must be longer than the max reaction")

    # --------------------------------------------------------------- buttons
    def on_pause(self, paused):
        self.paused = paused
        self.pause_button.setText("Resume" if paused else "Pause")
        if not paused:
            self.rebuild_table()     # catch up with what arrived while paused

    def on_clear(self):
        self.event_log.clear()
        self.events.clear()
        self.table.setRowCount(0)
        self._update_summary()

    def on_export(self):
        events = self._shown_events()
        if not events:
            QMessageBox.warning(self, "Export CSV", "No rows to export.")
            return
        folder = os.path.join(PROJECT_DIR, "logs")
        os.makedirs(folder, exist_ok=True)
        default = os.path.join(folder, time.strftime("ejection_log_%Y%m%d_%H%M%S.csv"))
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", default, "CSV files (*.csv)")
        if not path:
            return
        try:
            write_csv(path, events)
        except Exception as e:           # noqa: BLE001
            QMessageBox.critical(self, "Export CSV", f"Could not save:\n{e}")
            return
        QMessageBox.information(self, "Export CSV", f"Saved {len(events):,} rows to:\n{path}")
