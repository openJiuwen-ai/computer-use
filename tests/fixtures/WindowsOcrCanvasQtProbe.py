"""Painted text has no widget accessibility label; clicks publish real effects."""

import json
import sys
from pathlib import Path

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QWidget

folder = Path(sys.argv[1])
app = QApplication([])


class Canvas(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Visual Text Canvas Probe")
        self.resize(2400, 880)
        self.move(40, 100)
        self.clicks = 0
        self.wrong = 0
        self.ack = 0
        self.button = QRect(300, 240, 640, 120)
        self.popup = None

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("white"))
        p.fillRect(self.button, QColor("#cde3ff"))
        p.setPen(QColor("#162c4f"))
        p.setFont(QFont("Segoe UI", 30))
        p.drawText(self.button, Qt.AlignmentFlag.AlignCenter, "Schedule meeting")
        p.drawText(QRect(300, 440, 900, 90), Qt.AlignmentFlag.AlignLeft, "Unrelated content unchanged")

    def mousePressEvent(self, event):
        if self.button.contains(event.position().toPoint()):
            self.clicks += 1
            self.popup = QDialog(self)
            self.popup.setWindowTitle("Canvas confirmation")
            self.popup.resize(380, 180)
            button = QPushButton("Acknowledge", self.popup)
            button.setAccessibleName("Acknowledge")
            button.setGeometry(40, 40, 290, 80)

            def confirmed():
                self.ack += 1
                self.popup.close()

            button.clicked.connect(confirmed)
            self.popup.show()
        else:
            self.wrong += 1


window = Canvas()


def save():
    command = folder / "command.txt"
    if command.exists() and command.read_text() == "close":
        app.quit()
        return
    temporary = folder / "state.tmp"
    try:
        temporary.write_text(
            json.dumps(
                dict(clicks=window.clicks, wrongClicks=window.wrong, acknowledged=window.ack, other="unchanged")
            ),
            encoding="utf-8",
        )
        temporary.replace(folder / "state.json")
    except OSError:
        pass


timer = QTimer()
timer.timeout.connect(save)
timer.start(40)
window.show()
app.exec()
