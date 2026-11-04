# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Disposable Qt launcher, new form, combos, and offscreen checkbox/radio."""

import argparse
import ctypes
import os
import time
from pathlib import Path

from mouse_button_counter import MouseButtonCounters
from qt_foreground_continuity_probe import ContinuityProbe, QtCore, QtWidgets, write_json


class LatencyProbe(ContinuityProbe):
    def __init__(self, output, title):
        super().__init__(output, title)
        self.opened = 0
        self.cloud_events = []
        self.mute_events = []
        self.inline_clicks = 0
        self.callbacks = []
        self.native_oracle = os.environ.get("CU_POPUP_NATIVE_ORACLE") == "1"
        self.native_creation_events = []
        self.native_window_changes = []
        self.mouse_counters = MouseButtonCounters()
        self.mouse_counters.sample("initial")
        self.inline = QtWidgets.QPushButton("Coordinate activation", self)
        self.inline.setAccessibleName("Coordinate activation")
        self.inline.clicked.connect(self.activate_inline)
        self.layout().insertWidget(1, self.inline)
        self.repeat = QtWidgets.QComboBox(self)
        self.repeat.setAccessibleName("Meeting repeat")
        self.repeat.addItems(["Never", "Every week", "Every weekday"])
        self.repeat_activations = []
        self.repeat.textActivated.connect(self.repeat_activations.append)
        self.layout().addWidget(self.repeat)
        self.topic.textChanged.connect(lambda value: self.record("title", value))
        self.date.dateChanged.connect(lambda _value: self.record("date", self.value()))
        self.hour.textActivated.connect(lambda value: self.record("hour", value))
        self.duration.textActivated.connect(lambda value: self.record("duration", value))
        self.repeat.textActivated.connect(lambda value: self.record("repeat", value))
        self.scroll = QtWidgets.QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        contents = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(contents)
        filler = QtWidgets.QLabel("Scroll to the recording options")
        filler.setFixedHeight(600)
        layout.addWidget(filler)
        options = QtWidgets.QWidget(contents)
        options_layout = QtWidgets.QHBoxLayout(options)
        self.cloud = QtWidgets.QCheckBox("Cloud recording", options)
        self.cloud.setAccessibleName("Cloud recording")
        # clicked also exposes a zero-argument overload. A built-in list.append
        # slot is inferred without its bool parameter by PySide 6.8, poisoning
        # later meta-calls after the resulting exception. Bind the bool signal
        # explicitly and use a Python callable with a concrete argument.
        self.cloud.clicked[bool].connect(lambda checked: self.cloud_events.append(checked))
        self.cloud.clicked[bool].connect(lambda checked: self.record("cloud", checked))
        options_layout.addWidget(self.cloud)
        self.microphone_live = QtWidgets.QRadioButton("Microphone enabled", options)
        self.microphone_live.setAccessibleName("Microphone enabled")
        self.mute = QtWidgets.QRadioButton("Mute on entry", options)
        self.mute.setAccessibleName("Mute on entry")
        self.microphone_group = QtWidgets.QButtonGroup(options)
        self.microphone_group.addButton(self.microphone_live)
        self.microphone_group.addButton(self.mute)
        self.microphone_live.setChecked(True)
        self.mute.clicked[bool].connect(lambda checked: self.mute_events.append(checked))
        self.mute.clicked[bool].connect(lambda checked: self.record("mute", checked))
        options_layout.addWidget(self.microphone_live)
        options_layout.addWidget(self.mute)
        layout.addWidget(options)
        self.scroll.setWidget(contents)
        self.layout().addWidget(self.scroll)
        self.resize(570, 500)
        self.launcher = QtWidgets.QWidget()
        self.launcher.setWindowTitle(title + " Launcher")
        open_button = QtWidgets.QPushButton("Open schedule")
        open_button.setAccessibleName("Open schedule")
        open_button.clicked.connect(self.open_form)
        top = QtWidgets.QVBoxLayout(self.launcher)
        top.addWidget(open_button)
        self.launcher.resize(300, 100)

    def open_form(self):
        self.opened += 1
        self.record("opened", self.opened)
        self.show()
        self.launcher.hide()
        self.publish()

    def record(self, kind, value):
        self.callbacks.append({"sequence": len(self.callbacks) + 1, "monotonic": time.monotonic(),
                               "kind": kind, "value": value})

    def activate_inline(self):
        self.inline_clicks += 1
        self.record("inline", self.inline_clicks)

    def eventFilter(self, watched, event):
        handled = super().eventFilter(watched, event)
        # Keep raw key text out of the diagnostic event journal. Business-field
        # values remain the fixture's independent explicit operation oracle.
        if self.events:
            self.events[-1].pop("text", None)
        if hasattr(self, "mouse_counters") and event.type() in (QtCore.QEvent.Show, QtCore.QEvent.Hide):
            self.mouse_counters.sample("qt:" + event.type().name)
        if getattr(self, "native_oracle", False) and event.type() in (
            QtCore.QEvent.WinIdChange, QtCore.QEvent.PlatformSurface,
        ):
            entry = {"monotonic": time.monotonic(), "event": event.type().name,
                     "object": self.object_label(watched),
                     "qt_application_state": QtWidgets.QApplication.applicationState().name}
            if isinstance(watched, QtWidgets.QWidget):
                # Never call QWidget.winId(), QWindow.winId() or combo.view():
                # those could manufacture the native object being measured.
                entry.update(native_created=watched.testAttribute(QtCore.Qt.WA_WState_Created),
                             is_window=watched.isWindow(), has_window_handle=watched.windowHandle() is not None)
            if hasattr(event, "surfaceEventType"):
                entry["surface_event"] = event.surfaceEventType().name
            self.native_creation_events.append(entry)
        return handled

    def existing_cocoa_windows(self):
        """Read NSApp's existing objects; never request a native window number."""
        if self.cocoa is None:
            return []
        app = ctypes.c_void_p.in_dll(self.cocoa.appkit, "NSApp").value
        windows = self.cocoa.get(app, "windows") if app else None
        if not windows:
            return []
        count = self.cocoa.get(windows, "count", ctypes.c_size_t)
        at = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t)(self.cocoa.message)
        selector = self.cocoa.objc.sel_registerName(b"objectAtIndex:")
        result = []
        for index in range(min(count, 256)):
            window = at(windows, selector, index)
            result.append({"object": self.cocoa.describe(window),
                           "visible": self.cocoa.get(window, "isVisible", ctypes.c_bool),
                           "content_view": self.cocoa.describe(self.cocoa.get(window, "contentView"))})
        return result

    @staticmethod
    def frame(widget):
        rect = widget.frameGeometry()
        return [rect.x(), rect.y(), rect.width(), rect.height()]

    @staticmethod
    def relative_point(widget, window):
        point = widget.mapToGlobal(widget.rect().center()) - window.frameGeometry().topLeft()
        return [point.x(), point.y()]

    def control_geometry(self, widget):
        """Observe existing widget/viewport geometry without scrolling or focusing."""
        top_left = widget.mapToGlobal(QtCore.QPoint())
        bottom_right = widget.mapToGlobal(widget.rect().bottomRight())
        viewport = self.scroll.viewport()
        return {
            "frame": [top_left.x(), top_left.y(), widget.width(), widget.height()],
            "fully_in_viewport": viewport.rect().contains(viewport.mapFromGlobal(top_left))
            and viewport.rect().contains(viewport.mapFromGlobal(bottom_right)),
        }

    def popup_geometry(self):
        """Inspect an already open popup; never initialize QComboBox.view()."""
        popup = QtWidgets.QApplication.activePopupWidget()
        if popup is None:
            return None
        choices = {}
        for view in popup.findChildren(QtWidgets.QAbstractItemView):
            model = view.model()
            matches = model.match(model.index(0, 0), QtCore.Qt.DisplayRole, "10:15", 1, QtCore.Qt.MatchExactly)
            for index in matches:
                rect = view.visualRect(index)
                point = view.viewport().mapToGlobal(rect.center()) - popup.frameGeometry().topLeft()
                choices[index.data()] = {
                    "point": [point.x(), point.y()],
                    "visible": view.viewport().rect().contains(rect.center()) and not rect.isEmpty(),
                }
        return {"frame": self.frame(popup), "choices": choices}

    def publish(self):
        if hasattr(self, "mouse_counters"):
            self.mouse_counters.sample()
        if hasattr(self, "launcher"):
            # Publish one complete independent oracle atomically. Publishing
            # the parent first exposed a valid but incomplete JSON object to
            # the concurrent reader between the two file replacements.
            value = super().capture_state()
            value.update(
                opened=self.opened,
                launcher_visible=self.launcher.isVisible(),
                cloud=self.cloud.isChecked(),
                cloud_events=self.cloud_events,
                cloud_y=self.cloud.mapToGlobal(QtCore.QPoint()).y(),
                cloud_height=self.cloud.height(),
                mute=self.mute.isChecked(),
                mute_events=self.mute_events,
                microphone_live=self.microphone_live.isChecked(),
                mute_y=self.mute.mapToGlobal(QtCore.QPoint()).y(),
                mute_height=self.mute.height(),
                offscreen_controls={name: self.control_geometry(widget) for name, widget in (
                    ("cloud", self.cloud), ("mute", self.mute),
                )},
                viewport_y=self.scroll.viewport().mapToGlobal(QtCore.QPoint()).y(),
                viewport_height=self.scroll.viewport().height(),
                scroll_value=self.scroll.verticalScrollBar().value(),
                inline_clicks=self.inline_clicks,
                form_frame=self.frame(self),
                inline_point=self.relative_point(self.inline, self),
                popup_geometry=self.popup_geometry(),
                repeat=self.repeat.currentText(),
                repeat_activations=self.repeat_activations,
                application_state=QtWidgets.QApplication.applicationState().name,
                callbacks=self.callbacks,
                mouse_button_counter_changes=self.mouse_counters.changes,
            )
            if self.native_oracle:
                windows = self.existing_cocoa_windows()
                if not self.native_window_changes or self.native_window_changes[-1]["windows"] != windows:
                    self.native_window_changes.append({"monotonic": time.monotonic(), "windows": windows,
                        "qt_application_state": QtWidgets.QApplication.applicationState().name})
                value.update(
                    native_oracle_sampled_at=time.monotonic(), native_creation_events=self.native_creation_events,
                    cocoa_native_window_changes=self.native_window_changes,
                )
            write_json(self.output / "state.json", value)
        else:
            super().publish()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--title", required=True)
    options = parser.parse_args()
    options.output.mkdir(parents=True, exist_ok=True)
    app = QtWidgets.QApplication([])
    app.setApplicationName(options.title)
    probe = LatencyProbe(options.output, options.title)
    app.installEventFilter(probe)
    probe.launcher.show()
    probe.publish()
    app.exec()


if __name__ == "__main__":
    main()
