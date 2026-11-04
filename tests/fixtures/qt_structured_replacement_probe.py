# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Probe Qt date replacement using an optional PySide6-Essentials interpreter.

The offscreen widgets exercise Qt's actual validator and internal section state.
macOS process-addressed input and AX range setters are verified separately.
"""

import argparse
import json
import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6 import QtCore, QtGui, QtWidgets


def key_text(editor, text):
    for kind in (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease):
        event = QtGui.QKeyEvent(kind, QtCore.Qt.Key_2, QtCore.Qt.NoModifier, text)
        QtWidgets.QApplication.sendEvent(editor, event)
    QtWidgets.QApplication.processEvents()


def replace(editor, requested, recipe):
    editor.selectAll()
    if recipe == "forward_atomic":
        # Qt's Cocoa AX range setter forwards start/end to this same positive
        # selection, while QAbstractSpinBox.selectAll() uses a negative length.
        editor.lineEdit().setSelection(0, len(editor.text()))
    selected = editor.lineEdit().selectedText()
    section = str(editor.currentSection())
    parts = list(requested) if recipe == "reverse_scalar" else [requested]
    for part in parts:
        key_text(editor, part)
    return {
        "requested": requested,
        "selected": selected,
        "section_before_body": section,
        "text": editor.text(),
        "value": editor.date().toString("yyyy/M/d"),
    }


def run_case(section, requested, recipe):
    editor = QtWidgets.QDateEdit()
    editor.setDisplayFormat("yyyy/M/d")
    editor.setMinimumDate(QtCore.QDate(2026, 11, 9))
    editor.setMaximumDate(QtCore.QDate(2027, 11, 30))
    editor.setDate(QtCore.QDate(2026, 11, 15))
    editor.show()
    editor.setFocus()
    QtWidgets.QApplication.processEvents()
    editor.setCurrentSection(section)
    result = replace(editor, requested, recipe)
    result.update({"initial_section": str(section), "recipe": recipe})
    assert result["selected"] == "2026/11/15"
    target = QtCore.QDate.fromString(requested, "yyyy/M/d")
    if recipe == "forward_atomic":
        assert result["value"] == requested
        # A later replacement must work after the previous edit moved the
        # section/caret and parser cache. Verify both increasing and decreasing.
        repeated = [replace(editor, value, recipe) for value in ("2026/11/23", "2026/11/13")]
        assert all(item["value"] == item["requested"] for item in repeated)
        result["repeated"] = repeated
    elif recipe == "reverse_scalar":
        assert result["value"] == "2026/11/15"
    elif section == QtWidgets.QDateTimeEdit.DaySection and target.day() < 15:
        assert result["value"] == QtCore.QDate(target.year(), target.month(), 15).toString("yyyy/M/d")
    else:
        assert result["value"] == requested
    editor.close()
    QtWidgets.QApplication.processEvents()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    app = QtWidgets.QApplication([])
    sections = (
        QtWidgets.QDateTimeEdit.YearSection,
        QtWidgets.QDateTimeEdit.MonthSection,
        QtWidgets.QDateTimeEdit.DaySection,
    )
    cases = [
        run_case(section, requested, recipe)
        for section in sections
        for requested in ("2026/11/13", "2026/11/23", "2026/12/1")
        for recipe in ("reverse_atomic", "forward_atomic")
    ]
    cases.extend(run_case(section, "2026/11/13", "reverse_scalar") for section in sections)
    result = {
        "qt_version": QtCore.qVersion(),
        "platform": app.platformName(),
        "scope": "QDateEdit selection direction, parser state and complete text delivery",
        "cases": cases,
    }
    report = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    sys.stdout.write(report)
    app.quit()


if __name__ == "__main__":
    main()
