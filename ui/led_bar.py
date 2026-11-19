"""
ui/led_bar.py - 63 ejector LEDs in one row (FRONT + REAR combined).

    EJECTOR LEDs   [1][2][3][4][5] ... [63]

An LED switches ON `delay` ms after FRONT or REAR saw an object in that channel
(core/ejector.py decides when). The colour tells you WHICH camera saw it:

    red    = both cameras
    orange = FRONT only
    cyan   = REAR only
    dark   = nothing

Display only - nothing is sent to the ejector board.
"""

from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import QSizePolicy, QWidget

COLOUR_BOTH = QColor(255, 40, 40)
COLOUR_FRONT = QColor(255, 150, 0)
COLOUR_REAR = QColor(0, 200, 255)
COLOUR_OFF = QColor(45, 52, 58)
BACKGROUND = QColor(16, 22, 26)


class LedBar(QWidget):

    def __init__(self, channels=63):
        super().__init__()
        self.channels = channels
        self.lit = {}                    # {channel: "FRONT" / "REAR" / "BOTH"}
        self.setFixedHeight(40)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip("Ejector LEDs: red = both cameras, orange = FRONT only, cyan = REAR only")

    def set_lit(self, lit):
        """lit: {channel: "FRONT"/"REAR"/"BOTH"}. Only redraw when something changed."""
        if lit != self.lit:
            self.lit = dict(lit)
            self.update()

    def _colour(self, ch):
        return {"BOTH": COLOUR_BOTH, "FRONT": COLOUR_FRONT, "REAR": COLOUR_REAR}.get(self.lit.get(ch))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), BACKGROUND)

        cell_w = self.width() / self.channels
        led_h = self.height() - 8
        painter.setFont(QFont("Arial", 7 if cell_w >= 18 else 6))

        for i in range(self.channels):
            ch = i + 1
            rect = QRectF(i * cell_w + 1.5, 4, cell_w - 3, led_h)
            colour = self._colour(ch)

            painter.setPen(QPen(QColor(70, 80, 88), 1))
            painter.setBrush(colour if colour else COLOUR_OFF)
            painter.drawRoundedRect(rect, 3, 3)

            painter.setPen(Qt.black if colour else QColor(140, 150, 158))
            painter.drawText(rect, Qt.AlignCenter, str(ch))
