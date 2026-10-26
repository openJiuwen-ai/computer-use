"""Two working windows; closing a modal returns focus to its original editor."""

import argparse
import json
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk


def main(output):
    output.mkdir(parents=True, exist_ok=True)
    GLib.set_prgname("computer-use-v5-modal-context")
    GLib.set_application_name("Computer Use Modal Context")
    state = dict(ready=False, values=dict(A="", B=""), activated=dict(A=0, B=0), dialogs=0)

    def publish():
        temporary = output / "state.tmp"
        temporary.write_text(json.dumps(state))
        temporary.replace(output / "state.json")

    windows = []
    for index, letter in enumerate(("A", "B")):
        window = Gtk.Window(title="Keyboard Root " + letter)
        window.set_default_size(420, 240)
        window.move(80 + index * 500, 80)
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        entry = Gtk.Entry()
        entry.get_accessible().set_name("Editor " + letter)
        button = Gtk.Button(label="Open dialog")

        def changed(widget, letter=letter):
            state["values"][letter] = widget.get_text()
            publish()

        def activated(_, letter=letter):
            state["activated"][letter] += 1
            publish()

        def open_dialog(_, window=window, entry=entry, letter=letter):
            dialog = Gtk.Dialog(title="Modal " + letter, transient_for=window, modal=True)
            dialog.add_button("Close dialog", Gtk.ResponseType.CLOSE)
            dialog.get_content_area().add(Gtk.Label(label="Close to return to Editor " + letter))
            state["dialogs"] += 1
            publish()

            def close(*_):
                dialog.destroy()
                state["dialogs"] -= 1
                entry.grab_focus()
                entry.select_region(-1, -1)
                publish()

            dialog.connect("response", close)
            dialog.show_all()

        entry.connect("changed", changed)
        entry.connect("activate", activated)
        button.connect("clicked", open_dialog)
        layout.pack_start(entry, False, False, 0)
        layout.pack_start(button, False, False, 0)
        window.add(layout)
        window.show_all()
        windows.append(window)

    def ready():
        state["ready"] = True
        publish()
        return False

    GLib.idle_add(ready)
    Gtk.main()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output)
