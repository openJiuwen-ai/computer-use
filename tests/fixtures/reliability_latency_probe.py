# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Disposable Qt launcher, new form, native combos and an offscreen checkbox."""

import argparse
import json
import time
from pathlib import Path

from mouse_button_counter import MouseButtonCounters
from qt_foreground_continuity_probe import ContinuityProbe, QtCore, QtWidgets, write_json


class LatencyProbe(ContinuityProbe):
    def __init__(self, output, title):
        super().__init__(output, title)
        self.opened = 0
        self.cloud_events = []
        self.inline_clicks = 0
        self.callbacks = []
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
        self.cloud = QtWidgets.QCheckBox("Cloud recording", contents)
        self.cloud.setAccessibleName("Cloud recording")
        # clicked also exposes a zero-argument overload. A built-in list.append
        # slot is inferred without its bool parameter by PySide 6.8, poisoning
        # later meta-calls after the resulting exception. Bind the bool signal
        # explicitly and use a Python callable with a concrete argument.
        self.cloud.clicked[bool].connect(lambda checked: self.cloud_events.append(checked))
        self.cloud.clicked[bool].connect(lambda checked: self.record("cloud", checked))
        layout.addWidget(self.cloud)
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
        return handled

    @staticmethod
    def frame(widget):
        rect = widget.frameGeometry()
        return [rect.x(), rect.y(), rect.width(), rect.height()]

    @staticmethod
    def relative_point(widget, window):
        point = widget.mapToGlobal(widget.rect().center()) - window.frameGeometry().topLeft()
        return [point.x(), point.y()]

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
        super().publish()
        if hasattr(self, "launcher"):
            value = json.loads((self.output / "state.json").read_text())
            value.update(
                opened=self.opened,
                launcher_visible=self.launcher.isVisible(),
                cloud=self.cloud.isChecked(),
                cloud_events=self.cloud_events,
                cloud_y=self.cloud.mapToGlobal(QtCore.QPoint()).y(),
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
            write_json(self.output / "state.json", value)


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
