"""Static pixels and independently published button effects."""

import json
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QWidget

folder = Path(sys.argv[1])
app = QApplication([])
window = QWidget()
window.setWindowTitle("Image Exposure Probe")
window.resize(640, 420)
window.move(180, 170)
button = QPushButton("Record click", window)
button.setGeometry(20, 20, 500, 220)
button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
button.setStyleSheet(
    "QPushButton,QPushButton:hover,QPushButton:pressed {background:#dceafe;border:2px solid #234;color:#123;}"
)
other = QLineEdit("untouched", window)
other.setGeometry(20, 280, 500, 50)
other.setAccessibleName("Unrelated field")
other.setFocusPolicy(Qt.FocusPolicy.NoFocus)
clicks = 0


def clicked():
    global clicks
    clicks += 1


button.clicked.connect(clicked)


def save():
    command = folder / "command.txt"
    if command.exists() and command.read_text() == "close":
        app.quit()
        return
    temporary = folder / "state.tmp"
    try:
        temporary.write_text(json.dumps(dict(clicks=clicks, other=other.text())), encoding="utf-8")
        temporary.replace(folder / "state.json")
    except OSError:
        pass


timer = QTimer()
timer.timeout.connect(save)
timer.start(40)
window.show()
app.exec()
