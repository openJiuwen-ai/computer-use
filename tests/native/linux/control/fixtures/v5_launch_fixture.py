"""Disposable GTK3/Qt process launched only through a real desktop recipe."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def birth(pid):
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def main(toolkit, count, output):
    output.mkdir(parents=True, exist_ok=True)
    log = (output / "fixture.log").open("a", buffering=1)
    sys.stdout = sys.stderr = log
    pid = os.getpid()
    started = dict(pid=pid, birth=birth(pid), toolkit=toolkit, windows=count, started=time.time())
    with (output / "launches.jsonl").open("a") as stream:
        stream.write(json.dumps(started) + "\n")
    state = dict(**started, reason="startup", fields={}, changes={}, closed=[], visible=[], readyAt=None)
    windows = {}
    prefix = "Computer Use v5 Launch " + ("single" if count == 1 else "multiple") + " "

    def publish(reason):
        state["reason"] = reason
        temporary = output / "state.tmp"
        temporary.write_text(json.dumps(state, ensure_ascii=False))
        temporary.replace(output / "state.json")

    def changed(key, value):
        state["fields"][key] = value
        state["changes"][key] += 1
        publish("text-changed")

    def closed(key):
        state["visible"].remove(key)
        state["closed"].append(key)
        publish("window-closed")
        if not state["visible"]:
            quit_app()

    def create():
        for number in range(count):
            key = chr(ord("A") + number)
            state["fields"][key] = "initial-" + key
            state["changes"][key] = 0
            state["visible"].append(key)
            create_window(key, prefix + key, 80 + number * 620, 100)
        state["readyAt"] = time.time()
        publish("ready")

    publish("startup")
    if toolkit == "gtk3":
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import GLib, Gtk

        GLib.set_prgname("computer-use-v5-launch-fixture")
        GLib.set_application_name("Computer Use v5 Launch Fixture")

        def quit_app():
            Gtk.main_quit()

        def create_window(key, title, x, y):
            window = Gtk.Window(title=title)
            window.set_default_size(520, 220)
            window.move(x, y)
            layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
            layout.set_border_width(24)
            layout.pack_start(Gtk.Label(label="Input " + key), False, False, 0)
            editor = Gtk.Entry()
            editor.set_text(state["fields"][key])
            editor.get_accessible().set_name("Input " + key)
            editor.connect("changed", lambda widget: changed(key, widget.get_text()))
            layout.pack_start(editor, False, False, 0)
            button = Gtk.Button(label="Close " + key)
            button.connect("clicked", lambda _: window.destroy())
            layout.pack_start(button, False, False, 0)
            window.add(layout)
            window.connect("destroy", lambda _: closed(key))
            windows[key] = window
            window.show_all()

        def delayed():
            create()
            return False

        GLib.timeout_add(300, delayed)
        Gtk.main()
    else:
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

        application = QApplication([])
        application.setApplicationName("Computer Use v5 Launch Fixture")

        def quit_app():
            application.quit()

        class Window(QWidget):
            def __init__(self, key):
                super().__init__()
                self.key = key

            def closeEvent(self, event):
                closed(self.key)
                event.accept()

        def create_window(key, title, x, y):
            window = Window(key)
            window.setWindowTitle(title)
            window.setGeometry(x, y, 520, 220)
            layout = QVBoxLayout(window)
            layout.addWidget(QLabel("Input " + key))
            editor = QLineEdit(state["fields"][key])
            editor.setAccessibleName("Input " + key)
            editor.textChanged.connect(lambda value: changed(key, value))
            layout.addWidget(editor)
            button = QPushButton("Close " + key)
            button.clicked.connect(window.close)
            layout.addWidget(button)
            windows[key] = window
            window.show()

        QTimer.singleShot(300, create)
        application.exec()
    publish("exited")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--toolkit", choices=("gtk3", "qt"), required=True)
    parser.add_argument("--windows", type=int, choices=(1, 2), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    main(args.toolkit, args.windows, args.output)
