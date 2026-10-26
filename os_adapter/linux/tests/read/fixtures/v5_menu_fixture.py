"""Owned GTK/Qt menus with independent read-side-effect counters.

Stdin only closes one named owned window or stops this fixture. It cannot open
menus, activate commands or synthesize keyboard/mouse input.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

TITLE = "Computer Use v5 Menu "


def main(toolkit, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    state = dict(
        pid=os.getpid(),
        processStart=Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19],
        toolkit=toolkit,
        ready=False,
        command=0,
        reason="startup",
        opened=0,
        activated=0,
        keyEvents=0,
        mouseEvents=0,
        windows={key: dict(open=True, opened=0, activated=0) for key in ("A", "B")},
        events=[],
    )
    windows = {}

    def save(reason=None):
        if reason:
            state["reason"] = reason
        temporary = output.with_suffix(".tmp")
        temporary.write_text(json.dumps(state))
        temporary.replace(output)
        return True

    def event(kind, window=None, title=None):
        state[kind] += 1
        if window is not None:
            state["windows"][window][kind] += 1
        state["events"].append(dict(kind=kind, window=window, title=title))
        save(kind)

    def arrange(close, stop):
        line = sys.stdin.readline()
        if not line:
            stop()
            return False
        request = json.loads(line)
        state["command"] = request["id"]
        if request["kind"] == "close":
            key = request["window"]
            assert key in windows and state["windows"][key]["open"]
            close(windows[key])
            state["windows"][key]["open"] = False
        elif request["kind"] == "stop":
            stop()
        else:
            raise ValueError("Unknown owned menu fixture arrangement")
        save(request["kind"])
        return True

    if toolkit == "gtk3":
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk, GLib, Gtk

        GLib.set_prgname("computer-use-v5-menu-fixture")
        GLib.set_application_name("Computer Use v5 Menu Fixture")
        state["versions"] = dict(
            Gtk=".".join(str(fn()) for fn in (Gtk.get_major_version, Gtk.get_minor_version, Gtk.get_micro_version)),
            PyGObject=gi.__version__,
        )

        def observe_event(current, _):
            if current.type in (Gdk.EventType.KEY_PRESS, Gdk.EventType.KEY_RELEASE):
                event("keyEvents")
            elif current.type in (Gdk.EventType.BUTTON_PRESS, Gdk.EventType.BUTTON_RELEASE):
                event("mouseEvents")
            Gtk.main_do_event(current)

        Gdk.event_handler_set(observe_event, None)

        def menu(parent, title, key):
            item = Gtk.MenuItem.new_with_label(title)
            nested = Gtk.Menu()
            nested.connect("map", lambda *_: event("opened", key, title))
            item.set_submenu(nested)
            parent.append(item)
            return nested

        def leaf(parent, title, key, enabled=True):
            item = Gtk.MenuItem.new_with_label(title)
            item.set_sensitive(enabled)
            item.connect("activate", lambda *_: event("activated", key, title))
            parent.append(item)

        for key in ("A", "B"):
            window = Gtk.Window(title=TITLE + key)
            window.set_default_size(580, 360)
            window.move(20 if key == "A" else 660, 80)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            bar = Gtk.MenuBar()
            file = menu(bar, "File", key)
            leaf(file, "Open " + key, key)
            leaf(menu(file, "Recent", key), "Document " + key, key)
            leaf(menu(file, "Advanced", key), "Run " + key, key)
            leaf(file, "Disabled " + key, key, False)
            leaf(menu(bar, "Help", key), "About " + key, key)
            box.pack_start(bar, False, False, 0)
            box.pack_start(Gtk.Label(label="Only reading menus in window " + key), True, True, 0)
            window.add(box)
            window.show_all()
            windows[key] = window
        GLib.io_add_watch(
            sys.stdin, GLib.IO_IN | GLib.IO_HUP, lambda *_: arrange(lambda window: window.destroy(), Gtk.main_quit)
        )
        GLib.timeout_add(100, save)
        state["ready"] = True
        save("ready")
        Gtk.main()
    else:
        from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, QEvent, QObject, QSocketNotifier, Qt, QTimer
        from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow

        app = QApplication([])
        app.setApplicationName("Computer Use v5 Menu Fixture")
        app.setQuitOnLastWindowClosed(False)
        state["versions"] = dict(Qt=QT_VERSION_STR, PyQt=PYQT_VERSION_STR)

        class InputEvents(QObject):
            def eventFilter(self, watched, current):
                if current.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                    event("keyEvents")
                elif current.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
                    event("mouseEvents")
                return False

        filter_ = InputEvents(app)
        app.installEventFilter(filter_)

        def menu(parent, title, key):
            nested = parent.addMenu(title)
            nested.aboutToShow.connect(lambda: event("opened", key, title))
            return nested

        def leaf(parent, title, key, enabled=True):
            item = parent.addAction(title)
            item.setEnabled(enabled)
            item.triggered.connect(lambda *_: event("activated", key, title))

        for key in ("A", "B"):
            window = QMainWindow()
            window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            window.setWindowTitle(TITLE + key)
            window.setGeometry(20 if key == "A" else 660, 80, 580, 360)
            bar = window.menuBar()
            file = menu(bar, "File", key)
            leaf(file, "Open " + key, key)
            leaf(menu(file, "Recent", key), "Document " + key, key)
            leaf(menu(file, "Advanced", key), "Run " + key, key)
            leaf(file, "Disabled " + key, key, False)
            leaf(menu(bar, "Help", key), "About " + key, key)
            window.setCentralWidget(QLabel("Only reading menus in window " + key))
            window.show()
            windows[key] = window
        notifier = QSocketNotifier(sys.stdin.fileno(), QSocketNotifier.Type.Read, app)
        notifier.activated.connect(lambda *_: arrange(lambda window: window.close(), app.quit))
        timer = QTimer(app)
        timer.timeout.connect(save)
        timer.start(100)
        state["ready"] = True
        save("ready")
        app.exec()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--toolkit", choices=("gtk3", "qt"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    main(args.toolkit, args.output.resolve())
