"""
ui/decision_view.py - The DECISION LINE picture, just above the ejector LED bar.

    DECISION LINE (camera scan line) ─────●──────────●──────────  <- camera sees the object here
                                          │          │   falls during the Delay
    NOZZLES (ejector LEDs below)     ─────▼──────────▼──────────  <- ejector ON here
    [1][2][3] ...                 ejector LEDs                 ... [63]

  * A dot appears on the decision line in the channel where a camera saw an object.
    Colour = who saw it: orange FRONT, cyan REAR, red BOTH.
  * The dot falls to the nozzle line during the delay. Then the LED below switches ON.
  * A 10 ms fall is too fast for the eye, so the picture shows the fall over at least
    150 ms. The REAL times are in the Logs window.

The 63 channel columns line up with the LED bar below (same width, same cells).
"""

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import QSizePolicy, QWidget

COLOURS = {"BOTH": QColor(255, 40, 40), "FRONT": QColor(255, 150, 0), "REAR": QColor(0, 200, 255)}
BACKGROUND = QColor(16, 22, 26)
LINE_SCAN = QColor(255, 230, 0)        # yellow: decision line
LINE_NOZZLE = QColor(140, 150, 158)    # grey: nozzle line
TEXT = QColor(200, 200, 200)


class DecisionLineView(QWidget):

    def __init__(self, channels=63):
        super().__init__()
        self.channels = channels
        self.objects = []                # from core.EjectScheduler.in_flight()
        self.delay_ms = 0.0
        self.setFixedHeight(78)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip("Decision line: where the cameras see an object. "
                        "Dots fall to the nozzles during the Delay (shown slower so you can see it).")

    def set_state(self, objects, delay_ms):
        if objects or self.objects or delay_ms != self.delay_ms:
            self.objects = objects
            self.delay_ms = delay_ms
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), BACKGROUND)

        w = self.width()
        top, bottom = 22.0, self.height() - 8.0       # decision line / nozzle line
        cell = w / self.channels

        # Labels
        painter.setFont(QFont("Arial", 8, QFont.Bold))
        painter.setPen(LINE_SCAN)
        painter.drawText(QRectF(6, 2, w / 2, 16), Qt.AlignLeft | Qt.AlignVCenter,
                         "DECISION LINE  (camera scan line - object seen here)")
        painter.setPen(TEXT)
        painter.drawText(QRectF(w / 2, 2, w / 2 - 6, 16), Qt.AlignRight | Qt.AlignVCenter,
                         f"falls during the Delay ({self.delay_ms:.1f} ms) to the NOZZLES  ->  ejector LEDs below")

        # The two lines
        painter.setPen(QPen(LINE_SCAN, 2))
        painter.drawLine(QPointF(0, top), QPointF(w, top))
        painter.setPen(QPen(LINE_NOZZLE, 2, Qt.DashLine))
        painter.drawLine(QPointF(0, bottom), QPointF(w, bottom))

        # Objects: a short trail from the decision line + a dot at the current height
        for obj in self.objects:
            x = (obj["channel"] - 0.5) * cell
            y = top + obj["progress"] * (bottom - top)
            colour = COLOURS.get(obj["by"], TEXT)
            painter.setPen(QPen(colour, 1, Qt.DotLine))
            painter.drawLine(QPointF(x, top), QPointF(x, y))
            painter.setPen(Qt.NoPen)
            painter.setBrush(colour)
            radius = max(3.0, min(6.0, cell / 4))
            painter.drawEllipse(QPointF(x, y), radius, radius)
            if obj["fired"] and obj["progress"] >= 1.0:          # arrived: small "blow" mark
                painter.setPen(QPen(colour, 2))
                painter.drawLine(QPointF(x - radius * 1.8, bottom), QPointF(x + radius * 1.8, bottom))
