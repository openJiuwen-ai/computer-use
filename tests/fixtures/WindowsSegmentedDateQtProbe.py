"""Disposable date/time fields with independently published native values."""

import json
import sys
from pathlib import Path

from PySide6 import __version__
from PySide6.QtCore import QDate, QDateTime, QTime, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDateEdit,
    QDateTimeEdit,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QTimeEdit,
    QWidget,
)


class CompositeTime(QComboBox):
    def __init__(self, name, ambiguous=False):
        super().__init__()
        self.setAccessibleName(name)
        self.addItem("")
        self.outer = QWidget(self)
        self.inner = QWidget(self.outer)
        self.fields = []
        for index in range(2 if ambiguous else 1):
            field_value_local = QTimeEdit(QTime(13, 20), self.inner)
            field_value_local.setDisplayFormat("HH:mm")
            field_value_local.setAccessibleName(name + " internal " + str(index))
            self.fields.append(field_value_local)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.outer.setGeometry(2, 1, max(1, self.width() - 28), max(1, self.height() - 2))
        self.inner.setGeometry(self.outer.rect())
        width = self.inner.width() // len(self.fields)
        for index_value, field_value in enumerate(self.fields):
            field_value.setGeometry(index_value * width, 0, width, self.inner.height())


folder = Path(sys.argv[1])
app = QApplication([])
if len(sys.argv) > 2:
    app.setKeyboardInputInterval(int(sys.argv[2]))
window = QWidget()
window.setWindowTitle("Segmented Date Probe")
window.resize(640, 560)
layout = QFormLayout(window)
editors = {}
for label, fmt in [
    ("ISO date", "yyyy/MM/dd"),
    ("Dash date", "yyyy-MM-dd"),
    ("Variable date", "yyyy.M.d"),
    ("Day-first date", "dd/MM/yyyy"),
]:
    field = QDateEdit(QDate(2026, 10, 14))
    field.setDisplayFormat(fmt)
    field.setAccessibleName(label)
    editors[label] = field
    layout.addRow(label, field)
for label, fmt in [("Time", "HH:mm"), ("Seconds", "HH:mm:ss")]:
    field = QTimeEdit(QTime(13, 20, 30))
    field.setDisplayFormat(fmt)
    field.setAccessibleName(label)
    editors[label] = field
    layout.addRow(label, field)
field = QDateTimeEdit(QDateTime(QDate(2026, 10, 14), QTime(13, 20, 30)))
field.setDisplayFormat("yyyy/MM/dd HH:mm:ss")
field.setAccessibleName("Date and time")
editors["Date and time"] = field
layout.addRow("Date and time", field)
field = QSpinBox()
field.setRange(0, 9999)
field.setValue(12)
field.setAccessibleName("Integer")
editors["Integer"] = field
layout.addRow("Integer", field)
field = QDoubleSpinBox()
field.setRange(0, 9999)
field.setValue(12.25)
field.setAccessibleName("Decimal")
editors["Decimal"] = field
layout.addRow("Decimal", field)
composite = CompositeTime("Composite time")
layout.addRow("Composite time", composite)
editors["Composite time"] = composite.fields[0]
ambiguous = CompositeTime("Ambiguous time", True)
layout.addRow("Ambiguous time", ambiguous)
editors["Ambiguous first"] = ambiguous.fields[0]
editors["Ambiguous second"] = ambiguous.fields[1]
otherContainer = QWidget()
otherLayout = QFormLayout(otherContainer)
other = QLineEdit("untouched")
other.setAccessibleName("Unrelated field")
otherLayout.addRow(other)
layout.addRow("Unrelated field", otherContainer)


def save():
    command = folder / "command.txt"
    if command.exists():
        value = command.read_text(encoding="utf-8")
        command.unlink()
        if value == "close":
            QTimer.singleShot(0, app.quit)
    values = {
        name: dict(
            text=field.text(),
            value=field.dateTime().toString("yyyy-MM-dd HH:mm:ss") if hasattr(field, "dateTime") else field.value(),
        )
        for name, field in editors.items()
    }
    focused = app.focusWidget()
    data = dict(
        values=values,
        unrelated=other.text(),
        qtVersion=__version__,
        focus=focused.accessibleName() if focused else None,
        focusClass=focused.metaObject().className() if focused else None,
        active=window.isActiveWindow(),
    )
    temporary = folder / "state.tmp"
    try:
        temporary.write_text(json.dumps(data), encoding="utf-8")
        temporary.replace(folder / "state.json")
    except OSError:
        pass  # Windows readers may hold a non-delete-sharing file handle.


timer = QTimer()
timer.timeout.connect(save)
timer.start(40)
window.show()
app.exec()
