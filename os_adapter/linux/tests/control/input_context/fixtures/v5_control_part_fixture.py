"""Real toolkit control composition for v5 controlPart acceptance.

Standard GTK/Qt widgets expose their own accessibility hierarchy and hit tests.
No custom accessible objects, roles, hit tests, or input callbacks are used.
Observers return False, retaining each toolkit's ordinary event processing.
Stdin only arranges disposable widgets; it never calls click or sends input.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TITLE = "Computer Use v5 Control Parts "
NAMES = {
    "direct": "Direct label control",
    "container": "Container label control",
    "independent": "Independent child control",
}


def main(toolkit, output):
    state = dict(
        toolkit=toolkit,
        command=0,
        sequence=0,
        reason="startup",
        independent=False,
        events=[],
        clicks={key: 0 for key in NAMES},
        rects={},
    )
    windows, widgets, observers = {}, {}, []
    origins = {"a": (40, 80), "b": (720, 80)}

    def publish(reason):
        state["reason"] = reason
        state["rects"] = {key: rectangle(widget) for key, widget in widgets.items()}
        temporary = output.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False))
        temporary.replace(output)

    def record(receiver, kind, *, button=None, stamp=None, x=0, y=0):
        state["sequence"] += 1
        state["events"].append(
            dict(
                sequence=state["sequence"],
                receiver=receiver,
                kind=kind,
                button=button,
                timestamp=stamp,
                x=float(x),
                y=float(y),
            )
        )
        publish(kind)

    def clicked(key, *_):
        state["clicks"][key] += 1
        publish("clicked-" + key)

    def arrange(request):
        kind = request["kind"]
        if kind == "activate":
            activate(request["window"])
        elif kind == "independent":
            assert not state["independent"], "Independent child is added only once"
            add_independent()
            state["independent"] = True
        elif kind == "stop":
            quit_app()
            return
        else:
            raise ValueError("Unknown owned fixture arrangement: " + str(kind))

        def settled():
            state["command"] = request["id"]
            publish(kind)

        later(180, settled)

    def request_ready(*_):
        line = sys.stdin.readline()
        if not line:
            quit_app()
            return False
        arrange(json.loads(line))
        return True

    if toolkit == "gtk3":
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk, GLib, Gtk

        GLib.set_prgname("computer-use-v5-control-part-fixture")
        GLib.set_application_name("Computer Use v5 Control Parts Fixture")

        def later(ms, fn):
            def once():
                fn()
                return False

            GLib.timeout_add(ms, once)

        def rectangle(widget):
            top = widget.get_toplevel()
            origin = top.get_window().get_origin() if top.get_window() else None
            point = widget.translate_coordinates(top, 0, 0)
            if origin is None or point is None:
                return None
            return [
                origin[-2] + point[0],
                origin[-1] + point[1],
                widget.get_allocated_width(),
                widget.get_allocated_height(),
            ]

        def activate(key):
            windows[key].present()

        def quit_app():
            Gtk.main_quit()

        def watch(widget, key):
            widgets[key] = widget
            widget.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK)

            def press(_, event):
                # GDK also reports a double-click notification after its
                # ordinary second press. Record it without double-counting.
                kind = "press" if event.type == Gdk.EventType.BUTTON_PRESS else "double-notify"
                record(key, kind, button=int(event.button), stamp=int(event.time), x=event.x, y=event.y)
                return False

            def release(_, event):
                record(key, "release", button=int(event.button), stamp=int(event.time), x=event.x, y=event.y)
                return False

            widget.connect("button-press-event", press)
            widget.connect("button-release-event", release)

        for key in ("a", "b"):
            window = Gtk.Window(title=TITLE + key.upper())
            window.set_default_size(590, 470)
            window.move(*origins[key])
            windows[key] = window
        layout = Gtk.Fixed()
        windows["a"].add(layout)
        windows["b"].add(Gtk.Label(label="Unrelated foreground window"))
        contents = {}
        for key, y in (("direct", 35), ("container", 240)):
            button = Gtk.Button()
            button.set_size_request(430, 170)
            button.get_accessible().set_name(NAMES[key])
            label = Gtk.Label(label=key.capitalize() + " passive label")
            label.set_hexpand(True)
            label.set_vexpand(True)
            if key == "direct":
                button.add(label)
            else:
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                box.pack_start(label, True, True, 0)
                button.add(box)
                contents[key] = box
                watch(box, key + "-layout")
            watch(label, key + "-label")
            watch(button, key)
            button.connect("clicked", lambda _, key=key: clicked(key))
            layout.put(button, 60, y)

        def add_independent():
            # GtkButton is a native GtkBin. Its child may be a layout with an
            # independent GtkButton; no accessibility behaviour is overridden.
            label = widgets["container-label"]
            contents["container"].remove(label)
            widgets.pop("container-label")
            label.destroy()
            child = Gtk.Button(label="Independent child")
            child.get_accessible().set_name(NAMES["independent"])
            contents["container"].pack_start(child, True, True, 0)
            watch(child, "independent")
            child.connect("clicked", lambda _: clicked("independent"))
            child.show_all()

        for window in windows.values():
            window.show_all()
        GLib.io_add_watch(sys.stdin, GLib.IO_IN | GLib.IO_HUP, request_ready)
        later(400, lambda: (activate("b"), publish("ready")))
        Gtk.main()
    elif toolkit == "qt":
        from PyQt6.QtCore import QEvent, QObject, QPoint, QSocketNotifier, Qt, QTimer
        from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

        app = QApplication([])
        app.setApplicationName("Computer Use v5 Control Parts Fixture")

        def later(ms, fn):
            QTimer.singleShot(ms, fn)

        def rectangle(widget):
            point = widget.mapToGlobal(QPoint(0, 0))
            return [point.x(), point.y(), widget.width(), widget.height()]

        def activate(key):
            windows[key].raise_()
            windows[key].activateWindow()

        def quit_app():
            app.quit()

        class Observer(QObject):
            def __init__(self, key):
                super().__init__()
                self.key = key

            def eventFilter(self, watched, event):
                kinds = {
                    QEvent.Type.MouseButtonPress: "press",
                    QEvent.Type.MouseButtonRelease: "release",
                    QEvent.Type.MouseButtonDblClick: "press",
                }
                if event.type() in kinds:
                    number = {
                        Qt.MouseButton.LeftButton: 1,
                        Qt.MouseButton.MiddleButton: 2,
                        Qt.MouseButton.RightButton: 3,
                    }.get(event.button(), int(event.button().value))
                    record(
                        self.key,
                        kinds[event.type()],
                        button=number,
                        stamp=int(event.timestamp()),
                        x=event.position().x(),
                        y=event.position().y(),
                    )
                return False

        def watch(widget, key):
            widgets[key] = widget
            observer = Observer(key)
            observers.append(observer)
            widget.installEventFilter(observer)

        for key in ("a", "b"):
            window = QWidget()
            window.setWindowTitle(TITLE + key.upper())
            window.setGeometry(*origins[key], 590, 470)
            windows[key] = window
        note = QLabel("Unrelated foreground window", windows["b"])
        note.setGeometry(40, 40, 450, 100)
        contents = {}
        for key, y in (("direct", 35), ("container", 240)):
            button = QPushButton(windows["a"])
            button.setAccessibleName(NAMES[key])
            button.setGeometry(60, y, 430, 170)
            if key == "direct":
                parent = button
            else:
                parent = QWidget(button)
                parent.setGeometry(8, 8, 414, 154)
                contents[key] = QVBoxLayout(parent)
                contents[key].setContentsMargins(0, 0, 0, 0)
                watch(parent, key + "-layout")
            label = QLabel(key.capitalize() + " passive label", parent)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if key == "direct":
                label.setGeometry(8, 8, 414, 154)
            else:
                contents[key].addWidget(label)
            watch(label, key + "-label")
            watch(button, key)
            button.clicked.connect(lambda checked=False, key=key: clicked(key))

        def add_independent():
            label = widgets.pop("container-label")
            contents["container"].removeWidget(label)
            label.hide()
            label.deleteLater()
            child = QPushButton("Independent child")
            child.setAccessibleName(NAMES["independent"])
            child.setMinimumHeight(110)
            contents["container"].addWidget(child)
            watch(child, "independent")
            child.clicked.connect(lambda: clicked("independent"))
            child.show()

        for window in windows.values():
            window.show()
        notifier = QSocketNotifier(sys.stdin.fileno(), QSocketNotifier.Type.Read)
        notifier.activated.connect(request_ready)
        later(400, lambda: (activate("b"), publish("ready")))
        app.exec()
    else:
        raise ValueError(toolkit)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
