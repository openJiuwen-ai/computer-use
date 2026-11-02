# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Probe Qt toggle/click signals and saved state with an optional PySide6 interpreter.

Run this standalone fixture with an interpreter that provides PySide6-Essentials.
The widgets use Qt's offscreen platform and keep their business model in memory.
macOS AX delivery is verified separately against the installed native provider.
"""

import argparse
import json
import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6 import QtCore, QtWidgets


class SettingsForm(QtWidgets.QWidget):
    """Persist from clicked, while Qt owns the independently visible check state."""

    def __init__(self, saved=None):
        super().__init__()
        self.model = dict(saved or {"muted": False, "recording": False})
        self.events = []
        self.mute_on = QtWidgets.QRadioButton("Mute on", self)
        self.mute_off = QtWidgets.QRadioButton("Mute off", self)
        self.radio_group = QtWidgets.QButtonGroup(self)
        self.radio_group.addButton(self.mute_on)
        self.radio_group.addButton(self.mute_off)
        self.recording = QtWidgets.QCheckBox("Automatic recording", self)
        self.mute_on.setChecked(self.model["muted"])
        self.mute_off.setChecked(not self.model["muted"])
        self.recording.setChecked(self.model["recording"])
        self.mute_on.clicked.connect(lambda checked: self._clicked("muted", checked))
        self.mute_off.clicked.connect(lambda checked: self._clicked("muted", not checked))
        self.recording.clicked.connect(lambda checked: self._clicked("recording", checked))
        self.mute_on.toggled.connect(lambda checked: self.events.append(["muted", "toggled", checked]))
        self.recording.toggled.connect(lambda checked: self.events.append(["recording", "toggled", checked]))

    def _clicked(self, field, checked):
        self.events.append([field, "clicked", checked])
        self.model[field] = checked

    def save_and_reopen(self):
        return SettingsForm(json.loads(json.dumps(self.model)))


def activate(button, route):
    if route == "toggle":
        button.toggle()
    elif route == "click":
        button.click()
    else:
        loop = QtCore.QEventLoop()
        timer = QtCore.QTimer(loop)
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        returned = []

        def clicked(_checked):
            returned.append(True)
            loop.quit()

        button.clicked.connect(clicked)
        try:
            timer.start(5000)
            button.animateClick()
            loop.exec()
        finally:
            timer.stop()
            button.clicked.disconnect(clicked)
        assert returned, "animateClick must emit its real Qt clicked signal"


def run_case(field, route):
    form = SettingsForm()
    button = form.mute_on if field == "muted" else form.recording
    activate(button, route)
    reopened = form.save_and_reopen()
    reopened_button = reopened.mute_on if field == "muted" else reopened.recording
    result = {
        "control": "QRadioButton" if field == "muted" else "QCheckBox",
        "route": route,
        "checked": button.isChecked(),
        "business_model": form.model[field],
        "reopened_checked": reopened_button.isChecked(),
        "events": list(form.events),
    }
    assert result["checked"] is True
    activated = route != "toggle"
    assert result["business_model"] is activated
    assert result["reopened_checked"] is activated
    assert sum(event[1] == "clicked" for event in result["events"]) == int(activated)
    assert sum(event[1] == "toggled" for event in result["events"]) == 1
    form.close()
    reopened.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    app = QtWidgets.QApplication([])
    result = {
        "qt_version": QtCore.qVersion(),
        "platform": app.platformName(),
        "scope": "QAbstractButton signal and persistence mechanism",
        "cases": [
            run_case(field, route) for field in ("muted", "recording") for route in ("toggle", "click", "animateClick")
        ],
    }
    report = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    sys.stdout.write(report)
    app.quit()


if __name__ == "__main__":
    main()
