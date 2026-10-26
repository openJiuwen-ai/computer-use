"""Owned Qt Quick composite editor and independent keyboard-effect evidence.

The SpinBox content item is a layout containing a text editor and unit label,
as in ordinary styled controls. AX focus and actual editor focus are recorded
separately. An application-wide Select All shortcut optionally reconstructs the
disposable control, exposing wrong-recipient input without business app names.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtQuickWidgets import QQuickWidget
from PyQt6.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit, QMainWindow, QScrollArea, QVBoxLayout, QWidget

os.environ.setdefault("QT_LINUX_ACCESSIBILITY_ALWAYS_ON", "1")
os.environ.setdefault("QT_ACCESSIBILITY", "1")

TITLE = "Computer Use Compound Input Fixture"
QML = b"""
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
Rectangle {
    id: fixtureRoot
    objectName: "fixtureRoot"
    width: 800; height: 620; color: "#e7e7e7"
    property int globalSelections: 0
    property int generations: 0
    property int commits: 0
    property int committedValue: -1
    property bool rebuildOnGlobalShortcut: false
    property bool driftAfterNativeEdit: false
    property bool rebuildOnEdit: false
    property string retiredText: ""
    Column {
        x: 20; y: 20; spacing: 20
        Label { text: "A styled numeric control with a composite content item" }
        Loader {
            id: compoundLoader; objectName: "compoundLoader"
            sourceComponent: Component {
                SpinBox {
                    id: compoundSpin
                    objectName: "compoundSpin"
                    Accessible.name: "Compound numeric value"
                    from: 0; to: 1112; value: 0; editable: true
                    width: 360; height: 48
                    Component.onCompleted: fixtureRoot.generations++
                    contentItem: RowLayout {
                        objectName: "compoundContent"
                        TextInput {
                            id: compoundEditor; objectName: "compoundEditor"
                            text: compoundSpin.displayText
                            Layout.fillWidth: true
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                            selectByMouse: true
                            validator: compoundSpin.validator
                            inputMethodHints: Qt.ImhDigitsOnly
                            onTextEdited: if (fixtureRoot.rebuildOnEdit) {
                                fixtureRoot.rebuildOnEdit = false
                                fixtureRoot.retiredText = text
                                compoundLoader.active = false
                                Qt.callLater(function() {
                                    compoundLoader.active = true
                                    quickOtherText.forceActiveFocus()
                                })
                            }
                            onAccepted: {
                                compoundSpin.value = compoundSpin.valueFromText(text, compoundSpin.locale)
                                fixtureRoot.committedValue = compoundSpin.value
                                fixtureRoot.commits++
                            }
                        }
                        Label { text: "units" }
                    }
                }
            }
        }
        TextField {
            id: quickOtherText
            objectName: "quickOther"; Accessible.name: "Other Quick input"
            text: "quick untouched"; width: 480
        }
        Label { text: "The following independent labels add ordinary AX content." }
        ScrollView {
            width: 740; height: 300
            Column {
                Repeater {
                    model: 450
                    Label {
                        required property int index
                        text: "Independent row " + index; width: 700; height: 20
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
        self.events = []

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            data = dict(receiver=obj.objectName(), objectClass=type(obj).__name__, event=event.type().name)
            if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                data.update(key=event.key(), text=event.text())
            self.events.append(data)
        return False


def main(state_path: Path, command_path: Path, leading_native_labels=0, closed_popup=False):
    app = QApplication(sys.argv)
    app.setApplicationName(TITLE)
    journal = Journal()
    app.installEventFilter(journal)
    window = QMainWindow()
    window.setObjectName("fixtureWindow")
    window.setWindowTitle(TITLE)
    holder = QWidget()
    layout = QVBoxLayout(holder)
    if leading_native_labels:
        scroll = QScrollArea()
        scroll.setMaximumHeight(80)
        rows = QWidget()
        row_layout = QVBoxLayout(rows)
        for index in range(leading_native_labels):
            row_layout.addWidget(QLabel(f"Native row {index}"))
        scroll.setWidget(rows)
        layout.addWidget(scroll)
    combo = None
    if closed_popup:
        combo = QComboBox()
        combo.setObjectName("ordinaryCombo")
        combo.setAccessibleName("Ordinary option")
        combo.addItems(["Low", "Normal", "High"])
        combo.setCurrentIndex(1)
        layout.addWidget(combo)
    other = QLineEdit("widget untouched")
    other.setObjectName("widgetOther")
    other.setAccessibleName("Other native input")
    layout.addWidget(other)
    native_returns = 0

    def native_return():
        nonlocal native_returns
        native_returns += 1

    other.returnPressed.connect(native_return)
    quick = QQuickWidget()
    quick.setObjectName("quickHost")
    quick.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
    qml_path = state_path.with_suffix(".qml")
    qml_path.write_bytes(QML)
    quick.setSource(QUrl.fromLocalFile(str(qml_path)))
    root = quick.rootObject()
    if root is None:
        raise RuntimeError("Qt Quick fixture failed to load; inspect stderr")
    select_all = QAction("Global select all", window)
    select_all.setShortcut(QKeySequence.StandardKey.SelectAll)
    select_all.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
    window.addAction(select_all)

    def global_selection():
        root.setProperty("globalSelections", root.property("globalSelections") + 1)
        if root.property("rebuildOnGlobalShortcut"):
            loader = root.findChild(QObject, "compoundLoader")
            loader.setProperty("active", False)
            QTimer.singleShot(0, lambda: loader.setProperty("active", True))

    select_all.triggered.connect(global_selection)

    def native_edited():
        if root.property("driftAfterNativeEdit"):
            root.setProperty("driftAfterNativeEdit", False)
            quick.setFocus()
            root.findChild(QObject, "quickOther").forceActiveFocus()

    other.textEdited.connect(native_edited)
    layout.addWidget(quick)
    window.setCentralWidget(holder)
    window.resize(820, 730)
    window.show()
    last = None

    def description(obj):
        return None if obj is None else dict(name=obj.objectName(), objectClass=obj.metaObject().className())

    def publish():
        nonlocal last
        if command_path.exists():
            command = json.loads(command_path.read_text())
            if command["id"] != last:
                obj = (
                    other
                    if command.get("target") == "widgetOther"
                    else root.findChild(QObject, command.get("target", "compoundSpin"))
                )
                if obj is None:
                    raise ValueError("Unknown owned fixture control: " + str(command.get("target")))
                for name in ("value", "text"):
                    if name in command:
                        obj.setProperty(name, command[name])
                if "rebuildOnGlobalShortcut" in command:
                    root.setProperty("rebuildOnGlobalShortcut", command["rebuildOnGlobalShortcut"])
                if "driftAfterNativeEdit" in command:
                    root.setProperty("driftAfterNativeEdit", command["driftAfterNativeEdit"])
                if "rebuildOnEdit" in command:
                    root.setProperty("rebuildOnEdit", command["rebuildOnEdit"])
                if command.get("rebuild"):
                    loader = root.findChild(QObject, "compoundLoader")
                    loader.setProperty("active", False)
                    QTimer.singleShot(0, lambda: loader.setProperty("active", True))
                if "moveWindow" in command:
                    window.move(*command["moveWindow"])
                if command.get("reshow"):
                    window.hide()
                    QTimer.singleShot(100, window.show)
                if command.get("cyclePopup"):
                    if combo is None:
                        raise ValueError("The optional ordinary combo was not enabled")
                    combo.showPopup()
                    QTimer.singleShot(100, combo.hidePopup)
                if command.get("focus"):
                    if obj is other:
                        other.setFocus()
                    else:
                        quick.setFocus()
                        obj.forceActiveFocus()
                    window.activateWindow()
                    window.raise_()
                if command.get("clearEvents"):
                    journal.events.clear()
                last = command["id"]
        fields = {}
        for name in ("compoundSpin", "compoundContent", "compoundEditor", "quickOther"):
            obj = root.findChild(QObject, name)
            fields[name] = (
                None
                if obj is None
                else {
                    key: obj.property(key)
                    for key in (
                        "value",
                        "text",
                        "activeFocus",
                        "focus",
                        "editable",
                        "displayText",
                        "selectionStart",
                        "selectionEnd",
                        "cursorPosition",
                        "x",
                        "y",
                        "width",
                        "height",
                    )
                }
            )
            if obj is not None:
                point = obj.mapToScene(QPointF(0, 0))
                fields[name]["scenePosition"] = [point.x(), point.y()]
        fields["widgetOther"] = dict(text=other.text(), hasFocus=other.hasFocus())
        if combo is not None:
            fields["ordinaryCombo"] = dict(
                text=combo.currentText(), hasFocus=combo.hasFocus(), popupVisible=combo.view().isVisible()
            )
        quick_origin = quick.mapToGlobal(QPoint(0, 0))
        frame = window.frameGeometry()
        data = dict(
            command=last,
            pid=os.getpid(),
            fields=fields,
            quickGlobal=[quick_origin.x(), quick_origin.y()],
            windowFrame=[frame.x(), frame.y(), frame.width(), frame.height()],
            activeFocusItem=description(quick.quickWindow().activeFocusItem()),
            nativeFocusWidget=description(app.focusWidget()),
            nativeReturns=native_returns,
            globalSelections=root.property("globalSelections"),
            generations=root.property("generations"),
            commits=root.property("commits"),
            retiredText=root.property("retiredText"),
            committedValue=root.property("committedValue"),
            events=journal.events,
        )
        temporary = state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False))
        temporary.replace(state_path)

    timer = QTimer()
    timer.timeout.connect(publish)
    timer.start(25)
    return app.exec()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path)
    parser.add_argument("command", type=Path)
    parser.add_argument("--leading-native-labels", type=int, default=0)
    parser.add_argument(
        "--closed-popup",
        action="store_true",
        help="Include an ordinary combo for native showPopup/hidePopup focus diagnostics",
    )
    arguments = parser.parse_args()
    assert 0 <= arguments.leading_native_labels <= 600
    raise SystemExit(
        main(
            arguments.state.resolve(),
            arguments.command.resolve(),
            arguments.leading_native_labels,
            arguments.closed_popup,
        )
    )
