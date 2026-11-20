"""
ui/snap_view.py - "Object snap": the picture of the object in the selected log row.

    +-- Object snap --------------------------+
    | Object 12 - row 31 - channel 17 - BOTH  |
    | FRONT  saw it, pixels 1073 - 1127        |
    |  [ enlarged picture, green marks ]       |
    | REAR   saw it, pixels 1101 - 1163        |
    |  [ enlarged picture ]                    |
    | [Save picture]                            |
    +------------------------------------------+

The picture is cut from the camera frames of that moment (core/frame_history.py):
the object's pixels + 30 pixels left/right, its lines + 8 lines before/after.
Top = oldest line, bottom = newest line. Enlarged so you can see it; each camera
line is drawn 3x higher (like V-stretch 3 in the live view).
Green marks = where the program saw the object (first ... last pixel).
A camera that did NOT see the object shows the picture at the other camera's place,
so you can see WHY it missed it (too small, too light, out of the picture ...).
"""

import os

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from config import SNAPSHOT_DIR

MAX_WIDTH = 320          # screen pixels
MAX_HEIGHT = 260
LINE_STRETCH = 3         # each camera line 3x higher (like the live view)


def to_qimage(image):
    lines, width = image.shape[:2]
    return QImage(image.data, width, lines, image.strides[0], QImage.Format_BGR888).copy()


class SnapView(QWidget):

    def __init__(self):
        super().__init__()
        self.event = None
        self.title = QLabel("<b>Object snap</b><br>Click a row to see the object.")
        self.title.setWordWrap(True)
        self.captions, self.pictures = {}, {}
        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        for cam in ("FRONT", "REAR"):
            caption, picture = QLabel(), QLabel()
            picture.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            picture.setMinimumHeight(60)
            picture.setStyleSheet("background:#202020")
            self.captions[cam], self.pictures[cam] = caption, picture
            layout.addWidget(caption)
            layout.addWidget(picture)
        self.save_button = QPushButton("Save picture")
        self.save_button.setToolTip("Save this object's pictures (real size) as PNG in the snapshots folder")
        self.save_button.clicked.connect(self.on_save)
        self.save_button.setEnabled(False)
        layout.addWidget(self.save_button)
        layout.addStretch()
        self.setMinimumWidth(MAX_WIDTH + 30)

    def show_event(self, e):
        self.event = e
        if e is None:
            return
        self.title.setText(f"<b>Object {e.object_no}</b> &nbsp; row {e.seq} &nbsp; channel {e.channel} "
                           f"&nbsp; seen by {e.seen_by} &nbsp; {e.result}")
        snaps = e.snaps or {}
        for cam in ("FRONT", "REAR"):
            snap = snaps.get(cam)
            if snap is None:
                self.captions[cam].setText(f"<b>{cam}</b>: no picture "
                                           "(row too old, or snaps switched off)")
                self.pictures[cam].clear()
                continue
            how = (f"saw it, pixels {snap.px_from} - {snap.px_to}" if snap.seen else
                   f"<span style='color:#c0392b'>did NOT see it</span> (picture at the other camera's "
                   f"pixels {snap.px_from} - {snap.px_to})")
            self.captions[cam].setText(f"<b>{cam}</b> {how}")
            self.pictures[cam].setPixmap(self._picture(snap))
        self.save_button.setEnabled(bool(snaps))

    @staticmethod
    def _picture(snap):
        lines, width = snap.image.shape[:2]
        zoom = max(1.0, min(6.0, MAX_WIDTH / max(1, width)))
        height_zoom = min(zoom * LINE_STRETCH, MAX_HEIGHT / max(1, lines))
        w, h = max(1, int(width * zoom)), max(1, int(lines * height_zoom))
        pixmap = QPixmap.fromImage(to_qimage(snap.image)).scaled(w, h, Qt.IgnoreAspectRatio,
                                                                 Qt.FastTransformation)
        # green marks: first and last pixel where the program saw the object
        painter = QPainter(pixmap)
        painter.setPen(QPen(QColor(0, 230, 0) if snap.seen else QColor(255, 160, 0), 2))
        for px in (snap.px_from, snap.px_to + 1):
            x = int((px - snap.x0) * zoom)
            painter.drawLine(x, 0, x, 7)
            painter.drawLine(x, h - 8, x, h)
        painter.end()
        return pixmap

    def on_save(self):
        e = self.event
        if e is None or not e.snaps:
            return
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        saved = []
        for cam, snap in e.snaps.items():
            path = os.path.join(SNAPSHOT_DIR, f"object_{e.object_no}_row{e.seq}_ch{e.channel}_{cam}.png")
            to_qimage(snap.image).save(path)
            saved.append(path)
        QMessageBox.information(self, "Save picture", "Saved:\n" + "\n".join(saved))


def save_snaps(events, folder):
    """Save the pictures of these rows as PNG files. Returns how many files were written."""
    count = 0
    for e in events:
        for cam, snap in (e.snaps or {}).items():
            os.makedirs(folder, exist_ok=True)
            name = f"obj{e.object_no:04d}_row{e.seq}_ch{e.channel}_{cam}{'' if snap.seen else '_notseen'}.png"
            if to_qimage(snap.image).save(os.path.join(folder, name)):
                count += 1
    return count
