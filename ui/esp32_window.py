"""
ui/esp32_window.py - The "ESP32 Ejector" window: connect the valve board, test valves, arm.

    Port: [COM24 [CP210x] ... v] [Refresh] [Find ESP32]   [Connect] [Disconnect]
    Status: connected COM24 (230400 baud)

    TEST (air ON or OFF - you hear / see the valve):
      Valve [17]  Pulse [20 ms]  [Pulse valve]   [Walk 1 -> 63]  [Stop walk]
                         [ ALL VALVES OFF ]
    [ ] Fire valves from the cameras   (OFF at every start)

Safety:
  * Nothing is connected at start. Connect = all valves OFF + board watchdog ON
    (the board closes any valve after 250 ms without a new ON command).
  * "Fire valves from the cameras" is OFF at every start - you switch it on.
  * Disconnect, closing the program, or unticking = all valves OFF.
Connecting / searching runs in the background, so the window does not freeze.
"""

import threading

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QLabel, QMessageBox,
                             QPushButton, QSpinBox, QVBoxLayout, QWidget)

from hardware import esp32_ejector


class Esp32Window(QWidget):

    def __init__(self, board, ejector, saved_port="", on_port_saved=None):
        """
        board         : hardware.Esp32Ejector
        ejector       : core.EjectScheduler (arm / disarm the real valves)
        saved_port    : last port that worked (from camera_config.json)
        on_port_saved : function(port, baud) - remember the port for the next start
        """
        super().__init__()
        self.board = board
        self.ejector = ejector
        self.saved_port = saved_port
        self.on_port_saved = on_port_saved
        self._job = None                 # background job: (thread, result list, what)
        self._walk_channel = 0

        self.setWindowTitle("Lambton - ESP32 Ejector")
        self.resize(720, 430)

        # --- port -------------------------------------------------------------
        self.port_box = QComboBox()
        self.port_box.setMinimumWidth(330)
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh_ports)
        self.find_button = QPushButton("Find ESP32")
        self.find_button.setToolTip("Ask every ESP32-type USB port (CP210x / CH340) 'are you there?'.\n"
                                    "Only the ejector board answers. No valve is switched.")
        self.find_button.clicked.connect(self.on_find)
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self.on_connect)
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.clicked.connect(self.on_disconnect)
        self.status = QLabel()
        self.status.setWordWrap(True)

        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("Port:"))
        port_row.addWidget(self.port_box, stretch=1)
        port_row.addWidget(refresh_button)
        port_row.addWidget(self.find_button)
        port_row.addSpacing(12)
        port_row.addWidget(self.connect_button)
        port_row.addWidget(self.disconnect_button)

        # --- test -------------------------------------------------------------
        self.valve_box = QSpinBox()
        self.valve_box.setRange(1, esp32_ejector.NUM_CHANNELS)
        self.valve_box.setValue(32)
        self.pulse_box = QSpinBox()
        self.pulse_box.setRange(5, 200)
        self.pulse_box.setValue(20)
        self.pulse_box.setSuffix(" ms")
        self.pulse_button = QPushButton("Pulse valve")
        self.pulse_button.clicked.connect(self.on_pulse)
        self.walk_button = QPushButton("Walk 1 -> 63")
        self.walk_button.setToolTip("Pulse every valve once, 1 to 63, one every 0.3 s")
        self.walk_button.clicked.connect(self.on_walk)
        self.stop_walk_button = QPushButton("Stop walk")
        self.stop_walk_button.clicked.connect(self.stop_walk)
        self.all_off_button = QPushButton("ALL VALVES OFF")
        self.all_off_button.setStyleSheet("background:#c0392b; color:white; font-weight:bold; padding:8px")
        self.all_off_button.clicked.connect(self.on_all_off)

        test_row = QHBoxLayout()
        for widget in (QLabel("Valve:"), self.valve_box, QLabel("Pulse:"), self.pulse_box, self.pulse_button):
            test_row.addWidget(widget)
        test_row.addSpacing(16)
        test_row.addWidget(self.walk_button)
        test_row.addWidget(self.stop_walk_button)
        test_row.addStretch()
        test_box = QGroupBox("Test valves by hand (air ON: you hear a short blast; air OFF: you hear the valve click)")
        test_layout = QVBoxLayout(test_box)
        test_layout.addLayout(test_row)
        test_layout.addWidget(self.all_off_button)

        # --- arm --------------------------------------------------------------
        self.arm_check = QCheckBox("Fire valves from the cameras")
        self.arm_check.setStyleSheet("font-weight:bold")
        self.arm_check.toggled.connect(self.on_arm)
        arm_info = QLabel("When ticked, every ejector LED on the main screen also opens the real valve "
                          "(same delay and blow time). OFF at every start. Untick = all valves OFF at once.")
        arm_info.setWordWrap(True)
        arm_box = QGroupBox("Sorting")
        arm_layout = QVBoxLayout(arm_box)
        arm_layout.addWidget(self.arm_check)
        arm_layout.addWidget(arm_info)

        layout = QVBoxLayout(self)
        layout.addLayout(port_row)
        layout.addWidget(self.status)
        layout.addWidget(test_box)
        layout.addWidget(arm_box)
        layout.addStretch()

        self.walk_timer = QTimer(self)
        self.walk_timer.timeout.connect(self._walk_step)
        self.poll_timer = QTimer(self)           # checks background jobs + connection state
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start(200)

        if not esp32_ejector.SERIAL_AVAILABLE:
            QMessageBox.warning(self, "ESP32", esp32_ejector.SERIAL_ERROR)
        self.refresh_ports()
        self._update_buttons()

    # ---------------------------------------------------------------- ports
    def refresh_ports(self):
        current = self.port_box.currentData() or self.saved_port or self.board.port
        self.port_box.clear()
        for port, label in esp32_ejector.list_ports():
            self.port_box.addItem(label, port)
        if current:
            index = self.port_box.findData(current)
            if index >= 0:
                self.port_box.setCurrentIndex(index)

    def on_find(self):
        if self.board.connected:
            QMessageBox.information(self, "Find ESP32", "Already connected. Disconnect first.")
            return
        self._find_report = []
        self._start_job("find", lambda: esp32_ejector.find_ejector_ports(report=self._find_report))
        self.status.setText("Searching ... (a few seconds)")

    def on_connect(self):
        port = self.port_box.currentData()
        if not port:
            QMessageBox.warning(self, "Connect", "Choose a COM port first (or click Find ESP32).")
            return
        self.ejector.arm(False)
        self._start_job("connect", lambda: self.board.connect(port))
        self.status.setText(f"Connecting to {port} ...")

    def on_disconnect(self):
        self.stop_walk()
        self.arm_check.setChecked(False)
        self.ejector.arm(False)
        self.board.close()
        self._update_buttons()

    # ---------------------------------------------------------------- tests
    def on_pulse(self):
        channel, ms = self.valve_box.value(), self.pulse_box.value()
        self.board.valve_on(channel)
        QTimer.singleShot(ms, lambda: self.board.valve_off(channel))

    def on_walk(self):
        self._walk_channel = 0
        self.walk_timer.start(300)
        self._update_buttons()

    def _walk_step(self):
        self._walk_channel += 1
        if self._walk_channel > esp32_ejector.NUM_CHANNELS or not self.board.connected:
            self.stop_walk()
            return
        channel, ms = self._walk_channel, self.pulse_box.value()
        self.valve_box.setValue(channel)
        self.board.valve_on(channel)
        QTimer.singleShot(ms, lambda: self.board.valve_off(channel))

    def stop_walk(self):
        self.walk_timer.stop()
        self._update_buttons()

    def on_all_off(self):
        self.stop_walk()
        self.arm_check.setChecked(False)
        self.ejector.arm(False)
        self.board.all_off()

    # ------------------------------------------------------------------ arm
    def on_arm(self, on):
        if on:
            self.stop_walk()
        armed = self.ejector.arm(on)
        if on and not armed:
            self.arm_check.setChecked(False)
        self._update_buttons()

    # --------------------------------------------------------- background
    def _start_job(self, what, func):
        result = []
        def work():
            try:
                result.append(func())
            except Exception as exc:     # noqa: BLE001  (never leave the window waiting)
                self.board.error = f"error: {exc}"
                result.append(None)
        thread = threading.Thread(target=work, daemon=True)
        self._job = (thread, result, what)
        thread.start()
        self._update_buttons()

    def _poll(self):
        if self._job and not self._job[0].is_alive():
            _, result, what = self._job
            self._job = None
            value = result[0] if result else None
            if what == "find":
                self.refresh_ports()
                if value:
                    port, baud = value[0]
                    self.port_box.setCurrentIndex(max(0, self.port_box.findData(port)))
                    others = ", ".join(p for p, _ in value[1:])
                    self.status.setText(f"Found the ejector ESP32 on <b>{port}</b> ({baud} baud)"
                                        + (f" - also answering: {others}" if others else "")
                                        + ". Click Connect.")
                else:
                    tried = "<br>".join(f"&nbsp; {p} at {b}: {t}" for p, b, t in self._find_report) \
                        or "&nbsp; no CP210x / CH340 port found - is the USB cable in?"
                    self.status.setText("<b>No ESP32 answered.</b> Is the USB cable in, the board powered, "
                                        "and lambton-main / Arduino closed?<br>" + tried)
            elif what == "connect" and value and self.on_port_saved:
                self.on_port_saved(self.board.port, self.board.baud)
            self._update_buttons(show_status=(what != "find"))
        elif self._job is None:
            # connection lost while running (cable pulled) -> show it, disarm
            if not self.board.connected and self.arm_check.isChecked():
                self.arm_check.setChecked(False)
            state = (self.board.connected, self.board.error, self.arm_check.isChecked())
            if state != getattr(self, "_last_state", None):
                self._update_buttons()

    def _update_buttons(self, show_status=True):
        self._last_state = (self.board.connected, self.board.error, self.arm_check.isChecked())
        busy = self._job is not None
        connected = self.board.connected
        armed = self.arm_check.isChecked()
        walking = self.walk_timer.isActive()
        self.find_button.setEnabled(not busy and not connected)
        self.connect_button.setEnabled(not busy and not connected)
        self.port_box.setEnabled(not busy and not connected)
        self.disconnect_button.setEnabled(not busy and connected)
        for button in (self.pulse_button, self.walk_button):
            button.setEnabled(connected and not armed and not walking)
        self.stop_walk_button.setEnabled(walking)
        self.all_off_button.setEnabled(connected)
        self.arm_check.setEnabled(connected and not walking)
        if not busy and show_status:
            text = self.board.status_text()
            colour = "#27ae60" if connected else "#c0392b"
            extra = " &nbsp; <b style='color:#c0392b'>VALVES FIRE FROM THE CAMERAS</b>" if armed else ""
            self.status.setText(f"<span style='color:{colour}'><b>{text}</b></span>{extra}")

    def closeEvent(self, event):
        # Closing this window keeps the connection and the arm state (the main window shows them).
        self.stop_walk()
        event.accept()
