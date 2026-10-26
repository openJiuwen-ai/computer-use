"""Ordinary Qt Quick controls and independent input-effect evidence.

Run inside an owned Linux X11 desktop. Requires PyQt6.QtQuickWidgets and Qt Quick
Controls. STATE and COMMAND are private paths supplied by the acceptance runner.
Commands arrange only disposable controls; they never synthesize user input.
The key observer returns False and leaves normal toolkit processing untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, QPoint, QPointF, QTimer, QUrl
from PyQt6.QtQuickWidgets import QQuickWidget
from PyQt6.QtWidgets import QApplication, QMainWindow

os.environ.setdefault("QT_LINUX_ACCESSIBILITY_ALWAYS_ON", "1")
os.environ.setdefault("QT_ACCESSIBILITY", "1")

TITLE = "Computer Use Numeric Receiver Fixture"
QML = b"""
import QtQuick
import QtQuick.Controls
Rectangle {
    id: fixtureRoot
    width: 800; height: 660; color: "#e7e7e7"
    property bool driftAfterEdit: false
    property int interactionValue: 0
    property int interactionChanges: 0
    property int propertyChanges: 0
    Column {
        x: 20; y: 20; spacing: 12
        Row {
            spacing: 20
            Label { text: "Editable numeric value"; width: 220 }
            SpinBox {
                objectName: "editableSpin"; Accessible.name: "Editable numeric value"
                from: 0; to: 1112; value: 0; editable: true
                onValueModified: {
                    fixtureRoot.interactionValue = value
                    fixtureRoot.interactionChanges += 1
                }
                onValueChanged: fixtureRoot.propertyChanges += 1
            }
        }
        Row {
            spacing: 20
            Label { text: "Read-only numeric value"; width: 220 }
            SpinBox {
                objectName: "readonlySpin"; Accessible.name: "Read-only numeric value"
                from: 0; to: 1112; value: 17; editable: false
            }
        }
        Row {
            spacing: 20
            Label { text: "Numeric slider"; width: 220 }
            Slider {
                objectName: "slider"; Accessible.name: "Numeric slider"
                from: 0; to: 1112; value: 23; width: 220
            }
        }
        Row {
            spacing: 20
            Label { text: "Formatted numeric value"; width: 220 }
            SpinBox {
                objectName: "formattedSpin"; Accessible.name: "Formatted numeric value"
                from: 0; to: 100000; value: 125; editable: true
                inputMethodHints: Qt.ImhFormattedNumbersOnly
                validator: DoubleValidator { bottom: 0; top: 1000; decimals: 2; notation: DoubleValidator.StandardNotation }
                textFromValue: function(value, locale) { return Number(value / 100).toFixed(2) }
                valueFromText: function(text, locale) { return Math.round(Number(text) * 100) }
            }
        }
        TextField {
            objectName: "normalText"; Accessible.name: "Normal text"
            text: "untouched"; width: 480
            onTextEdited: if (fixtureRoot.driftAfterEdit) {
                fixtureRoot.driftAfterEdit = false
                otherText.forceActiveFocus()
            }
        }
        TextField {
            id: otherText; objectName: "otherText"; Accessible.name: "Other text"
            text: "do not change"; width: 480
        }
        Label { text: "Independent provider controls" }
        ScrollView {
            width: 750; height: 330
            Column {
                width: 730
                Repeater {
                    model: 450
                    Label {
                        required property int index
                        text: "Provider row " + index; Accessible.name: text
                        width: 700; height: 20
                    }
                }
            }
        }
    }
}
"""


class Journal(QObject):
    def __init__(self):
        super().__init__()
        self.keys = []

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            self.keys.append(
                dict(
                    receiver=obj.objectName(),
                    objectClass=type(obj).__name__,
                    key=event.key(),
                    text=event.text(),
                    kind="press" if event.type() == QEvent.Type.KeyPress else "release",
                )
            )
        return False


def main(state_path: Path, command_path: Path):
    app = QApplication(sys.argv)
    app.setApplicationName(TITLE)
    journal = Journal()
    app.installEventFilter(journal)
    window = QMainWindow()
    window.setWindowTitle(TITLE)
    quick = QQuickWidget()
    quick.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
    qml_path = state_path.with_suffix(".qml")
    qml_path.write_bytes(QML)
    quick.setSource(QUrl.fromLocalFile(str(qml_path)))
    root = quick.rootObject()
    if root is None:
        raise RuntimeError("Qt Quick fixture failed to load; inspect stderr")
    window.setCentralWidget(quick)
    window.resize(820, 700)
    window.show()
    last = None

    def publish():
        nonlocal last
        if command_path.exists():
            command = json.loads(command_path.read_text())
            if command["id"] != last:
                obj = root.findChild(QObject, command["target"])
                if obj is None:
                    raise ValueError("Unknown owned fixture control: " + command["target"])
                for name in ("value", "text"):
                    if name in command:
                        obj.setProperty(name, command[name])
                if "driftAfterEdit" in command:
                    root.setProperty("driftAfterEdit", command["driftAfterEdit"])
                if command.get("reshow"):
                    window.hide()
                    QTimer.singleShot(100, window.show)
                if command.get("focus"):
                    obj.forceActiveFocus()
                    window.activateWindow()
                    window.raise_()
                if command.get("clearKeys"):
                    journal.keys.clear()
                last = command["id"]
        fields = {}
        for name in ("editableSpin", "readonlySpin", "slider", "formattedSpin", "normalText", "otherText"):
            obj = root.findChild(QObject, name)
            fields[name] = {
                key: obj.property(key) for key in ("value", "text", "activeFocus", "editable", "displayText")
            }
            point = obj.mapToScene(QPointF(0, 0))
            fields[name]["scenePosition"] = [point.x(), point.y()]
            editor = obj.property("contentItem")
            fields[name]["contentItem"] = (
                {
                    key: editor.property(key)
                    for key in ("text", "selectionStart", "selectionEnd", "cursorPosition", "activeFocus")
                }
                if isinstance(editor, QObject)
                else None
            )
        quick_origin = quick.mapToGlobal(QPoint(0, 0))
        data = dict(
            command=last,
            pid=os.getpid(),
            fields=fields,
            keys=journal.keys,
            quickGlobal=[quick_origin.x(), quick_origin.y()],
            interactionValue=root.property("interactionValue"),
            interactionChanges=root.property("interactionChanges"),
            propertyChanges=root.property("propertyChanges"),
        )
        temporary = state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False))
        temporary.replace(state_path)

    timer = QTimer()
    timer.timeout.connect(publish)
    timer.start(50)
    return app.exec()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path)
    parser.add_argument("command", type=Path)
    arguments = parser.parse_args()
    raise SystemExit(main(arguments.state.resolve(), arguments.command.resolve()))
