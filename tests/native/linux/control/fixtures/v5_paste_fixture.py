"""Owned GTK/Qt multiline editors and a real Qt clipboard source for v5 acceptance.

The optional clipboard process is a data owner, never a clipboard manager. A
separately installed desktop manager must handle SAVE_TARGETS and persistence.
"""

import base64
import json
import sys
from pathlib import Path

kind, output = sys.argv[1], Path(sys.argv[2])
TITLE = "Computer Use v5 Paste "
INITIAL = "left|right"


def publish(value):
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False))
    temporary.replace(output)


if kind == "clipboard":
    from PyQt6.QtCore import QByteArray, QMimeData, QTimer
    from PyQt6.QtWidgets import QApplication

    app = QApplication([])
    app.setApplicationName("Computer Use Clipboard Seed")
    mime = QMimeData()
    mime.setText("Original clipboard 中文 😀")
    mime.setHtml("<p><b>Original</b> clipboard 中文 😀</p>")
    mime.setData("application/x-computer-use-v5-proof", QByteArray(b"\x00binary\xff\x80\x00proof"))
    expected = {name: base64.b64encode(bytes(mime.data(name))).decode() for name in mime.formats()}
    app.clipboard().setMimeData(mime)
    QTimer.singleShot(100, lambda: publish(dict(formats=expected, ownsClipboard=app.clipboard().ownsClipboard())))
    app.exec()
else:
    state = dict(a=INITIAL, b="untouched", changes=dict(a=0, b=0), pasteKeys=dict(a=0, b=0))

    def changed(text, key):
        state[key] = text
        state["changes"][key] += 1
        publish(state)

    def paste_key(key):
        state["pasteKeys"][key] += 1
        publish(state)

    if kind == "gtk3":
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk, GLib, Gtk

        GLib.set_prgname("computer-use-v5-paste-fixture")
        GLib.set_application_name("Computer Use v5 Paste Fixture")
        windows = []
        for letter in ("A", "B"):
            window = Gtk.Window(title=TITLE + letter)
            window.set_default_size(520, 300)
            text = Gtk.TextView()
            text.set_accepts_tab(True)
            text.get_accessible().set_name("Body " + letter)
            buffer = text.get_buffer()
            buffer.set_text(state[letter.lower()])

            def buffer_changed(buffer, key):
                changed(buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True), key)

            buffer.connect("changed", buffer_changed, letter.lower())

            def pressed(_, event, key):
                if event.keyval in (Gdk.KEY_v, Gdk.KEY_V) and event.state & Gdk.ModifierType.CONTROL_MASK:
                    paste_key(key)
                return False

            text.connect("key-press-event", pressed, letter.lower())
            window.add(text)
            window.show_all()
            text.grab_focus()
            if letter == "A":
                buffer.select_range(buffer.get_iter_at_offset(4), buffer.get_iter_at_offset(5))
            windows.append(window)
        publish(state)
        Gtk.main()
    elif kind == "qt":
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QTextCursor
        from PyQt6.QtWidgets import QApplication, QPlainTextEdit, QVBoxLayout, QWidget

        app = QApplication([])
        app.setApplicationName("Computer Use v5 Paste Fixture")

        class Editor(QPlainTextEdit):
            def __init__(self, text, key):
                super().__init__(text)
                self.key = key

            def keyPressEvent(self, event):
                if event.key() == Qt.Key.Key_V and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    paste_key(self.key)
                super().keyPressEvent(event)

        windows = []
        for letter in ("A", "B"):
            window = QWidget()
            window.setWindowTitle(TITLE + letter)
            window.resize(520, 300)
            layout = QVBoxLayout(window)
            text = Editor(state[letter.lower()], letter.lower())
            text.setAccessibleName("Body " + letter)
            text.textChanged.connect(lambda text=text, key=letter.lower(): changed(text.toPlainText(), key))
            layout.addWidget(text)
            window.show()
            text.setFocus()
            if letter == "A":
                cursor = text.textCursor()
                cursor.setPosition(4)
                cursor.setPosition(5, QTextCursor.MoveMode.KeepAnchor)
                text.setTextCursor(cursor)
            windows.append(window)
        publish(state)
        app.exec()
    else:
        raise ValueError(kind)
