"""Owned test windows; only the acceptance harness's temporary state is changed."""

import json
import os
import pathlib
import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

output = pathlib.Path(sys.argv[1])
state = dict(a="", b="untouched", notes="", submitted="", clicks=0, slider=20, scroll=0, closed=False)


def save():
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(state))
    os.replace(temporary, output)


windows = []
for name in ("A", "B"):
    window = Gtk.Window(title=f"Computer Use Linux Test {name}")
    window.set_wmclass("computer-use-linux-fixture", "ComputerUseLinuxFixture")
    window.set_default_size(560, 560)
    window.move(40 if name == "A" else 660, 60)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    window.add(box)
    entry = Gtk.Entry()
    entry.get_accessible().set_name(f"Input {name}")
    entry.set_text(state[name.lower()])
    entry.connect("changed", lambda e, key=name.lower(): (state.update({key: e.get_text()}), save()))
    entry.connect("activate", lambda e: (state.update(submitted=e.get_text()), save()))
    box.pack_start(entry, False, False, 0)
    if name == "A":

        def count(*_):
            state.update(clicks=state["clicks"] + 1)
            save()

        button = Gtk.Button(label="Count")
        button.connect("clicked", count)
        box.pack_start(button, False, False, 0)

        def dialog(*_):
            modal = Gtk.Dialog(title="Owned modal", transient_for=windows[0], modal=True)
            modal.add_button("Dismiss", Gtk.ResponseType.CLOSE)
            modal.get_content_area().add(Gtk.Label(label="Handle the dialog first"))
            modal.show_all()
            modal.run()  # Exercise the nested loop used by real file choosers.
            modal.destroy()

        button = Gtk.Button(label="Open dialog")
        button.connect("clicked", dialog)
        box.pack_start(button, False, False, 0)
        slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        slider.get_accessible().set_name("Level")
        slider.set_value(20)
        slider.connect("value-changed", lambda s: (state.update(slider=s.get_value()), save()))
        box.pack_start(slider, False, False, 0)
        notes = Gtk.TextView()
        notes.get_accessible().set_name("Notes")
        notes.set_size_request(-1, 70)
        notes.get_buffer().connect(
            "changed", lambda b: (state.update(notes=b.get_text(b.get_start_iter(), b.get_end_iter(), True)), save())
        )
        box.pack_start(notes, False, False, 0)
        copy = Gtk.Button(label="Copy marker")

        def copy_marker(*_):
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text("original clipboard", -1)
            clipboard.store()

        copy.connect("clicked", copy_marker)
        box.pack_start(copy, False, False, 0)
        scroller = Gtk.ScrolledWindow()
        scroller.get_accessible().set_name("Rows")
        scroller.get_vadjustment().connect("value-changed", lambda a: (state.update(scroll=a.get_value()), save()))
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for i in range(100):
            rows.add(Gtk.Label(label=f"Row {i}"))
        scroller.add(rows)
        box.pack_start(scroller, True, True, 0)
        close = Gtk.Button(label="Close test A")
        close.connect("clicked", lambda _: windows[0].destroy())
        box.pack_start(close, False, False, 0)
        window.connect("destroy", lambda _: (state.update(closed=True), save()))
    window.show_all()
    windows.append(window)
save()


def geometry():
    area = slider.get_range_rect()
    first, last = slider.get_slider_range()
    x, y = slider.translate_coordinates(windows[0], 0, 0)
    state["drag"] = [
        x + (first + last) / 2,
        y + area.y + area.height / 2,
        x + area.x + area.width * 0.8,
        y + area.y + area.height / 2,
    ]
    save()


GLib.idle_add(geometry)
Gtk.main()
