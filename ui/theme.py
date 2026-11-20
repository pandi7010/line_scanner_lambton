"""
ui/theme.py - Dark theme for all windows (easier on the eyes on long days).

    from ui.theme import apply_dark_theme
    apply_dark_theme(app)            # in main.py, right after QApplication(...)

Switch: "display": {"dark_theme": true} in camera_config.json (false = the normal light look).
Uses Qt's "Fusion" style with dark colours: dark grey windows, light grey text,
soft blue for selected rows. The camera pictures are not changed.
"""

from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import QStyleFactory

WINDOW = QColor(37, 37, 40)          # window background
BASE = QColor(28, 28, 30)            # tables, input boxes
ALT_BASE = QColor(44, 44, 48)        # every second table row
TEXT = QColor(215, 215, 215)         # normal text (not pure white: softer)
DISABLED = QColor(120, 120, 120)
BUTTON = QColor(52, 52, 56)
HIGHLIGHT = QColor(52, 101, 164)     # selected row / item
LINK = QColor(110, 160, 230)


def apply_dark_theme(app):
    app.setStyle(QStyleFactory.create("Fusion"))
    p = QPalette()
    p.setColor(QPalette.Window, WINDOW)
    p.setColor(QPalette.WindowText, TEXT)
    p.setColor(QPalette.Base, BASE)
    p.setColor(QPalette.AlternateBase, ALT_BASE)
    p.setColor(QPalette.ToolTipBase, BUTTON)
    p.setColor(QPalette.ToolTipText, TEXT)
    p.setColor(QPalette.Text, TEXT)
    p.setColor(QPalette.Button, BUTTON)
    p.setColor(QPalette.ButtonText, TEXT)
    p.setColor(QPalette.BrightText, QColor(255, 90, 90))
    p.setColor(QPalette.Link, LINK)
    p.setColor(QPalette.Highlight, HIGHLIGHT)
    p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.PlaceholderText, DISABLED)
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, DISABLED)
    p.setColor(QPalette.Disabled, QPalette.Button, QColor(45, 45, 48))
    app.setPalette(p)
    app.setStyleSheet(
        "QToolTip { color: #d7d7d7; background-color: #343438; border: 1px solid #555; }"
        "QHeaderView::section { background-color: #343438; color: #d7d7d7; padding: 3px;"
        " border: 1px solid #2a2a2d; }"
        "QTableWidget { gridline-color: #3a3a3e; }"
        "QGroupBox { border: 1px solid #4a4a4e; border-radius: 4px; margin-top: 8px; padding-top: 6px; }"
        "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 3px; }"
        "QStatusBar { color: #b8b8b8; }"
    )
