"""Opt-in disposable Qt mouse/click oracle; never injects input itself.

Run with ``uv run --with PySide6-Essentials python QtClickProbe.py DIR ROLE``.
The acceptance controller may place ``activate`` or ``close`` in command.txt.
Activation is limited to this fixture's own process and window.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from pathlib import Path

from PySide6 import __version__
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QCursor, QMouseEvent
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QWidget

folder, role = Path(sys.argv[1]), sys.argv[2]
folder.mkdir(parents=True, exist_ok=True)
title = f"Qt Click {role} {folder.parent.name}"
events: list[dict] = []
counts = {"target": 0, "sentinel": 0}

# Read-only native oracles distinguish Qt's synthetic active state from the
# WindowServer foreground and verify that global mouse buttons are released.
ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
objc.objc_getClass.argtypes = [ctypes.c_char_p]
objc.objc_getClass.restype = ctypes.c_void_p
objc.sel_registerName.argtypes = [ctypes.c_char_p]
objc.sel_registerName.restype = ctypes.c_void_p
send_object = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
send_int = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
send_bool = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
send_bool_argument = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool)(("objc_msgSend", objc))


def selector(name):
    return objc.sel_registerName(name.encode())


workspace = send_object(objc.objc_getClass(b"NSWorkspace"), selector("sharedWorkspace"))
cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
cg.CGEventSourceButtonState.argtypes = [ctypes.c_int32, ctypes.c_uint32]
cg.CGEventSourceButtonState.restype = ctypes.c_bool


def native_state() -> dict:
    front = send_object(workspace, selector("frontmostApplication"))
    nsapp = send_object(objc.objc_getClass(b"NSApplication"), selector("sharedApplication"))
    return {
        "foregroundPid": send_int(front, selector("processIdentifier")) if front else None,
        "nativeActive": bool(send_bool(nsapp, selector("isActive"))),
        "globalButtons": {
            name: bool(cg.CGEventSourceButtonState(0, value))
            for name, value in (("left", 0), ("right", 1), ("middle", 2))
        },
    }


def record(kind: str, **values) -> None:
    event = {"timeNs": time.time_ns(), "kind": kind, **native_state(), **values}
    events.append(event)
    with (folder / "events.jsonl").open("a") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")


class ProbeApplication(QApplication):
    def notify(self, receiver, event):
        if isinstance(event, QMouseEvent) and event.type() in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
            QEvent.Type.MouseMove,
        ):
            record(
                "qtNotify",
                event=event.type().name,
                receiver=receiver.objectName(),
                local=[event.position().x(), event.position().y()],
                globalPosition=[event.globalPosition().x(), event.globalPosition().y()],
                button=event.button().name,
                buttons=event.buttons().value,
                source=event.source().name,
                spontaneous=event.spontaneous(),
            )
        return super().notify(receiver, event)


class ProbeButton(QPushButton):
    def mousePressEvent(self, event):
        record("mousePressEvent", button=self.objectName(), local=[event.position().x(), event.position().y()])
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        record("mouseReleaseEvent", button=self.objectName(), local=[event.position().x(), event.position().y()])
        super().mouseReleaseEvent(event)


app = ProbeApplication([])
app.setApplicationName(title)
app.setApplicationDisplayName(title)
window = QWidget()
window.setObjectName("probe-window")
window.setWindowTitle(title)
window.resize(600, 400)
window.move(80 if role == "target" else 760, 130 if role == "target" else 140)
heading = QLabel(f"{role.upper()} — Qt actual callback probe", window)
heading.setGeometry(30, 20, 540, 35)
label = QLabel("Target clicks: 0 | Sentinel clicks: 0", window)
label.setGeometry(30, 270, 540, 30)
editor = QLineEdit("untouched", window)
editor.setObjectName("unrelated-editor")
editor.setReadOnly(True)
editor.setFocusPolicy(Qt.FocusPolicy.NoFocus)
editor.setGeometry(30, 325, 540, 35)


def clicked(name: str) -> None:
    counts[name] += 1
    record("clicked", button=name, count=counts[name])
    label.setText(f"Target clicks: {counts['target']} | Sentinel clicks: {counts['sentinel']}")


buttons = {}
for name, text, y in (("target", "CLICK TARGET", 85), ("sentinel", "UNRELATED SENTINEL", 185)):
    button = ProbeButton(text, window)
    button.setObjectName(name)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setAccessibleName(text)
    button.setGeometry(80, y, 440, 65)
    button.clicked.connect(lambda checked=False, name=name: clicked(name))
    buttons[name] = button


def snapshot() -> dict:
    frame = window.frameGeometry()
    native_view = ctypes.c_void_p(int(window.winId()))
    native_window = send_object(native_view, selector("window"))
    return {
        "pid": os.getpid(),
        "title": title,
        "role": role,
        "qtVersion": __version__,
        "nativeWindowID": send_int(native_window, selector("windowNumber")),
        "globalPointer": [QCursor.pos().x(), QCursor.pos().y()],
        "counts": dict(counts),
        "unrelatedText": editor.text(),
        "eventCount": len(events),
        "qtActive": window.isActiveWindow(),
        "qtApplicationState": app.applicationState().name,
        "qtMouseButtons": app.mouseButtons().value,
        "windowFrame": [frame.x(), frame.y(), frame.width(), frame.height()],
        "buttons": {
            name: {
                "globalCenter": [
                    button.mapToGlobal(button.rect().center()).x(),
                    button.mapToGlobal(button.rect().center()).y(),
                ],
                "down": button.isDown(),
            }
            for name, button in buttons.items()
        },
        **native_state(),
    }


def save() -> None:
    command = folder / "command.txt"
    if command.exists():
        value = command.read_text().strip()
        command.unlink()
        record("fixtureCommand", command=value)
        if value == "close":
            app.quit()
            return
        if value == "activate":
            nsapp = send_object(objc.objc_getClass(b"NSApplication"), selector("sharedApplication"))
            send_bool_argument(nsapp, selector("activateIgnoringOtherApps:"), True)
            window.raise_()
            window.activateWindow()
    temporary = folder / "state.tmp"
    temporary.write_text(json.dumps(snapshot(), ensure_ascii=False, indent=2))
    temporary.replace(folder / "state.json")


(folder / "fixture.pid").write_text(str(os.getpid()))
timer = QTimer()
timer.timeout.connect(save)
timer.start(50)
window.show()
record("ready")
app.exec()
