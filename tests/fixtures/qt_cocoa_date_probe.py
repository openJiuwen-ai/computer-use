# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Visible QDateEdit with independent edit, Return and business-state evidence.

Use an optional PySide6 interpreter. Configuration files only seed a case; all
measured editing and Return input must arrive through the native Cocoa provider.

Qt contract references:
https://doc.qt.io/qt-6.8/qabstractspinbox.html#editingFinished
https://doc.qt.io/qt-6.8/qdatetimeedit.html#keyPressEvent
https://github.com/qt/qtbase/blob/v6.8.0/src/plugins/platforms/cocoa/qcocoawindow.mm
https://github.com/qt/qtbase/blob/v6.8.0/src/plugins/platforms/cocoa/qnsview.mm
"""

import argparse
import ctypes
import json
import os
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "cocoa"

import PySide6
from PySide6 import QtCore, QtWidgets

SECTIONS = {
    "year": QtWidgets.QDateTimeEdit.YearSection,
    "month": QtWidgets.QDateTimeEdit.MonthSection,
    "day": QtWidgets.QDateTimeEdit.DaySection,
}
OBSERVED_EVENTS = {
    QtCore.QEvent.FocusIn,
    QtCore.QEvent.FocusOut,
    QtCore.QEvent.WindowActivate,
    QtCore.QEvent.WindowDeactivate,
    QtCore.QEvent.MouseButtonPress,
    QtCore.QEvent.MouseButtonRelease,
    QtCore.QEvent.KeyPress,
    QtCore.QEvent.KeyRelease,
    QtCore.QEvent.Show,
    QtCore.QEvent.Hide,
}


class CocoaState:
    """Read existing AppKit objects; never activate or create an application."""

    def __init__(self):
        self.objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        self.appkit = ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
        self.objc.sel_registerName.argtypes = [ctypes.c_char_p]
        self.objc.sel_registerName.restype = ctypes.c_void_p
        self.objc.object_getClassName.argtypes = [ctypes.c_void_p]
        self.objc.object_getClassName.restype = ctypes.c_char_p
        self.message = ctypes.cast(self.objc.objc_msgSend, ctypes.c_void_p).value

    def get(self, object_pointer, selector, result=ctypes.c_void_p):
        call = ctypes.CFUNCTYPE(result, ctypes.c_void_p, ctypes.c_void_p)(self.message)
        return call(object_pointer, self.objc.sel_registerName(selector.encode()))

    def describe(self, pointer):
        if not pointer:
            return None
        name = self.objc.object_getClassName(pointer)
        return {"pointer": hex(pointer), "class": name.decode() if name else None}

    def read(self, view):
        # QCocoaWindow::winId() is its existing QNSView. The fixture calls this
        # only after show(), so it does not manufacture a native window.
        window = self.get(view, "window")
        application = ctypes.c_void_p.in_dll(self.appkit, "NSApp").value
        responder = self.get(window, "firstResponder") if window else None
        key = self.get(application, "keyWindow") if application else None
        main = self.get(application, "mainWindow") if application else None
        return {
            "view": self.describe(view),
            "window": self.describe(window),
            "application_active": self.get(application, "isActive", ctypes.c_bool) if application else None,
            "key_window": self.describe(key),
            "main_window": self.describe(main),
            "key_window_is_target": bool(window and key == window),
            "main_window_is_target": bool(window and main == window),
            "window_is_key": self.get(window, "isKeyWindow", ctypes.c_bool) if window else None,
            "first_responder": self.describe(responder),
            "first_responder_is_view": bool(view and responder == view),
        }


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class DateField(QtWidgets.QDateEdit):
    def __init__(self, parent):
        super().__init__(parent)
        self.on_return = None

    def keyPressEvent(self, event):
        # Qt 6.8 editingFinished also fires on lost focus; it is not evidence of
        # Return. Observe the normal virtual handler, then delegate unchanged.
        is_return = event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter)
        super().keyPressEvent(event)
        if is_return and self.on_return is not None:
            self.on_return()


class Probe(QtWidgets.QWidget):
    def __init__(self, output, title, normal_show=False):
        super().__init__()
        self.output, self.title = output, title
        self.last_command = None
        self.failure = None
        self.normal_show = normal_show
        self.events, self.event_count = [], 0
        self.cocoa = None
        self.cocoa_error = None
        try:
            self.cocoa = CocoaState()
        except Exception as error:
            self.cocoa_error = f"{type(error).__name__}: {error}"
        self.model = "2026/11/15"
        self.date_changes, self.finishes, self.submissions = [], [], []
        self.setWindowTitle(title)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, not normal_show)
        self.resize(570, 220)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("Meeting date: replace the whole field, then explicitly press Return."))
        self.date = DateField(self)
        self.date.setAccessibleName("Meeting date")
        self.date.setDisplayFormat("yyyy/M/d")
        self.date.setMinimumDate(QtCore.QDate(2026, 11, 9))
        self.date.setMaximumDate(QtCore.QDate(2027, 11, 30))
        self.date.setDate(QtCore.QDate(2026, 11, 15))
        self.date.setCurrentSection(SECTIONS["year"])
        self.date.dateChanged.connect(self.changed)
        self.date.editingFinished.connect(lambda: self.finishes.append(self.value()))
        self.date.on_return = self.submitted
        layout.addWidget(self.date)
        self.guard = QtWidgets.QLineEdit("unchanged")
        self.guard.setAccessibleName("Unrelated guard field")
        layout.addWidget(self.guard)
        self.summary = QtWidgets.QLabel("No submitted date")
        self.summary.setAccessibleName("Submission result")
        layout.addWidget(self.summary)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(30)
        for widget in (self, self.date, self.date.lineEdit(), self.guard):
            widget.installEventFilter(self)

    def object_label(self, obj):
        if obj is None:
            return None
        for candidate, label in (
            (self, "root"), (self.date, "date"), (self.date.lineEdit(), "date.lineEdit"),
            (self.guard, "guard"), (self.windowHandle(), "root.windowHandle"),
        ):
            if obj is candidate:
                return label
        return f"{obj.metaObject().className()}:{obj.objectName()}"

    def eventFilter(self, watched, event):
        if event.type() in OBSERVED_EVENTS:
            self.event_count += 1
            entry = {
                "sequence": self.event_count, "monotonic": time.monotonic(),
                "object": self.object_label(watched), "event": event.type().name,
            }
            if event.type() in (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease):
                entry.update(key=event.key(), text=event.text())
            self.events.append(entry)
            del self.events[:-256]
        return super().eventFilter(watched, event)

    def focus_state(self):
        app = QtWidgets.QApplication.instance()
        handle = self.windowHandle()
        native = {"error": self.cocoa_error} if self.cocoa_error else None
        if self.cocoa and self.isVisible() and handle:
            try:
                native = self.cocoa.read(int(handle.winId()))
            except Exception as error:
                native = {"error": f"{type(error).__name__}: {error}"}
        return {
            "active_window": self.object_label(app.activeWindow()),
            "focus_widget": self.object_label(app.focusWidget()),
            "focus_window": self.object_label(app.focusWindow()),
            "focus_object": self.object_label(app.focusObject()),
            "window_handle_active": handle.isActive() if handle else None,
            "date_has_focus": self.date.hasFocus(),
            "line_edit_has_focus": self.date.lineEdit().hasFocus(),
            "line_edit_focus_proxy": self.object_label(self.date.lineEdit().focusProxy()),
            "cocoa": native,
        }

    def text_geometry(self):
        """Independent fixture oracle: query the real editor, not its stepper AX frame."""
        editor = self.date.lineEdit()

        def global_rect(local):
            origin = editor.mapToGlobal(local.topLeft())
            return [origin.x(), origin.y(), local.width(), local.height()]

        content = editor.contentsRect()
        margins = editor.textMargins()
        text = content.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        point = editor.mapToGlobal(text.center()) if not text.isEmpty() else None
        screen = editor.screen()
        screen_rect = screen.geometry() if screen else None
        return {
            "coordinate_space": "qt_global_logical_desktop",
            "axes": "desktop logical points; top-left origin, x right, y down; no DPR multiplication",
            "point": [point.x(), point.y()] if point else None,
            "point_basis": "center of lineEdit contentsRect after textMargins; excludes spinbox arrow buttons",
            "text_rect": global_rect(text),
            "line_edit_rect": global_rect(editor.rect()),
            "contents_rect": global_rect(content),
            "text_margins": [margins.left(), margins.top(), margins.right(), margins.bottom()],
            "visible": editor.isVisible(),
            "screen": {
                "name": screen.name() if screen else None,
                "geometry": [screen_rect.x(), screen_rect.y(), screen_rect.width(), screen_rect.height()]
                if screen_rect else None,
                "device_pixel_ratio": screen.devicePixelRatio() if screen else None,
            },
        }

    def value(self):
        return self.date.date().toString("yyyy/M/d")

    def changed(self, date):
        self.model = date.toString("yyyy/M/d")
        self.date_changes.append(self.model)

    def submitted(self):
        value = self.value()
        self.submissions.append(value)
        self.summary.setText("Submitted: " + value)

    def configure(self, command):
        if command["kind"] != "configure" or command["section"] not in SECTIONS:
            raise ValueError("Unknown fixture configuration")
        initial = QtCore.QDate.fromString(command["value"], "yyyy/M/d")
        if not initial.isValid() or not self.date.minimumDate() <= initial <= self.date.maximumDate():
            raise ValueError("Initial date is outside the fixture range")
        # Seed only; do not focus, click, select all or send the input under test.
        blocker = QtCore.QSignalBlocker(self.date)
        self.date.setDate(initial)
        self.date.setCurrentSection(SECTIONS[command["section"]])
        del blocker
        self.model = self.value()
        self.date_changes, self.finishes, self.submissions = [], [], []
        self.summary.setText("No submitted date")
        self.last_command = command["id"]

    def tick(self):
        try:
            path = self.output / "command.json"
            if path.exists():
                command = json.loads(path.read_text(encoding="utf-8"))
                if command["id"] != self.last_command:
                    self.configure(command)
        except Exception as error:
            self.failure = f"{type(error).__name__}: {error}"
        self.publish()

    def publish(self):
        write_json(
            self.output / "state.json",
            {
                "pid": os.getpid(),
                "title": self.title,
                "qt_version": QtCore.qVersion(),
                "pyside_version": PySide6.__version__,
                "platform": QtWidgets.QApplication.instance().platformName(),
                "normal_show": self.normal_show,
                "active": self.isActiveWindow(),
                "visible": self.isVisible(),
                "last_command": self.last_command,
                "failure": self.failure,
                "value": self.value(),
                "text": self.date.text(),
                "model": self.model,
                "section": next(
                    (name for name, section in SECTIONS.items() if section == self.date.currentSection()), "none"
                ),
                "selected_text": self.date.lineEdit().selectedText(),
                "date_changes": self.date_changes,
                "editing_finished": self.finishes,
                "submissions": self.submissions,
                "guard": self.guard.text(),
                "focus_state": self.focus_state(),
                "text_geometry": self.text_geometry(),
                "event_count": self.event_count,
                "events": self.events,
            },
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument(
        "--normal-show", action="store_true", help="Diagnostic control: show without the no-activation attribute"
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    QtCore.QCoreApplication.setApplicationName(args.title)
    app = QtWidgets.QApplication([])
    if app.platformName() != "cocoa":
        raise RuntimeError(f"Native acceptance requires Cocoa, got {app.platformName()}")
    probe = Probe(args.output, args.title, normal_show=args.normal_show)
    probe.show()
    probe.windowHandle().installEventFilter(probe)
    probe.publish()
    app.exec()


if __name__ == "__main__":
    main()
