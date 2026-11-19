"""
ui/log_window.py - The "Logs" window: one row per ejection.

    +-----------------------------------------------------------------------------------+
    | Channel [All]  Seen by [All v]  [x] Auto-scroll          [Pause] [Clear] [Export CSV] |
    +----+--------------+---------+---------+--------------+-----------+----------+------+
    | #  | Seen at      | Seen by | Channel | LED ON at    | Delay set | Measured | Diff |
    | 12 | 16:52:03.125 | BOTH    |   16    | 16:52:03.127 |   2.0     |   2.1    | 0.1  |
    +----+--------------+---------+---------+--------------+-----------+----------+------+
    | 120 ejections | measured - set: average 0.3 ms, max 1.2 ms | 4 in the last second |
    +-----------------------------------------------------------------------------------+

A row appears ~150 ms after the object was seen (then it is certain whether
one or both cameras saw it). The window reads new rows 4 times per second.
"""

import os
import time

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                             QHeaderView, QLabel, QMessageBox, QPushButton, QSpinBox,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from config import PROJECT_DIR
from core import clock_text, write_csv

MAX_ROWS = 5000          # rows shown in the table (older rows are removed from the top)
MAX_KEPT = 20000         # rows kept by this window (for filters and export)
COLUMNS = ["#", "Seen at", "Seen by", "Channel", "LED ON at",
           "Delay set (ms)", "Delay measured (ms)", "Difference (ms)"]


class LogWindow(QWidget):

    def __init__(self, event_log, channels=63):
        super().__init__()
        self.event_log = event_log
        self.events = []                 # all rows this window has received
        self.last_seq = 0                # newest row number already read
        self.paused = False

        self.setWindowTitle("Lambton - Ejection Log")
        self.resize(950, 650)

        # --- filters and buttons --------------------------------------------
        self.channel_filter = QSpinBox()
        self.channel_filter.setRange(0, channels)
        self.channel_filter.setSpecialValueText("All")     # 0 is shown as "All"
        self.channel_filter.valueChanged.connect(self.rebuild_table)

        self.seen_by_filter = QComboBox()
        self.seen_by_filter.addItems(["All", "BOTH", "FRONT", "REAR"])
        self.seen_by_filter.currentIndexChanged.connect(self.rebuild_table)

        self.autoscroll = QCheckBox("Auto-scroll")
        self.autoscroll.setChecked(True)

        self.pause_button = QPushButton("Pause")
        self.pause_button.setCheckable(True)
        self.pause_button.setToolTip("Stop adding new rows (ejections are still recorded)")
        self.pause_button.toggled.connect(self.on_pause)

        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self.on_clear)

        export_button = QPushButton("Export CSV")
        export_button.setToolTip("Save the rows shown by the filter to a CSV file (Excel)")
        export_button.clicked.connect(self.on_export)

        top = QHBoxLayout()
        top.addWidget(QLabel("Channel:"))
        top.addWidget(self.channel_filter)
        top.addSpacing(10)
        top.addWidget(QLabel("Seen by:"))
        top.addWidget(self.seen_by_filter)
        top.addSpacing(10)
        top.addWidget(self.autoscroll)
        top.addStretch()
        top.addWidget(self.pause_button)
        top.addWidget(clear_button)
        top.addWidget(export_button)

        info = QLabel("One row = one ejection. <b>Seen at</b> = first camera saw the object. "
                      "<b>LED ON at</b> = ejector LED switched ON. <b>Delay measured</b> = LED ON at "
                      "- Seen at. <b>Difference</b> = measured - set (how late the PC was).")
        info.setWordWrap(True)

        # --- table ----------------------------------------------------------
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        self.summary = QLabel("No ejections yet")
        self.summary.setStyleSheet("color: gray;")

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
        return (channel == 0 or e.channel == channel) and (seen_by == "All" or e.seen_by == seen_by)

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
            values = [e.seq, clock_text(e.seen_time), e.seen_by, e.channel, clock_text(e.on_time),
                      f"{e.delay_set_ms:.1f}", f"{e.delay_ms:.1f}", f"{e.difference_ms:+.1f}"]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignCenter)
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
        if not self.events:
            self.summary.setText("No ejections yet")
            return
        shown = self._shown_events()[-MAX_ROWS:]
        text = f"{len(self.events):,} ejections received   |   {len(shown):,} shown"
        if shown:
            diffs = [e.difference_ms for e in shown]
            text += (f"   |   measured - set:  average {sum(diffs) / len(diffs):+.1f} ms, "
                     f"max {max(diffs):+.1f} ms")
        now = time.time()
        last_second = sum(1 for e in self.events[-2000:] if now - e.seen_time <= 1.0)
        self.summary.setText(text + f"   |   {last_second} in the last second")

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
