"""Owned GTK table whose ATK Table cells are absent from ordinary Children.

This is a native provider fixture, not a driver hook. Two coordinates of the
second row share a real label to model a merged cell; the two windows remain
separate. Read-only acceptance must leave values, focus and input counts alone.
"""

import json
import os
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Atk", "1.0")
from gi.repository import Atk, Gdk, GLib, Gtk

output = Path(sys.argv[2])
ROWS, COLS = int(sys.argv[3]), 3
GLib.set_prgname("computer-use-v5-table-probe")
GLib.set_application_name("Computer Use v5 Table Probe")
state = dict(pid=os.getpid(), toolkit="table-only", inputs=0, ready=False, windows={})


def save():
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False))
    temporary.replace(output)
    return True


def event(current, _):
    if current.type in (
        Gdk.EventType.KEY_PRESS,
        Gdk.EventType.KEY_RELEASE,
        Gdk.EventType.BUTTON_PRESS,
        Gdk.EventType.BUTTON_RELEASE,
    ):
        state["inputs"] += 1
        save()
    Gtk.main_do_event(current)


class TableAccessible(Atk.Object, Atk.Table):
    __gtype_name__ = "ComputerUseV5TableOnlyAccessible"

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.set_role(Atk.Role.TABLE)
        self.set_name("Data table " + owner.key)

    def do_get_parent(self):
        parent = self.owner.get_parent()
        return parent.get_accessible() if parent is not None else None

    def do_get_n_children(self):
        return 0

    def do_get_n_rows(self):
        return ROWS

    def do_get_n_columns(self):
        return COLS

    def do_ref_at(self, row, column):
        if not (0 <= row < ROWS and 0 <= column < COLS):
            return None
        cell = self.owner.cells[row][column].get_accessible()
        cell.set_parent(self)
        return cell

    def do_ref_state_set(self):
        states = Atk.StateSet()
        for state_value in (
            Atk.StateType.ENABLED,
            Atk.StateType.SENSITIVE,
            Atk.StateType.VISIBLE,
            Atk.StateType.SHOWING,
        ):
            states.add_state(state_value)
        return states


class Table(Gtk.Grid):
    __gtype_name__ = "ComputerUseV5TableOnlyGrid"

    def __init__(self, key):
        self.key, self.cells, self.native = key, [], None
        super().__init__(column_spacing=14, row_spacing=10, margin=15)
        for row in range(ROWS):
            cells = []
            for column in range(COLS):
                if row == 1 and column == 1:
                    cells.append(cells[0])
                    continue
                label = Gtk.Label(label=f"{key} R{row + 1:02}C{column + 1:02} 中文😀")
                cells.append(label)
                self.attach(label, column, row, 2 if row == 1 and column == 0 else 1, 1)
            self.cells.append(cells)

    def do_get_accessible(self):
        if self.native is None:
            self.native = TableAccessible(self)
        return self.native


Gdk.event_handler_set(event, None)
windows = []
for key in ("A", "B"):
    window = Gtk.Window(title="Computer Use v5 Table " + key)
    window.move(10 if key == "A" else 730, 80)
    window.set_default_size(700, 400)
    table = Table(key)
    window.add(table)
    window.show_all()
    windows.append(window)
    state["windows"][key] = dict(
        rows=ROWS, columns=COLS, merged=[1, 0, 1, 2], values=[[cell.get_text() for cell in row] for row in table.cells]
    )
state["ready"] = True
save()
GLib.timeout_add(100, save)
Gtk.main()
