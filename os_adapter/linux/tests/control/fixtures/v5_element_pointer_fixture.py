"""Owned GTK/Qt leaf Components for v5 addressed pointer acceptance.

Canvas widgets deliberately declare no native actions. Only real mouse/wheel
callbacks update their event journal. Stdin commands arrange owned windows,
move/replace a canvas, or arm a layout change after the first real press; they
never synthesize input or call the callbacks being tested.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TITLE = "Computer Use v5 Element Pointer "
NAME = "Pointer canvas "


def main(toolkit, output, *, click_v1=False):
    state = dict(toolkit=toolkit, command=0, sequence=0, reason="startup", windows={})
    windows, canvases = {}, {}
    # Window B stays away from A initially. A's canvas can move inside its own
    # window without reparenting or changing its accessible object identity.
    origins = dict(a=(50, 90), b=(720, 90))
    original, relocated = (25, 35), (285, 205)
    for key in ("a", "b"):
        state["windows"][key] = dict(
            generation=1,
            presses=[],
            releases=[],
            pressed=[],
            wheelX=0,
            wheelY=0,
            events=[],
            position=list(original),
            moveOnPress=False,
            movesAfterPress=0,
            rect=None,
            canvasRect=None,
            semanticActions=0,
        )

    def publish(reason):
        state["reason"] = reason
        for key in windows:
            state["windows"][key]["rect"] = window_rect(key)
            state["windows"][key]["canvasRect"] = canvas_rect(key)
        temporary = output.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False))
        temporary.replace(output)

    def event(key, kind, *, button=None, x=0, y=0, dx=0, dy=0):
        row = state["windows"][key]
        state["sequence"] += 1
        row["events"].append(
            dict(
                sequence=state["sequence"],
                kind=kind,
                button=button,
                x=float(x),
                y=float(y),
                dx=dx,
                dy=dy,
                generation=row["generation"],
            )
        )
        row["events"] = row["events"][-512:]
        if kind == "press":
            row["presses"].append(button)
            if button not in row["pressed"]:
                row["pressed"].append(button)
            if row["moveOnPress"]:
                row["moveOnPress"] = False
                row["movesAfterPress"] += 1
                move_canvas(key)
        elif kind == "release":
            row["releases"].append(button)
            if button in row["pressed"]:
                row["pressed"].remove(button)
        elif kind == "wheel":
            row["wheelX"] += dx
            row["wheelY"] += dy
        publish(kind)

    def semantic_action(key):
        state["windows"][key]["semanticActions"] += 1
        publish("semanticAction")

    def arrange(request):
        kind = request["kind"]
        if kind == "spread":
            for key in ("a", "b"):
                place_window(key, *origins[key], 560, 390)
            activate("b")
        elif kind == "cover":
            x, y, width, height = window_rect("a")
            place_window("b", max(0, x - 8), max(0, y - 8), width + 24, height + 24)
            activate("b")
        elif kind == "activate":
            assert request["window"] in windows
            activate(request["window"])
        elif kind == "moveCanvas":
            move_canvas("a")
        elif kind == "armMoveAfterPress":
            state["windows"]["a"]["moveOnPress"] = True
        elif kind == "replaceCanvas":
            replace_canvas("a")
            state["windows"]["a"]["generation"] += 1
        elif kind == "stop":
            quit_app()
            return
        else:
            raise ValueError("Unknown owned fixture command: " + str(kind))

        # Acknowledge only after toolkit allocation/WM geometry can settle.
        def settled():
            state["command"] = request["id"]
            publish(kind)

        later(150, settled)

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

        GLib.set_prgname("computer-use-v5-element-pointer-fixture")
        GLib.set_application_name("Computer Use v5 Element Pointer Fixture")
        layouts = {}

        def later(ms, fn):
            def once():
                fn()
                return False

            GLib.timeout_add(ms, once)

        def window_rect(key):
            x, y = windows[key].get_position()
            width, height = windows[key].get_size()
            return [x, y, width, height]

        def canvas_rect(key):
            canvas = canvases[key]
            native = canvas.get_window()
            if native is None:
                return None
            origin = native.get_origin()
            width, height = canvas.get_allocated_width(), canvas.get_allocated_height()
            return [origin[-2], origin[-1], width, height]

        def place_window(key, x, y, width, height):
            windows[key].move(x, y)
            windows[key].resize(width, height)

        def activate(key):
            windows[key].present()

        def quit_app():
            Gtk.main_quit()

        def move_canvas(key):
            row = state["windows"][key]
            position = relocated if tuple(row["position"]) == original else original
            row["position"] = list(position)
            layouts[key].move(canvases[key], *position)

        def make_canvas(key):
            # An empty EventBox paints its own CSS background in GTK. It is
            # still a leaf Component with no declared actions; no child Label
            # changes hit testing, and no Python Cairo converter is required.
            # The AX name and window title identify the unlabelled color patch.
            canvas = Gtk.EventBox()
            canvas.set_visible_window(True)
            style = Gtk.CssProvider()
            color = "#26a640" if key == "a" else "#3359bf"
            style.load_from_data(("* { background-color: " + color + "; }").encode())
            canvas.get_style_context().add_provider(style, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            canvas.set_size_request(220, 130)
            canvas.set_can_focus(False)
            canvas.get_accessible().set_name(NAME + key.upper())
            canvas.add_events(
                Gdk.EventMask.BUTTON_PRESS_MASK
                | Gdk.EventMask.BUTTON_RELEASE_MASK
                | Gdk.EventMask.POINTER_MOTION_MASK
                | Gdk.EventMask.SCROLL_MASK
            )

            def press(_, value):
                # GDK's extra DOUBLE_BUTTON_PRESS notification is not another
                # physical press; the ordinary second press is already logged.
                if value.type == Gdk.EventType.BUTTON_PRESS:
                    event(key, "press", button=int(value.button), x=value.x, y=value.y)
                return True

            def release(_, value):
                event(key, "release", button=int(value.button), x=value.x, y=value.y)
                return True

            def motion(_, value):
                event(key, "motion", x=value.x, y=value.y)
                return True

            def scroll(_, value):
                axes = {
                    Gdk.ScrollDirection.UP: (0, -1),
                    Gdk.ScrollDirection.DOWN: (0, 1),
                    Gdk.ScrollDirection.LEFT: (-1, 0),
                    Gdk.ScrollDirection.RIGHT: (1, 0),
                }
                if value.direction in axes:
                    dx, dy = axes[value.direction]
                else:
                    _, dx, dy = value.get_scroll_deltas()
                event(key, "wheel", x=value.x, y=value.y, dx=dx, dy=dy)
                return True

            for signal, handler in (
                ("button-press-event", press),
                ("button-release-event", release),
                ("motion-notify-event", motion),
                ("scroll-event", scroll),
            ):
                canvas.connect(signal, handler)
            return canvas

        def replace_canvas(key):
            canvases[key].destroy()
            canvases[key] = make_canvas(key)
            layouts[key].put(canvases[key], *state["windows"][key]["position"])
            canvases[key].show()

        for key in ("a", "b"):
            window = Gtk.Window(title=TITLE + key.upper())
            window.set_default_size(560, 390)
            window.move(*origins[key])
            layout = Gtk.Fixed()
            window.add(layout)
            windows[key], layouts[key] = window, layout
            canvases[key] = make_canvas(key)
            layout.put(canvases[key], *original)
            if click_v1:
                button = Gtk.Button(label="Semantic button " + key.upper())
                button.connect("clicked", lambda _, selected=key: semantic_action(selected))
                layout.put(button, 280, 35)
            window.show_all()
        GLib.io_add_watch(sys.stdin, GLib.IO_IN | GLib.IO_HUP, request_ready)
        later(250, lambda: (activate("b"), publish("ready")))
        Gtk.main()
    elif toolkit == "qt":
        from PyQt6.QtCore import QPoint, QSocketNotifier, Qt, QTimer
        from PyQt6.QtGui import QColor, QPainter
        from PyQt6.QtWidgets import QApplication, QPushButton, QWidget

        app = QApplication([])
        app.setApplicationName("Computer Use v5 Element Pointer Fixture")

        def later(ms, fn):
            QTimer.singleShot(ms, fn)

        def window_rect(key):
            window = windows[key]
            point = window.mapToGlobal(QPoint(0, 0))
            return [point.x(), point.y(), window.width(), window.height()]

        def canvas_rect(key):
            canvas = canvases[key]
            point = canvas.mapToGlobal(QPoint(0, 0))
            return [point.x(), point.y(), canvas.width(), canvas.height()]

        def place_window(key, x, y, width, height):
            windows[key].move(x, y)
            windows[key].resize(width, height)

        def activate(key):
            windows[key].raise_()
            windows[key].activateWindow()

        def quit_app():
            app.quit()

        def move_canvas(key):
            row = state["windows"][key]
            position = relocated if tuple(row["position"]) == original else original
            row["position"] = list(position)
            canvases[key].move(*position)

        class Canvas(QWidget):
            def __init__(self, key):
                super().__init__(windows[key])
                self.key = key
                self.setAccessibleName(NAME + key.upper())
                self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                self.setMouseTracking(True)
                self.resize(220, 130)

            def paintEvent(self, _):
                painter = QPainter(self)
                painter.fillRect(self.rect(), QColor("#26a640" if self.key == "a" else "#3359bf"))
                painter.setPen(QColor("white"))
                painter.drawText(15, 55, NAME + self.key.upper())
                painter.end()

            def button(self, value, kind):
                numbers = {Qt.MouseButton.LeftButton: 1, Qt.MouseButton.MiddleButton: 2, Qt.MouseButton.RightButton: 3}
                point = value.position()
                event(self.key, kind, button=numbers[value.button()], x=point.x(), y=point.y())
                value.accept()

            def mousePressEvent(self, value):
                self.button(value, "press")

            def mouseDoubleClickEvent(self, value):
                # Qt replaces the second press with this event.
                self.button(value, "press")

            def mouseReleaseEvent(self, value):
                self.button(value, "release")

            def mouseMoveEvent(self, value):
                point = value.position()
                event(self.key, "motion", x=point.x(), y=point.y())
                value.accept()

            def wheelEvent(self, value):
                point, delta = value.position(), value.angleDelta()
                event(self.key, "wheel", x=point.x(), y=point.y(), dx=-delta.x() / 120, dy=-delta.y() / 120)
                value.accept()

        def replace_canvas(key):
            old = canvases[key]
            old.hide()
            old.deleteLater()
            canvases[key] = Canvas(key)
            canvases[key].move(*state["windows"][key]["position"])
            canvases[key].show()

        for key in ("a", "b"):
            window = QWidget()
            window.setWindowTitle(TITLE + key.upper())
            window.resize(560, 390)
            window.move(*origins[key])
            windows[key] = window
            canvases[key] = Canvas(key)
            canvases[key].move(*original)
            if click_v1:
                button = QPushButton("Semantic button " + key.upper(), window)
                button.move(280, 35)
                button.clicked.connect(lambda _, selected=key: semantic_action(selected))
            window.show()
        notifier = QSocketNotifier(sys.stdin.fileno(), QSocketNotifier.Type.Read)
        notifier.activated.connect(request_ready)
        later(250, lambda: (activate("b"), publish("ready")))
        app.exec()
    else:
        raise ValueError(toolkit)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]), click_v1="--click-v1" in sys.argv[3:])
