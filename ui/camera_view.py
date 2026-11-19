"""
ui/camera_view.py - Draws ONE camera frame, at its real height, with the grid.

Size on screen:
    width  = the full width of the panel (4096 camera pixels are squeezed in)
    height = frame lines x V-stretch
             e.g. 16 lines x 3 = 48 screen pixels
                  64 lines x 3 = 192 screen pixels
    So when you change the frame height on the camera, the picture on
    screen changes height the same way. (If it would not fit, it is
    shrunk to the available space and the info line says so.)

Grid:
    the 64 channel boundaries of the camera's ChannelMap (63 channels, one per valve):
    equal slices between start_pixel and end_pixel, or calibrated on the real valves. Channel numbers are written
    under the picture. The first and last line (chute edges) are red.
"""

import numpy as np
from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QColor, QFont, QImage, QPainter, QPen
from PyQt5.QtWidgets import QSizePolicy, QWidget

NUMBER_STRIP_HEIGHT = 16                 # space under the picture for channel numbers
GRID_COLOUR = QColor(255, 230, 0, 170)   # yellow, a bit transparent
EDGE_COLOUR = QColor(255, 60, 60)        # red
TEXT_COLOUR = QColor(200, 200, 200)


class CameraView(QWidget):

    def __init__(self):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(60)

        self.image = None                # QImage of the newest frame
        self.frame_width = 0             # camera pixels (e.g. 4096)
        self.frame_lines = 0             # camera lines (e.g. 16)
        self.v_stretch = 3               # screen pixels per camera line
        self.grid_positions = []         # x in CAMERA pixels (channel boundaries, core.ChannelMap)
        self.show_grid = True
        self.fits = True                 # False = picture had to be shrunk

    # ---------------------------------------------------------------- input
    def set_frame(self, frame):
        """frame: numpy array (lines, width, 3), BGR order."""
        self.frame_lines, self.frame_width = frame.shape[:2]

        # Speed-up: we can never show more columns than the widget is wide,
        # so keep only every 2nd/3rd column when the frame is much wider.
        step = max(1, self.frame_width // max(1, self.width()))
        if step > 1:
            frame = np.ascontiguousarray(frame[:, ::step])

        lines, width = frame.shape[:2]
        self.image = QImage(frame.data, width, lines, frame.strides[0],
                            QImage.Format_BGR888).copy()   # own copy of the pixels
        self.update()                    # ask Qt to call paintEvent

    def set_grid(self, positions, show):
        self.grid_positions = positions
        self.show_grid = show
        self.update()

    def set_v_stretch(self, v_stretch):
        self.v_stretch = max(1, int(v_stretch))
        self.update()

    # ---------------------------------------------------------------- paint
    def _picture_rect(self):
        """Where the frame is drawn inside the widget."""
        wanted_h = self.frame_lines * self.v_stretch
        free_h = max(1, self.height() - NUMBER_STRIP_HEIGHT)
        height = min(wanted_h, free_h)
        self.fits = wanted_h <= free_h
        top = (free_h - height) / 2      # centred vertically
        return QRectF(0, top, self.width(), height)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.black)

        if self.image is None:
            painter.setPen(TEXT_COLOUR)
            painter.drawText(self.rect(), Qt.AlignCenter, "No picture yet")
            return

        rect = self._picture_rect()
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawImage(rect, self.image)

        if self.show_grid and self.grid_positions and self.frame_width:
            self._draw_grid(painter, rect)

    def _draw_grid(self, painter, rect):
        scale = rect.width() / self.frame_width          # camera pixel -> screen pixel
        xs = [rect.left() + x * scale for x in self.grid_positions]
        last = len(xs) - 1

        # Lines
        for i, x in enumerate(xs):
            is_edge = i in (0, last)
            painter.setPen(QPen(EDGE_COLOUR if is_edge else GRID_COLOUR, 2 if is_edge else 1))
            painter.drawLine(int(x), int(rect.top()), int(x), int(rect.bottom()))

        # Channel numbers (1, 2, 3 ...) in the middle of each channel, under the picture.
        if last < 1:
            return
        channel_w = xs[1] - xs[0]
        every = 1 if channel_w >= 18 else (5 if channel_w * 5 >= 18 else 10)
        painter.setPen(TEXT_COLOUR)
        painter.setFont(QFont("Arial", 7))
        text_top = rect.bottom() + 2
        for ch in range(1, last + 1):
            if ch == 1 or ch % every == 0:
                middle = (xs[ch - 1] + xs[ch]) / 2
                painter.drawText(QRectF(middle - 15, text_top, 30, NUMBER_STRIP_HEIGHT - 2),
                                 Qt.AlignHCenter | Qt.AlignTop, str(ch))
