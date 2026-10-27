"""Read-only native facts for one disposable test process, without SDK caches."""

import json
import sys

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi as A
from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, qVersion

A.set_timeout(500, 500)
pid = int(sys.argv[1])
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 60
desktop = A.get_desktop(0)
app = None
for index in range(desktop.get_child_count()):
    item = desktop.get_child_at_index(index)
    if item is not None and item.get_process_id() == pid:
        app = item
        break
if app is None:
    raise RuntimeError("Fixture accessible application not found")
rows = []


def visit(obj, parent, depth):
    if obj is None or depth > 10 or len(rows) >= limit:
        return
    try:
        states = obj.get_state_set()
        row = dict(
            index=len(rows),
            parent=parent,
            depth=depth,
            name=obj.get_name(),
            role=obj.get_role_name(),
            interfaces=obj.get_interfaces(),
            children=obj.get_child_count(),
            states=[
                key
                for key in dir(A.StateType)
                if key.isupper()
                and isinstance(getattr(A.StateType, key), A.StateType)
                and states.contains(getattr(A.StateType, key))
            ],
        )
        if "Value" in row["interfaces"]:
            row["value"] = A.Value.get_current_value(obj)
        if "Text" in row["interfaces"]:
            row["text"] = A.Text.get_text(obj, 0, -1)
            row["caret"] = A.Text.get_caret_offset(obj)
        if "Component" in row["interfaces"]:
            row["coordinates"] = {}
            for name in ("SCREEN", "WINDOW", "PARENT"):
                rect = A.Component.get_extents(obj, getattr(A.CoordType, name))
                row["coordinates"][name] = [rect.x, rect.y, rect.width, rect.height]
            row["bounds"] = row["coordinates"]["SCREEN"]
        rows.append(row)
        for index_value in range(row["children"]):
            if len(rows) >= limit:
                break
            visit(obj.get_child_at_index(index_value), row["index"], depth + 1)
    except Exception as error:
        rows.append(dict(parent=parent, depth=depth, error=str(error)))


visit(app, None, 0)
print(
    json.dumps(
        dict(pid=pid, rows=rows, qtCompile=QT_VERSION_STR, qtRuntime=qVersion(), pyqt=PYQT_VERSION_STR),
        ensure_ascii=False,
        indent=2,
    )
)
