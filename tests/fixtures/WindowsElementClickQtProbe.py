"""Disposable Qt callbacks for Windows element-pointer acceptance."""

import ctypes
import json
import sys
from ctypes import wintypes
from pathlib import Path

from PySide6 import __version__
from PySide6.QtCore import QDate, QEvent, QObject, QPoint, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QLabel,
    QListWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

folder = Path(sys.argv[1])
paint_delay = int(sys.argv[2]) if len(sys.argv) > 2 else 0
app = QApplication([])
window = QWidget()
window.setWindowTitle("Element Click Qt Probe")
window.resize(620, 660)
window.move(180, 170)
layout = QVBoxLayout(window)
peer = QWidget()
peer.setWindowTitle("Element Click Qt Witness")
peer.resize(400, 180)
peer.move(1100, 170)
peer_layout = QVBoxLayout(peer)
peer_text = QTextEdit("untouched")
peer_text.setAccessibleName("Unrelated editor")
peer_layout.addWidget(peer_text)
nav = QCheckBox("Open content page")
nav.setAccessibleName("Open content page")
status = QLabel("page=0")
status.setStyleSheet("background-color:#dc2626")
combo = QComboBox()
combo.addItems(["30 minutes", "1 hour"])
combo.setAccessibleName("Duration")
duration = QLabel("duration=30 minutes")
items = QListWidget()
items.addItems(["Interview mail", "Other mail"])
items.setAccessibleName("Mail list")
opened = QLabel("opened=0")
date = QDateEdit(QDate(2026, 10, 14))
date.setDisplayFormat("yyyy/MM/dd")
date.setAccessibleName("Start date")
text = QTextEdit()
text.setAccessibleName("Reply editor")
text.setPlainText("")
for widget in (nav, status, combo, duration, items, opened, date, text):
    layout.addWidget(widget)
state = dict(clicked=0, activated=0, opened=0)
date_events = []


def date_state():
    editor = date.lineEdit()
    return dict(
        text=editor.text(),
        selected=editor.selectedText(),
        selectionStart=editor.selectionStart(),
        section=date.currentSectionIndex(),
    )


class DateEvents(QObject):
    def eventFilter(self, obj, event):
        if obj in (date, date.lineEdit()) and event.type() == QEvent.Type.KeyPress:
            item = dict(key=event.key(), typed=event.text(), before=date_state())
            date_events.append(item)
            QTimer.singleShot(0, lambda: item.update(after=date_state()))
        return False


date_filter = DateEvents()
app.installEventFilter(date_filter)


def on_click():
    state["clicked"] += 1
    status.setText("page=" + str(state["clicked"]))
    color = "background-color:" + ("#16a34a" if state["clicked"] % 2 else "#dc2626")
    # Optional queued renderer: the accessible state is updated before pixels.
    if paint_delay:
        QTimer.singleShot(paint_delay, lambda: status.setStyleSheet(color))
    else:
        status.setStyleSheet(color)


def on_activate(value):
    state["activated"] += 1
    duration.setText("duration=" + combo.currentText())


def on_open(item):
    state["opened"] += 1
    opened.setText("opened=" + str(state["opened"]))


nav.clicked.connect(on_click)
combo.activated.connect(on_activate)
items.itemDoubleClicked.connect(on_open)


def save():
    command = folder / "command.txt"
    if command.exists():
        content = command.read_text()
        command.unlink()
        if content == "reset":
            nav.setChecked(False)
            combo.setCurrentIndex(0)
            items.clearSelection()
            text.clear()
            date.setDate(QDate(2026, 10, 14))
            date_events.clear()
            state.update(clicked=0, activated=0, opened=0)
            status.setText("page=0")
            duration.setText("duration=30 minutes")
            opened.setText("opened=0")
            status.setStyleSheet("background-color:#dc2626")
        if content == "close":
            app.quit()
            return
        if content == "minimize":
            window.showMinimized()
            peer.activateWindow()
        if content == "close-target":
            window.close()
            peer.activateWindow()
        if content == "cover":
            peer.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            peer.resize(window.size() + QSize(80, 80))
            peer.move(window.pos() - QPoint(40, 40))
            peer.show()
            peer.activateWindow()
        if content == "uncover":
            peer.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
            peer.resize(400, 180)
            peer.move(1100, 170)
            peer.show()
    logical = status.mapTo(window, QPoint(status.width() - 8, status.height() // 2))
    sample = wintypes.POINT(
        round(logical.x() * window.devicePixelRatioF()), round(logical.y() * window.devicePixelRatioF())
    )
    ctypes.windll.user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    ctypes.windll.user32.ClientToScreen(int(window.winId()), ctypes.byref(sample))
    data = {
        **state,
        "paintPoint": [sample.x, sample.y],
        "checked": nav.isChecked(),
        "duration": combo.currentText(),
        "date": date.text(),
        "reply": text.toPlainText(),
        "unrelated": peer_text.toPlainText(),
        "qtVersion": __version__,
        "minimized": window.isMinimized(),
        "active": window.isActiveWindow(),
        "handle": int(window.winId()),
        "nativeForeground": int(ctypes.windll.user32.GetForegroundWindow()),
        "focusWidget": app.focusWidget().accessibleName() if app.focusWidget() else None,
        "dateEditor": date_state(),
        "dateEvents": date_events[-60:],
    }
    temp = folder / "state.tmp"
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temp.replace(folder / "state.json")
    except OSError:
        pass  # A Windows reader can temporarily deny replacement.


timer = QTimer()
timer.timeout.connect(save)
timer.start(60)
peer.show()
window.show()
app.exec()
