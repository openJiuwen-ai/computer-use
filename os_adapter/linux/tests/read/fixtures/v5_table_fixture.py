"""Owned real GTK/Qt table widgets for v5 read acceptance; no driver hooks."""

import json
import os
import sys
from pathlib import Path

toolkit, output = sys.argv[1], Path(sys.argv[2])
state = dict(pid=os.getpid(), toolkit=toolkit, inputs=0, ready=False, windows={})
snapshots = {}
ROWS, COLS = int(sys.argv[3]) if len(sys.argv) > 3 else 6, 3
SELECTED_ROW = int(sys.argv[4]) if len(sys.argv) > 4 else 3
VIEWPORT_ROW = int(sys.argv[5]) if len(sys.argv) > 5 else None
assert 0 <= SELECTED_ROW < ROWS
assert VIEWPORT_ROW is None or 0 <= VIEWPORT_ROW < ROWS


def save():
    for key_value, snapshot_value in snapshots.items():
        state["windows"][key_value] = snapshot_value()
    tmp = output.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False))
    tmp.replace(output)
    return True


def data(key, row, col):
    return f"{key} R{row + 1:02}C{col + 1:02} 中文😀"


if toolkit == "gtk3":
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk

    GLib.set_prgname("computer-use-v5-table-probe")
    GLib.set_application_name("Computer Use v5 Table Probe")
    if VIEWPORT_ROW is not None:
        Gtk.Settings.get_default().set_property("gtk-enable-animations", False)

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

    Gdk.event_handler_set(event, None)
    windows = []
    for key in ("A", "B"):
        window = Gtk.Window(title="Computer Use v5 Table " + key)
        window.move(10 if key == "A" else 730, 80)
        window.set_default_size(700, 400)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        window.add(box)
        store = Gtk.ListStore(str, str, str)
        for row in range(ROWS):
            store.append([data(key, row, col) for col in range(COLS)])
        view = Gtk.TreeView(model=store)
        view.get_accessible().set_name("Data table " + key)
        for col in range(COLS):
            view.append_column(Gtk.TreeViewColumn("Column " + str(col + 1), Gtk.CellRendererText(), text=col))
        view.get_selection().select_path(Gtk.TreePath.new_from_indices([SELECTED_ROW]))
        scroll = Gtk.ScrolledWindow()
        scroll.add(view)
        box.pack_start(scroll, True, True, 0)
        box.pack_start(Gtk.Button(label="Not a table " + key), False, False, 0)
        window.show_all()
        windows.append(window)
        state["windows"][key] = dict(
            rows=ROWS, columns=COLS, values=[list(row) for row in store], selectedRow=SELECTED_ROW
        )

        def snapshot(store=store, view=view):
            model, selected = view.get_selection().get_selected()
            result = dict(
                rows=ROWS,
                columns=COLS,
                values=[list(row) for row in store],
                selectedRow=model.get_path(selected).get_indices()[0] if selected is not None else None,
            )
            if VIEWPORT_ROW is not None:
                bounds = view.get_visible_range()
                paths = bounds[-2:] if bounds else []
                visible = []
                if len(paths) == 2 and all(isinstance(p, Gtk.TreePath) for p in paths):
                    bw = view.get_bin_window()
                    for r in range(paths[0].get_indices()[0], paths[1].get_indices()[0] + 1):
                        for c, column in enumerate(view.get_columns()):
                            rect = view.get_cell_area(Gtk.TreePath.new_from_indices([r]), column)
                            if (
                                rect.x < bw.get_width()
                                and rect.y < bw.get_height()
                                and rect.x + rect.width > 0
                                and rect.y + rect.height > 0
                            ):
                                visible.append(store[r][c])
                result["visibleCells"] = visible
            return result

        snapshots[key] = snapshot
    if VIEWPORT_ROW is not None:

        def scroll():
            for w in windows:
                w.get_child().get_children()[0].get_child().scroll_to_cell(
                    Gtk.TreePath.new_from_indices([VIEWPORT_ROW]), None, True, 0.0, 0.0
                )
            state["viewportReady"] = True
            save()
            return False

        GLib.timeout_add(200, scroll)
    state["ready"] = True
    save()
    GLib.timeout_add(100, save)
    Gtk.main()
else:
    from PyQt6.QtCore import QEvent, QObject, QTimer
    from PyQt6.QtWidgets import QApplication, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

    app = QApplication([])
    app.setApplicationName("Computer Use v5 Table Probe")

    class InputEvents(QObject):
        def eventFilter(self, watched, current):
            if current.type() in (
                QEvent.Type.KeyPress,
                QEvent.Type.KeyRelease,
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseButtonRelease,
            ):
                state["inputs"] += 1
                save()
            return False

    events = InputEvents(app)
    app.installEventFilter(events)
    windows = []
    for key in ("A", "B"):
        window = QWidget()
        window.setWindowTitle("Computer Use v5 Table " + key)
        window.setGeometry(10 if key == "A" else 730, 80, 700, 400)
        layout = QVBoxLayout(window)
        table = QTableWidget(ROWS, COLS)
        table.setAccessibleName("Data table " + key)
        table.setHorizontalHeaderLabels(["Column " + str(col + 1) for col in range(COLS)])
        table.setVerticalHeaderLabels(["Row " + str(row + 1) for row in range(ROWS)])
        for row in range(ROWS):
            for col in range(COLS):
                table.setItem(row, col, QTableWidgetItem(data(key, row, col)))
        table.setSpan(1, 0, 1, 2)
        table.setCurrentCell(SELECTED_ROW, 1)
        layout.addWidget(table)
        layout.addWidget(QPushButton("Not a table " + key))
        window.show()
        windows.append(window)
        state["windows"][key] = dict(
            rows=ROWS,
            columns=COLS,
            values=[[table.item(row, col).text() for col in range(COLS)] for row in range(ROWS)],
            selectedCell=[SELECTED_ROW, 1],
            merged=[1, 0, 1, 2],
        )

        def snapshot(table=table):
            result = dict(
                rows=ROWS,
                columns=COLS,
                values=[[table.item(row, col).text() for col in range(COLS)] for row in range(ROWS)],
                selectedCells=[[index.row(), index.column()] for index in table.selectedIndexes()],
                currentCell=[table.currentRow(), table.currentColumn()],
                merged=[1, 0, table.rowSpan(1, 0), table.columnSpan(1, 0)],
            )
            if VIEWPORT_ROW is not None:
                viewport = table.viewport().rect()
                result["visibleCells"] = [
                    table.item(r, c).text()
                    for r in range(ROWS)
                    for c in range(COLS)
                    if table.visualItemRect(table.item(r, c)).intersects(viewport)
                ]
            return result

        snapshots[key] = snapshot
    if VIEWPORT_ROW is not None:

        def scroll():
            for w in windows:
                table_value = w.layout().itemAt(0).widget()
                table_value.scrollToItem(table_value.item(VIEWPORT_ROW, 0))
            state["viewportReady"] = True
            save()

        QTimer.singleShot(200, scroll)
    state["ready"] = True
    save()
    timer = QTimer(app)
    timer.timeout.connect(save)
    timer.start(100)
    app.exec()
