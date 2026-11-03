# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Live Cocoa fixture reusing the Qt clicked/model mechanism of the offscreen probe.

Requires an optional PySide6 interpreter. All data stays in the supplied temporary
folder. No programmatic widget activation is used to test SDK input.
"""

import argparse
import json
import os
from pathlib import Path

import PySide6
from qt_toggle_activation_probe import QtCore, QtWidgets, SettingsForm

# The shared widget module selects offscreen for its standalone tests. Choose
# Cocoa before QApplication is constructed; startup below rejects any substitute.
os.environ["QT_QPA_PLATFORM"] = "cocoa"


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class RecordingOption(QtWidgets.QCheckBox):
    """Ordinary Qt checkbox behavior with an application-defined class name."""


class MuteChoice(QtWidgets.QRadioButton):
    """Ordinary Qt radio behavior with an application-defined class name."""


class Probe(QtWidgets.QWidget):
    def __init__(self, output, title, variant="standard"):
        super().__init__()
        self.output, self.title = output, title
        self.variant = variant
        self.generation = self.save_clicks = 0
        self.saved, self.before_reopen, self.failure = None, None, None
        self.setWindowTitle(title)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.resize(580, 360)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("Cocoa native input: clicked must update the saved model."))
        self.forms_layout = QtWidgets.QHBoxLayout()
        layout.addLayout(self.forms_layout)
        self.forms = {}
        self.install_forms()
        self.guard = QtWidgets.QLineEdit("unchanged")
        self.guard.setAccessibleName("Unrelated guard field")
        layout.addWidget(self.guard)
        self.save = QtWidgets.QPushButton("Save and reopen")
        self.save.setAccessibleName("Save and reopen")
        self.save.clicked.connect(self.save_and_reopen)
        layout.addWidget(self.save)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.publish)
        self.timer.start(50)

    def install_forms(self, saved=None):
        for form in self.forms.values():
            self.forms_layout.removeWidget(form)
            form.deleteLater()
        self.forms = {}
        for route, label in (("normal", "Normal click"), ("explicit", "Explicit action")):
            custom_classes = self.variant in ("custom-classes", "custom-classes-names")
            form = SettingsForm(
                (saved or {}).get(route),
                checkbox_type=RecordingOption if custom_classes else QtWidgets.QCheckBox,
                radio_type=MuteChoice if custom_classes else QtWidgets.QRadioButton,
            )
            if self.variant in ("custom-names", "custom-classes-names"):
                # Real application object names replace class names in Qt's
                # generated AX identifiers; labels and behavior stay unchanged.
                form.recording.setObjectName("recording_option")
                form.mute_on.setObjectName("mute_on_option")
                form.mute_off.setObjectName("mute_off_option")
            column = QtWidgets.QVBoxLayout(form)
            column.addWidget(QtWidgets.QLabel(label))
            for control, text in (
                (form.recording, label + " checkbox"),
                (form.mute_on, label + " radio on"),
                (form.mute_off, label + " radio off"),
            ):
                control.setText(text)
                control.setAccessibleName(text)
                column.addWidget(control)
            self.forms_layout.addWidget(form)
            self.forms[route] = form
        self.generation += 1

    def values(self):
        return {
            route: {
                "checked": {"recording": form.recording.isChecked(), "muted": form.mute_on.isChecked()},
                "model": dict(form.model),
                "control_identity": {
                    name: {"class": control.metaObject().className(), "object_name": control.objectName()}
                    for name, control in (
                        ("recording", form.recording), ("mute_on", form.mute_on), ("mute_off", form.mute_off)
                    )
                },
                "events": list(form.events),
                "counts": {
                    field: {
                        kind: sum(event[:2] == [field, kind] for event in form.events)
                        for kind in ("toggled", "clicked")
                    }
                    for field in ("recording", "muted")
                },
            }
            for route, form in self.forms.items()
        }

    def save_and_reopen(self):
        self.save_clicks += 1
        self.before_reopen = self.values()
        try:
            write_json(
                self.output / "saved-settings.json", {route: dict(form.model) for route, form in self.forms.items()}
            )
            self.saved = json.loads((self.output / "saved-settings.json").read_text(encoding="utf-8"))
            self.install_forms(self.saved)
        except Exception as error:
            self.failure = f"{type(error).__name__}: {error}"
        self.publish()

    def publish(self):
        app = QtWidgets.QApplication.instance()
        write_json(
            self.output / "state.json",
            {
                "pid": os.getpid(),
                "title": self.title,
                "qt_version": QtCore.qVersion(),
                "pyside_version": PySide6.__version__,
                "platform": app.platformName(),
                "variant": self.variant,
                "generation": self.generation,
                "active": self.isActiveWindow(),
                "visible": self.isVisible(),
                "forms": self.values(),
                "saved": self.saved,
                "before_reopen": self.before_reopen,
                "save_clicks": self.save_clicks,
                "guard": self.guard.text(),
                "failure": self.failure,
            },
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--title", required=True)
    parser.add_argument(
        "--variant", default="standard",
        choices=("standard", "custom-classes", "custom-names", "custom-classes-names"),
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    QtCore.QCoreApplication.setApplicationName(args.title)
    app = QtWidgets.QApplication([])
    if app.platformName() != "cocoa":
        raise RuntimeError(f"Expected actual Cocoa accessibility provider, got {app.platformName()}")
    probe = Probe(args.output, args.title, args.variant)
    probe.show()
    probe.publish()
    app.exec()


if __name__ == "__main__":
    main()
