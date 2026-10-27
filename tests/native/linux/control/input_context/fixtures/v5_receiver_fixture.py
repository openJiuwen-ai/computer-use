"""Owned real composite controls for contained-editor acceptance.

The combo's internal Entry/LineEdit is a normal toolkit widget. No custom
accessibility implementation claims containment or editable capabilities.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

TITLE = "Computer Use v5 Receiver "


def main(toolkit, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    state = dict(
        pid=os.getpid(),
        toolkit=toolkit,
        reason="startup",
        sequence=0,
        editors={},
        commits={},
        pasteKeys={},
        pasteEvents=[],
        keys=[],
        command=0,
        driftTarget=None,
    )
    editors, windows = {}, []
    initial = dict(
        combo="start|end",
        neighbor="same-window untouched",
        left="left untouched",
        right="right untouched",
        other="other-window untouched",
        readonly="read-only value",
    )
    for key, value in initial.items():
        state["editors"][key] = dict(text=value, changes=0, focused=False, cursor=0, selection=None)
        state["commits"][key] = state["pasteKeys"][key] = 0

    def publish(reason=None):
        if reason is not None:
            state["reason"] = reason
        for key, editor in editors.items():
            state["editors"][key].update(observe(editor))
        temporary = output.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False))
        temporary.replace(output)
        return True

    def changed(key):
        state["editors"][key]["changes"] += 1
        state["sequence"] += 1
        publish("changed-" + key)
        if key == "left" and state["driftTarget"] is not None:
            destination = state["driftTarget"]
            state["driftTarget"] = None
            # Run synchronously after the first real widget edit. No requested
            # text is synthesized, and a same-window move never activates it.
            focus_editor(destination, activate=destination == "other")
            publish("drift-after-first-edit-" + destination)

    def committed(key):
        state["commits"][key] += 1
        state["sequence"] += 1
        publish("commit-" + key)

    def pasted(key, **event):
        state["pasteKeys"][key] += 1
        state["pasteEvents"].append(dict(editor=key, **event))
        publish("paste-key-" + key)

    def key_event(key, pressed, **event):
        state["keys"].append(dict(editor=key, kind="press" if pressed else "release", **event))
        publish("key-" + key)

    def arrange():
        line = sys.stdin.readline()
        if not line:
            return False
        request = json.loads(line)
        if request["kind"] == "focus":
            focus_editor(request["field"], activate=True)
        elif request["kind"] == "armDrift":
            assert request["destination"] in ("right", "other")
            state["driftTarget"] = request["destination"]
        else:
            raise ValueError("Unknown fixture arrangement: " + request["kind"])
        state["command"] = request["id"]
        publish("arranged-" + request["kind"])
        return True

    if toolkit == "gtk3":
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk, GLib, Gtk

        GLib.set_prgname("computer-use-v5-receiver-fixture")
        GLib.set_application_name("Computer Use v5 Receiver Fixture")

        def observe(editor):
            bounds = editor.get_selection_bounds()
            return dict(
                text=editor.get_text(),
                focused=editor.has_focus(),
                cursor=editor.get_position(),
                selection=list(bounds) if bounds else None,
            )

        def focus_editor(key, activate=False):
            editor = editors[key]
            if activate:
                editor.get_toplevel().present()
            editor.grab_focus()

        def bind(editor, key, name):
            editor.set_text(initial[key])
            editor.get_accessible().set_name(name)
            editors[key] = editor
            editor.connect("changed", lambda _: changed(key))
            editor.connect("activate", lambda _: committed(key))

            def pressed(_, event):
                key_event(key, True, keyval=int(event.keyval), modifiers=int(event.state), nativeTime=int(event.time))
                if event.keyval in (Gdk.KEY_v, Gdk.KEY_V) and event.state & Gdk.ModifierType.CONTROL_MASK:
                    pasted(key, receiverClass=editor.__class__.__name__, receiverName=name, nativeTime=int(event.time))
                return False

            editor.connect("key-press-event", pressed)

            def released(_, event):
                key_event(key, False, keyval=int(event.keyval), modifiers=int(event.state), nativeTime=int(event.time))
                return False

            editor.connect("key-release-event", released)
            return editor

        for letter in ("A", "B"):
            window = Gtk.Window(title=TITLE + letter)
            window.set_default_size(580, 460)
            window.move(30 if letter == "A" else 680, 90)
            layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            layout.set_border_width(24)
            if letter == "A":
                combo = Gtk.ComboBoxText.new_with_entry()
                combo.append_text("choice one")
                combo.append_text("choice two")
                combo.get_accessible().set_name("Destination")
                bind(combo.get_child(), "combo", "Destination editor")
                layout.pack_start(combo, False, False, 0)
                layout.pack_start(bind(Gtk.Entry(), "neighbor", "Neighbor"), False, False, 0)
                group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
                group.get_accessible().set_name("Two editors")
                for key, name in [("left", "Left independent editor"), ("right", "Right independent editor")]:
                    group.pack_start(bind(Gtk.Entry(), key, name), False, False, 0)
                layout.pack_start(group, False, False, 0)
                readonly = bind(Gtk.Entry(), "readonly", "Read-only editor")
                readonly.set_editable(False)
                layout.pack_start(readonly, False, False, 0)
            else:
                layout.pack_start(bind(Gtk.Entry(), "other", "Other window editor"), False, False, 0)
            window.add(layout)
            window.show_all()
            windows.append(window)
        editors["other"].grab_focus()
        publish("ready")
        GLib.timeout_add(25, publish)
        GLib.io_add_watch(sys.stdin, GLib.IO_IN | GLib.IO_HUP, lambda *_: arrange())
        Gtk.main()
    else:
        from PyQt6.QtCore import QEvent, QObject, QSocketNotifier, Qt, QTimer
        from PyQt6.QtWidgets import QApplication, QComboBox, QLineEdit, QVBoxLayout, QWidget

        app = QApplication([])
        app.setApplicationName("Computer Use v5 Receiver Fixture")
        filters = []

        def observe(editor):
            start = editor.selectionStart()
            return dict(
                text=editor.text(),
                focused=editor.hasFocus(),
                cursor=editor.cursorPosition(),
                selection=[start, start + len(editor.selectedText())] if start >= 0 else None,
            )

        def focus_editor(key, activate=False):
            editor = editors[key]
            if activate:
                editor.window().raise_()
                editor.window().activateWindow()
            editor.setFocus(Qt.FocusReason.OtherFocusReason)

        class Filter(QObject):
            def __init__(self, key):
                super().__init__()
                self.key = key

            def eventFilter(self, obj, event):
                if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                    key_event(
                        self.key,
                        event.type() == QEvent.Type.KeyPress,
                        keyval=int(event.key()),
                        text=event.text(),
                        modifiers=event.modifiers().value,
                        nativeTime=int(event.timestamp()),
                        receiverClass=obj.metaObject().className(),
                    )
                if (
                    event.type() == QEvent.Type.KeyPress
                    and event.key() == Qt.Key.Key_V
                    and event.modifiers() & Qt.KeyboardModifier.ControlModifier
                ):
                    pasted(
                        self.key,
                        receiverClass=obj.metaObject().className(),
                        receiverName=obj.accessibleName(),
                        nativeTime=int(event.timestamp()),
                        spontaneous=event.spontaneous(),
                    )
                return False

        def bind(editor, key, name, keyboard_owner=None):
            editor.setText(initial[key])
            editor.setAccessibleName(name)
            editors[key] = editor
            editor.textChanged.connect(lambda _: changed(key))
            editor.returnPressed.connect(lambda: committed(key))
            event_filter = Filter(key)
            editor.installEventFilter(event_filter)
            if keyboard_owner is not None:
                # QComboBox::keyPressEvent calls lineEdit->event(e) directly;
                # that forwarding bypasses the line edit's QObject filter.
                # Observe both native delivery entry points, without handling
                # or modifying the event. The oracle still requires one paste.
                keyboard_owner.installEventFilter(event_filter)
            filters.append(event_filter)
            return editor

        for letter in ("A", "B"):
            window = QWidget()
            window.setWindowTitle(TITLE + letter)
            window.setGeometry(30 if letter == "A" else 680, 90, 580, 460)
            layout = QVBoxLayout(window)
            if letter == "A":
                combo = QComboBox()
                combo.setEditable(True)
                combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
                combo.addItems(["choice one", "choice two"])
                combo.setAccessibleName("Destination")
                bind(combo.lineEdit(), "combo", "Destination editor", keyboard_owner=combo)
                layout.addWidget(combo)
                layout.addWidget(bind(QLineEdit(), "neighbor", "Neighbor"))
                group = QWidget()
                group.setAccessibleName("Two editors")
                nested = QVBoxLayout(group)
                for key, name in [("left", "Left independent editor"), ("right", "Right independent editor")]:
                    nested.addWidget(bind(QLineEdit(), key, name))
                layout.addWidget(group)
                readonly = bind(QLineEdit(), "readonly", "Read-only editor")
                readonly.setReadOnly(True)
                layout.addWidget(readonly)
            else:
                layout.addWidget(bind(QLineEdit(), "other", "Other window editor"))
            window.show()
            windows.append(window)
        editors["other"].setFocus()
        timer = QTimer()
        timer.timeout.connect(publish)
        timer.start(25)
        stdin = QSocketNotifier(sys.stdin.fileno(), QSocketNotifier.Type.Read)

        def ready(*_):
            if not arrange():
                stdin.setEnabled(False)

        stdin.activated.connect(ready)
        QTimer.singleShot(100, lambda: publish("ready"))
        app.exec()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--toolkit", choices=("gtk3", "qt"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    main(args.toolkit, args.output)
