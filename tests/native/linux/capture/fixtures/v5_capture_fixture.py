"""Owned native X11 windows without AT-SPI, including an untitled modal.

Commands on stdin mutate only these fixture windows; stdout acknowledges each
command. There is no input injection, application matching or user file access.
"""

import json
import os
import select
import signal
import sys
import time
from pathlib import Path

from Xlib import X, Xatom, display, protocol


def main():
    state_path = Path(sys.argv[1])
    d = display.Display()
    screen, windows, running = d.screen(), {}, True
    root = screen.root
    definitions = {
        "a": ("Computer Use v5 Capture A", (40, 60, 360, 240), "#e04020"),
        "b": ("Computer Use v5 Capture B", (200, 160, 300, 220), "#2060e0"),
        "dialog": ("", (100, 100, 120, 80), "#30b050"),
    }

    def atom(name):
        return d.intern_atom(name)

    for key, (title, rect, color) in definitions.items():
        pixel = screen.default_colormap.alloc_named_color(color).pixel
        w = root.create_window(
            *rect,
            0,
            screen.root_depth,
            X.InputOutput,
            X.CopyFromParent,
            background_pixel=pixel,
            event_mask=X.ExposureMask | X.StructureNotifyMask,
        )
        w.set_wm_name(title)
        w.set_wm_class("v5-capture-fixture", "v5-capture-fixture")
        w.change_property(atom("_NET_WM_NAME"), atom("UTF8_STRING"), 8, title.encode())
        w.change_property(atom("_NET_WM_PID"), Xatom.CARDINAL, 32, [os.getpid()])
        if key == "dialog":
            w.set_wm_transient_for(windows["a"])
            w.change_property(atom("_NET_WM_WINDOW_TYPE"), Xatom.ATOM, 32, [atom("_NET_WM_WINDOW_TYPE_DIALOG")])
            w.change_property(atom("_NET_WM_STATE"), Xatom.ATOM, 32, [atom("_NET_WM_STATE_MODAL")])
        windows[key] = w
        w.map()
    d.sync()
    time.sleep(0.15)

    def save(event):
        rows = {}
        for key, w in windows.items():
            geometry, point = w.get_geometry(), root.translate_coords(w, 0, 0)
            rows[key] = dict(
                xid=w.id,
                title=definitions[key][0],
                color=definitions[key][2],
                rect=[point.x, point.y, geometry.width, geometry.height],
            )
        value = dict(event=event, windows=rows)
        temporary = state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value))
        temporary.replace(state_path)
        print(json.dumps(value), flush=True)

    # Fixture setup establishes B as the user's foreground before monitoring.
    root.send_event(
        protocol.event.ClientMessage(
            window=windows["b"], client_type=atom("_NET_ACTIVE_WINDOW"), data=(32, [2, X.CurrentTime, 0, 0, 0])
        ),
        event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask,
    )
    d.sync()
    time.sleep(0.1)
    save("ready")

    def stop(*_):
        nonlocal running
        running = False

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)
    try:
        while running:
            while d.pending_events():
                event = d.next_event()
                if event.type == X.Expose and event.count == 0:
                    event.window.clear_area(exposures=False)
                    d.flush()
            ready, _, _ = select.select([d.fileno(), sys.stdin], [], [], 0.05)
            if sys.stdin in ready:
                line = sys.stdin.readline()
                if not line:
                    break
                command = json.loads(line)
                if command["kind"] == "move":
                    windows["a"].configure(x=70, y=90, width=420, height=210)
                elif command["kind"] == "close":
                    windows.pop(command["window"]).destroy()
                elif command["kind"] == "stop":
                    break
                else:
                    raise ValueError("Unsupported fixture command")
                d.sync()
                time.sleep(0.05)
                save(command["kind"])
    finally:
        for w in windows.values():
            w.destroy()
        d.sync()
        d.close()


if __name__ == "__main__":
    main()
