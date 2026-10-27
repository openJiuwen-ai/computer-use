"""Owned GTK/Qt content for raw v5 read acceptance; no user documents."""

import json
import pathlib
import sys

toolkit, output = sys.argv[1], pathlib.Path(sys.argv[2])
TITLE = "Computer Use v5 Read "
INITIAL = "v5 中文 😀"
LONG = "中😀" * 3000
state = dict(
    a=INITIAL,
    b="untouched",
    clicks=0,
    toggle=False,
    disabledClicks=0,
    submissions=dict(a=0, b=0),
    submitted=dict(a=None, b=None),
)


def save():
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False))
    temporary.replace(output)


def submitted(text, key):
    state["submissions"][key] += 1
    state["submitted"][key] = text
    save()


if toolkit == "gtk3":
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk

    GLib.set_prgname("computer-use-v5-read-fixture")
    GLib.set_application_name("Computer Use v5 Read Fixture")
    windows = []
    for letter in ("A", "B"):
        window = Gtk.Window(title=TITLE + letter)
        window.set_default_size(520, 640)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        window.add(box)
        entry = Gtk.Entry(text=state[letter.lower()])
        entry.get_accessible().set_name("Input " + letter)

        def changed(entry, key):
            state[key] = entry.get_text()
            save()

        entry.connect("changed", changed, letter.lower())
        entry.connect("activate", lambda entry, key: submitted(entry.get_text(), key), letter.lower())
        box.pack_start(entry, False, False, 0)
        if letter == "A":
            button = Gtk.Button(label="Count")

            def count(_):
                state.update(clicks=state["clicks"] + 1)
                save()

            button.connect("clicked", count)
            box.pack_start(button, False, False, 0)
            toggle = Gtk.CheckButton(label="Toggle")

            def toggled(button):
                state.update(toggle=button.get_active())
                save()

            toggle.connect("toggled", toggled)
            box.pack_start(toggle, False, False, 0)
            disabled = Gtk.Button(label="Disabled count")

            def disabled_count(_):
                state.update(disabledClicks=state["disabledClicks"] + 1)
                save()

            disabled.connect("clicked", disabled_count)
            disabled.set_sensitive(False)
            box.pack_start(disabled, False, False, 0)
            text = Gtk.TextView(editable=False)
            text.get_accessible().set_name("Long text")
            text.get_buffer().set_text(LONG)
            scroll = Gtk.ScrolledWindow()
            scroll.set_size_request(-1, 80)
            scroll.add(text)
            box.pack_start(scroll, False, False, 0)
            items = Gtk.ListBox()
            items.get_accessible().set_name("Paged items")
            for index in range(150):
                items.add(Gtk.Label(label=f"Item {index:03d}"))
            scroll = Gtk.ScrolledWindow()
            scroll.add(items)
            box.pack_start(scroll, True, True, 0)
        windows.append(window)
        window.show_all()
    save()
    Gtk.main()
elif toolkit == "qt":
    from PyQt6.QtWidgets import (
        QApplication,
        QCheckBox,
        QLineEdit,
        QListWidget,
        QPlainTextEdit,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

    app = QApplication([])
    app.setApplicationName("Computer Use v5 Read Fixture")
    windows = []
    for letter in ("A", "B"):
        window = QWidget()
        window.setWindowTitle(TITLE + letter)
        window.resize(520, 640)
        layout = QVBoxLayout(window)
        entry = QLineEdit(state[letter.lower()])
        entry.setAccessibleName("Input " + letter)

        def changed(value, key):
            state.update({key: value})
            save()

        entry.textChanged.connect(lambda value, key=letter.lower(): changed(value, key))
        entry.returnPressed.connect(lambda entry=entry, key=letter.lower(): submitted(entry.text(), key))
        layout.addWidget(entry)
        if letter == "A":
            button = QPushButton("Count")

            def count(_):
                state.update(clicks=state["clicks"] + 1)
                save()

            button.clicked.connect(count)
            layout.addWidget(button)
            toggle = QCheckBox("Toggle")

            def toggled(value):
                state.update(toggle=value)
                save()

            toggle.toggled.connect(toggled)
            layout.addWidget(toggle)
            disabled = QPushButton("Disabled count")

            def disabled_count(_):
                state.update(disabledClicks=state["disabledClicks"] + 1)
                save()

            disabled.clicked.connect(disabled_count)
            disabled.setEnabled(False)
            layout.addWidget(disabled)
            text = QPlainTextEdit(LONG)
            text.setReadOnly(True)
            text.setAccessibleName("Long text")
            text.setMaximumHeight(80)
            layout.addWidget(text)
            items = QListWidget()
            items.setAccessibleName("Paged items")
            items.addItems([f"Item {index:03d}" for index in range(150)])
            layout.addWidget(items)
        window.show()
        windows.append(window)
    save()
    app.exec()
else:
    raise ValueError(toolkit)
