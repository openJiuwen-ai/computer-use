"""One GTK window with two distinct menu bars exposing identical command names."""

import argparse
import json
import os
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    GLib.set_prgname("computer-use-v5-menu-sources")
    GLib.set_application_name("Computer Use Menu Sources")
    state = dict(
        pid=os.getpid(),
        birth=Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19],
        counts=[0, 0],
        ready=False,
        started=time.time(),
    )

    def publish():
        temporary = args.output / "state.tmp"
        temporary.write_text(json.dumps(state))
        temporary.replace(args.output / "state.json")

    window = Gtk.Window(title="Distinct native menu sources")
    window.set_default_size(640, 300)
    window.connect("destroy", Gtk.main_quit)
    layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
    label = Gtk.Label(label="First: 0; Second: 0")
    for index in range(2):
        bar = Gtk.MenuBar()
        item = Gtk.MenuItem(label="File")
        submenu = Gtk.Menu()
        command = Gtk.MenuItem(label="Mark")

        def activate(_, index=index):
            state["counts"][index] += 1
            label.set_text(f"First: {state['counts'][0]}; Second: {state['counts'][1]}")
            publish()

        command.connect("activate", activate)
        submenu.append(command)
        item.set_submenu(submenu)
        bar.append(item)
        layout.pack_start(bar, False, False, 0)
    layout.pack_start(label, True, True, 0)
    window.add(layout)
    window.show_all()

    def ready():
        state["ready"] = True
        publish()
        return False

    GLib.idle_add(ready)
    Gtk.main()
