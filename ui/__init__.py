"""
UI LAYER - everything you see on screen (PyQt5).

  main_window.py -> the window: FRONT + REAR panels, buttons, refresh timer
  camera_panel.py -> one camera's box: title, status, controls, info line
  camera_view.py  -> draws the frame at its real height + the channel grid
  led_bar.py      -> the 63 ejector LEDs (FRONT + REAR combined)
  decision_view.py -> the decision line: objects falling from the camera to the nozzles
  log_window.py   -> the "Logs" window: one row per ejection (seen at, LED ON at, delay)
  calibration_window.py -> "Calibrate channels": measure the valves so both cameras agree

Rules for this layer:
  * only shows things and passes the user's wishes on to the core layer
  * never calls the camera SDK directly
"""

from ui.main_window import MainWindow  # noqa: F401
