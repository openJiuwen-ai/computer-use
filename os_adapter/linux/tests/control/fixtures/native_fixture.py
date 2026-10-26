"""GTK 4 / Qt fixtures; each emits actual widget state for independent verification."""

import json
import os
import pathlib
import sys

toolkit, output = sys.argv[1], pathlib.Path(sys.argv[2])
state = dict(a="", b="untouched", clicks=0, modal=False)
windows = []


def save(**changes):
    state.update(changes)
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(state))
    os.replace(temporary, output)


if toolkit == "gtk4":
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, Gtk

    app = Gtk.Application(application_id="org.computeruse.NativeFixture", flags=Gio.ApplicationFlags.NON_UNIQUE)

    def activate(app):
        for letter_value in ("A", "B"):
            window_value = Gtk.ApplicationWindow(application=app, title=f"Computer Use Native {letter_value}")
            window_value.set_default_size(400, 200)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            window_value.set_child(box)
            entry_value = Gtk.Entry(text=state[letter_value.lower()])
            entry_value.update_property([Gtk.AccessibleProperty.LABEL], [f"Input {letter_value}"])
            entry_value.connect("changed", lambda entry, key=letter_value.lower(): save(**{key: entry.get_text()}))
            box.append(entry_value)
            if letter_value == "A":
                button_value = Gtk.Button(label="Count")
                button_value.connect("clicked", lambda _: save(clicks=state["clicks"] + 1))
                box.append(button_value)

                def open_dialog(_):
                    modal = Gtk.Window(title="Native modal", transient_for=windows[0], modal=True)
                    dismiss = Gtk.Button(label="Dismiss")
                    dismiss.connect("clicked", lambda _: (save(modal=False), modal.destroy()))
                    modal.set_child(dismiss)
                    save(modal=True)
                    modal.present()

                button_value = Gtk.Button(label="Open dialog")
                button_value.connect("clicked", open_dialog)
                box.append(button_value)
            windows.append(window_value)
            window_value.present()
        save()

    app.connect("activate", activate)
    app.run([])
elif toolkit == "qt":
    from PyQt6.QtWidgets import (
        QApplication,
        QDialog,
        QLineEdit,
        QPushButton,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )

    app = QApplication([])
    app.setApplicationName("Computer Use Native Fixture")
    for letter in ("A", "B"):
        window = QWidget()
        window.setWindowTitle(f"Computer Use Native {letter}")
        layout = QVBoxLayout(window)
        entry = QLineEdit(state[letter.lower()])
        entry.setAccessibleName(f"Input {letter}")
        entry.textChanged.connect(lambda text, key=letter.lower(): save(**{key: text}))
        layout.addWidget(entry)
        if letter == "A":
            button = QPushButton("Count")
            button.clicked.connect(lambda _: save(clicks=state["clicks"] + 1))
            layout.addWidget(button)

            def open_dialog(_):
                modal = QDialog(windows[0])
                modal.setWindowTitle("Native modal")
                modal.setModal(True)
                dismiss = QPushButton("Dismiss")
                box = QVBoxLayout(modal)
                box.addWidget(dismiss)
                dismiss.clicked.connect(modal.accept)
                save(modal=True)
                modal.exec()
                save(modal=False)

            button = QPushButton("Open dialog")
            button.clicked.connect(open_dialog)
            layout.addWidget(button)
            table = QTableWidget(2, 2)
            table.setAccessibleName("Sample table")
            for row in range(2):
                for col in range(2):
                    table.setItem(row, col, QTableWidgetItem(f"Cell {row + 1},{col + 1}"))
            table.setCurrentCell(1, 1)
            layout.addWidget(table)
        windows.append(window)
        window.show()
    save()
    app.exec()
else:
    raise ValueError(toolkit)
