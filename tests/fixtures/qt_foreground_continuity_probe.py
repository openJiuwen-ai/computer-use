# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Stock Qt date/combo form; observations never initialize combo.view()."""

import argparse
import os
from pathlib import Path

from qt_cocoa_date_probe import Probe, QtCore, QtWidgets, write_json


class ContinuityProbe(Probe):
    def __init__(self, output, title):
        super().__init__(output, title)
        self.resize(570, 400)
        self.move(80, 350)
        self.topic = QtWidgets.QLineEdit("Initial title", self)
        self.topic.setAccessibleName("Meeting title")
        self.layout().insertWidget(0, self.topic)
        self.hour = QtWidgets.QComboBox(self)
        self.hour.setAccessibleName("Meeting hour")
        self.hour.setObjectName("meetingHour")
        self.hour.addItems([f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)])
        self.hour.setCurrentText("08:00")
        self.duration = QtWidgets.QComboBox(self)
        self.duration.setAccessibleName("Meeting duration")
        self.duration.setObjectName("meetingDuration")
        self.duration.addItems(["30 minutes", "60 minutes", "90 minutes"])
        self.layout().addWidget(self.hour)
        self.layout().addWidget(self.duration)
        self.hour_activations, self.duration_activations = [], []
        self.hour.textActivated.connect(self.hour_activations.append)
        self.duration.textActivated.connect(self.duration_activations.append)

    def object_label(self, obj):
        for key in ("topic", "hour", "duration"):
            if obj is not None and obj is getattr(self, key, None):
                return key
        return super().object_label(obj)

    def capture_state(self):
        popup = QtWidgets.QApplication.activePopupWidget()
        return {
            "pid": os.getpid(),
            "title": self.title,
            "platform": "cocoa",
            "qt_version": QtCore.qVersion(),
            "visible": self.isVisible(),
            "active": self.isActiveWindow(),
            "failure": self.failure,
            "topic": self.topic.text(),
            "date": self.value(),
            "date_model": self.model,
            "date_changes": self.date_changes,
            "submissions": self.submissions,
            "hour": self.hour.currentText(),
            "hour_activations": self.hour_activations,
            "duration": self.duration.currentText(),
            "duration_activations": self.duration_activations,
            "guard": self.guard.text(),
            "popup": self.object_label(popup),
            "focus_state": self.focus_state(),
            "event_count": self.event_count,
            "events": self.events,
        }

    def publish(self):
        write_json(self.output / "state.json", self.capture_state())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--title", required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QtWidgets.QApplication([])
    assert app.platformName() == "cocoa"
    app.setApplicationName(args.title)
    probe = ContinuityProbe(args.output, args.title)
    app.installEventFilter(probe)
    probe.show()
    probe.publish()
    app.exec()


if __name__ == "__main__":
    main()
