"""Owned real scrolling widgets; journals adjustments, not synthetic effects."""

import hashlib
import json
import os
import sys
from pathlib import Path

toolkit, output = sys.argv[1], Path(sys.argv[2])
state = dict(pid=os.getpid(), toolkit=toolkit, ready=False, windows={})
snapshots, windows = {}, []
TEXT = "\n".join(f"Line {i:03} 中文😀 " + "content " * 30 for i in range(100))


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def publish():
    state["windows"] = {key: snapshot() for key, snapshot in snapshots.items()}
    p = output.with_suffix(".tmp")
    p.write_text(json.dumps(state, ensure_ascii=False))
    p.replace(output)
    return True


if toolkit == "gtk3":
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk

    GLib.set_prgname("computer-use-v5-scroll-fixture")
    for key in ("A", "B"):
        window = Gtk.Window(title="Computer Use v5 Scroll " + key)
        window.move(10 if key == "A" else 730, 40)
        window.set_default_size(680, 680)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        window.add(box)
        text = Gtk.TextView()
        text.get_buffer().set_text(TEXT)
        text.set_wrap_mode(Gtk.WrapMode.NONE)
        text.get_accessible().set_name("Text content " + key)
        text_area = Gtk.ScrolledWindow()
        text_area.get_accessible().set_name("Text viewport " + key)
        text_area.add(text)
        box.pack_start(text_area, True, True, 0)
        store = Gtk.ListStore(str, str, str)
        for i in range(100):
            store.append([f"{key} R{i + 1} C{j + 1}" for j in range(3)])
        table = Gtk.TreeView(model=store)
        table.get_accessible().set_name("Table content " + key)
        for j in range(3):
            table.append_column(Gtk.TreeViewColumn("Column " + str(j + 1), Gtk.CellRendererText(), text=j))
        table_area = Gtk.ScrolledWindow()
        table_area.get_accessible().set_name("Table viewport " + key)
        table_area.add(table)
        box.pack_start(table_area, True, True, 0)
        window.show_all()
        windows.append(window)

        def snapshot(text=text, table=table, store=store, text_area=text_area, table_area=table_area):
            buf = text.get_buffer()
            model, selected = table.get_selection().get_selected()
            return dict(
                textX=text_area.get_hadjustment().get_value(),
                textY=text_area.get_vadjustment().get_value(),
                tableY=table_area.get_vadjustment().get_value(),
                textHash=digest(buf.get_text(*buf.get_bounds(), True)),
                tableHash=digest(json.dumps([list(row) for row in store])),
                selected=model.get_path(selected).get_indices()[0] if selected is not None else None,
            )

        snapshots[key] = snapshot
    state["ready"] = True
    publish()
    GLib.timeout_add(20, publish)
    Gtk.main()
else:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import (
        QApplication,
        QHeaderView,
        QLabel,
        QScrollArea,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )

    app = QApplication([])
    app.setApplicationName("Computer Use v5 Scroll Fixture")
    for key in ("A", "B"):
        window = QWidget()
        window.setWindowTitle("Computer Use v5 Scroll " + key)
        window.setGeometry(10 if key == "A" else 730, 40, 680, 680)
        box = QVBoxLayout(window)
        text = QLabel(TEXT)
        text.setAccessibleName("Text content " + key)
        text_area = QScrollArea()
        text_area.setAccessibleName("Text viewport " + key)
        text_area.setWidget(text)
        box.addWidget(text_area)
        table = QTableWidget(100, 3)
        # The wheel must be over real cells, not the empty area beside columns.
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setAccessibleName("Table content " + key)
        for i in range(100):
            for j in range(3):
                table.setItem(i, j, QTableWidgetItem(f"{key} R{i + 1} C{j + 1}"))
        box.addWidget(table)
        window.show()
        windows.append(window)

        def snapshot(text=text, text_area=text_area, table=table):
            return dict(
                textX=text_area.horizontalScrollBar().value(),
                textY=text_area.verticalScrollBar().value(),
                tableY=table.verticalScrollBar().value(),
                textHash=digest(text.text()),
                tableHash=digest(json.dumps([[table.item(i, j).text() for j in range(3)] for i in range(100)])),
                selected=[[index.row(), index.column()] for index in table.selectedIndexes()],
            )

        snapshots[key] = snapshot
    state["ready"] = True
    publish()
    timer = QTimer(app)
    timer.timeout.connect(publish)
    timer.start(20)
    app.exec()
