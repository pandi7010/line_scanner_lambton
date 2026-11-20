"""
ui/log_window.py - The "Logs" window: one row per channel of an object (ejection).
"Object #" = rows of the same physical object have the same number.

    Channel [All]  Seen by [All v]  Result [All v]  [x] Auto-scroll     [Pause] [Clear] [Export CSV]
    +---------------+---------+----+---------------+----------+---------------+-------+-------+-------+---------+
    | Touched line  | Seen by | Ch | PC saw at     | Reaction | LED ON at     | Delay | Delay | Late  | Result  | ...
    |               |         |    |               |   (ms)   |               |  set  | meas. | by    |         |
    | 16:52:03.1250 | BOTH    | 16 | 16:52:03.1281 |   3.1    | 16:52:03.1350 | 10.0  | 10.0  | 0.0   | ON TIME |
    +---------------+---------+----+---------------+----------+---------------+-------+-------+-------+---------+
    31 rows = 10 objects | ON TIME 118 | LATE 2 (max 2.3 ms) | MISSED 0 | PC reaction: average 3.4 ms, max 9.8 ms

Click a row -> "Object snap" on the right shows the picture of that object (FRONT + REAR).
Export CSV also saves the pictures (PNG) into a folder next to the CSV file.

A row appears when the object has completely passed (or after 1 s).
The window reads new rows 2 times per second.

SPEED (feeder test 20 Nov 2026: 190 rows per second): the work per update depends only on
the NEW rows, never on all rows so far - the summary is counted up row by row, the filter
is read once per update, old table rows are removed in one step, and a hidden window does
not fill its table at all. Otherwise the window takes the time the cameras and the
ejector need (Python runs one thing at a time) and valves come LATE.
"""

import os
import time

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                             QHeaderView, QLabel, QMessageBox, QPushButton, QSpinBox, QSplitter,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from config import PROJECT_DIR
from core import clock_text, write_csv
from ui.snap_view import SnapView, save_snaps

MAX_ROWS = 1000          # rows shown in the table (older rows are removed from the top; Export has all)
MAX_KEPT = 20000         # rows kept by this window (for filters and export)
COLUMNS = ["#", "Object #", "Touched line at", "Seen by", "Channel", "PC saw at", "PC reaction (ms)",
           "LED ON at", "Delay set (ms)", "Delay measured (ms)", "Late by (ms)", "Result",
           "Object (ms)", "FRONT pixels", "REAR pixels", "Valve"]
RESULT_COLOURS = {"ON TIME": QColor(39, 174, 96), "LATE": QColor(230, 126, 34), "MISSED": QColor(192, 57, 43)}


def _num(value):
    return "-" if value is None else f"{value:.1f}"


class LogWindow(QWidget):

    def __init__(self, event_log, channels=63):
        super().__init__()
        self.event_log = event_log
        self.events = []                 # all rows this window has received
        self.by_seq = {}                 # row number -> row (to find the picture of a clicked row)
        self.last_seq = 0                # newest row number already read
        self.paused = False

        self.setWindowTitle("Lambton - Ejection Log")
        self.resize(1750, 720)

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
            "One row = one channel of an object. <b>Object #</b> = rows of the same object (neighbour "
            "channels, both cameras, within 5 ms) share one number. <b>Touched line at</b> = exact moment it touched the decision line "
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
        self.table.itemSelectionChanged.connect(self.on_row_selected)

        self.snap_view = SnapView()
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.table)
        splitter.addWidget(self.snap_view)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)

        self.summary = QLabel("No objects yet")
        self._read_filter()
        self._reset_counts()

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(info)
        layout.addWidget(splitter, stretch=1)
        layout.addWidget(self.summary)

        # --- read new rows 4x per second ---------------------------------------
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.read_new_events)
        self.timer.start(500)

    # ------------------------------------------------------------- filtering
    def _read_filter(self):
        """Read the filter boxes ONCE (not once per row)."""
        self._filter = (self.channel_filter.value(), self.seen_by_filter.currentText(),
                        self.result_filter.currentText())

    def _matches(self, e):
        channel, seen_by, result = self._filter
        return ((channel == 0 or e.channel == channel)
                and (seen_by == "All" or e.seen_by == seen_by)
                and (result == "All" or e.result == result))

    def _shown_events(self):
        return [e for e in self.events if self._matches(e)]

    def _reset_counts(self):
        self._counts = {"rows": 0, "ON TIME": 0, "LATE": 0, "MISSED": 0, "late_max": 0.0,
                        "reaction_sum": 0.0, "reaction_max": 0.0, "objects": set()}

    def _count(self, events):
        """Summary counted up row by row (fast also with 20,000 rows)."""
        c = self._counts
        for e in events:
            c["rows"] += 1
            c[e.result] = c.get(e.result, 0) + 1
            if e.result == "LATE" and e.late_ms is not None:
                c["late_max"] = max(c["late_max"], e.late_ms)
            c["reaction_sum"] += e.reaction_ms
            c["reaction_max"] = max(c["reaction_max"], e.reaction_ms)
            c["objects"].add(e.object_no)

    # ------------------------------------------------------------- new rows
    def read_new_events(self):
        new = self.event_log.events_since(self.last_seq)
        if new:
            self.last_seq = new[-1].seq
            self.events.extend(new)
            for e in new:
                self.by_seq[e.seq] = e
            if len(self.events) > MAX_KEPT:
                for e in self.events[:len(self.events) - MAX_KEPT]:
                    self.by_seq.pop(e.seq, None)
                del self.events[:len(self.events) - MAX_KEPT]
            matching = [e for e in new if self._matches(e)]
            self._count(matching)
            if not self.paused and self.isVisible():
                self._add_rows(matching)
            elif not self.paused:
                self._table_stale = True         # hidden: fill the table when it is shown again
            self._update_summary()

    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self, "_table_stale", False):
            self._table_stale = False
            self.rebuild_table()

    def _add_rows(self, events):
        if not events:
            return
        events = events[-MAX_ROWS:]
        self.table.setUpdatesEnabled(False)      # faster: draw once at the end
        first = self.table.rowCount()
        self.table.setRowCount(first + len(events))          # all new rows in one step
        for row, e in enumerate(events, start=first):
            values = [e.seq, e.object_no, clock_text(e.seen_time), e.seen_by, e.channel, clock_text(e.found_time),
                      _num(e.reaction_ms), clock_text(e.on_time), _num(e.delay_set_ms), _num(e.delay_ms),
                      _num(e.late_ms), e.result, _num(e.length_ms),
                      e.pixels_text("FRONT"), e.pixels_text("REAR"), "FIRED" if e.valve else "-"]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignCenter)
                if COLUMNS[col] == "Result" and e.result in RESULT_COLOURS:
                    item.setForeground(RESULT_COLOURS[e.result])
                self.table.setItem(row, col, item)
        extra = self.table.rowCount() - MAX_ROWS
        if extra > 0:
            self.table.model().removeRows(0, extra)          # old rows out in one step
        self.table.setUpdatesEnabled(True)
        if self.autoscroll.isChecked():
            self.table.scrollToBottom()

    def rebuild_table(self, *_):
        """Filter changed: show the matching rows again from the start."""
        self._read_filter()
        shown = self._shown_events()
        self._reset_counts()
        self._count(shown)
        self.table.setRowCount(0)
        self._add_rows(shown)
        self._update_summary()

    def _update_summary(self):
        c = self._counts
        if not c["rows"]:
            self.summary.setText("No objects yet" if not self.events else "No rows match the filter")
            return
        late_text = f"LATE {c['LATE']}" + (f" (max {c['late_max']:.1f} ms)" if c["LATE"] else "")
        self.summary.setText(
            f"<b>{c['rows']:,} rows = {len(c['objects']):,} objects</b> &nbsp;|&nbsp; "
            f"<span style='color:#27ae60'><b>ON TIME {c['ON TIME']}</b></span> &nbsp;|&nbsp; "
            f"<span style='color:#e67e22'><b>{late_text}</b></span> &nbsp;|&nbsp; "
            f"<span style='color:#c0392b'><b>MISSED {c['MISSED']}</b></span> &nbsp;|&nbsp; "
            f"PC reaction: average {c['reaction_sum'] / c['rows']:.1f} ms, max {c['reaction_max']:.1f} ms"
            f" &nbsp;&rarr; Delay must be longer than the max reaction")

    def on_row_selected(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        if item is not None:
            self.snap_view.show_event(self.by_seq.get(int(item.text())))

    # --------------------------------------------------------------- buttons
    def on_pause(self, paused):
        self.paused = paused
        self.pause_button.setText("Resume" if paused else "Pause")
        if not paused:
            self.rebuild_table()     # catch up with what arrived while paused

    def on_clear(self):
        self.event_log.clear()
        self.events.clear()
        self.by_seq.clear()
        self._reset_counts()
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
            folder = os.path.splitext(path)[0] + "_snaps"
            pictures = save_snaps(events, folder)
        except Exception as e:           # noqa: BLE001
            QMessageBox.critical(self, "Export CSV", f"Could not save:\n{e}")
            return
        QMessageBox.information(self, "Export CSV", f"Saved {len(events):,} rows to:\n{path}"
                                + (f"\n\n{pictures:,} object pictures to:\n{folder}" if pictures else ""))
