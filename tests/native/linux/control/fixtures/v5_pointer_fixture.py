"""Owned, non-accessible X11 canvas windows for genuine v5 pointer effects.

No input is synthesized here. Mouse effects come exclusively from received X11
ButtonPress/Release/Motion events. Stdin commands only arrange these windows to
establish occlusion, geometry and modal acceptance conditions.
"""

from __future__ import annotations

import json
import os
import select
import signal
import sys
import time
from pathlib import Path

from Xlib import X, Xatom, Xutil, display, protocol


def main():
    path = Path(sys.argv[1])
    d = display.Display()
    screen, root = d.screen(), d.screen().root
    running, command = True, 0
    windows, gc, dragging = {}, {}, {}
    definitions = {
        "a": dict(
            title="Computer Use v5 Pointer A",
            rect=(60, 100, 440, 320),
            background="#e0c090",
            button="#e03020",
            scroll="#20d0d0",
            knob="#e020e0",
        ),
        "b": dict(
            title="Computer Use v5 Pointer B",
            rect=(720, 120, 440, 320),
            background="#80a0e0",
            button="#205080",
            scroll="#306080",
            knob="#407090",
        ),
        "dialog": dict(
            title="",
            rect=(150, 150, 440, 320),
            background="#a0e090",
            button="#309030",
            scroll="#40a050",
            knob="#50b070",
        ),
    }
    state = {key: dict(clicks=0, wheels=0, drags=0, knobX=52, pressed=[], events=[]) for key in definitions}
    sequence = 0

    def atom(name):
        return d.intern_atom(name)

    def activate(key):
        root.send_event(
            protocol.event.ClientMessage(
                window=windows[key], client_type=atom("_NET_ACTIVE_WINDOW"), data=(32, [2, X.CurrentTime, 0, 0, 0])
            ),
            event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask,
        )
        d.flush()

    def rectangles(key):
        return dict(
            button=(20, 30, 100, 70),
            scroll=(160, 30, 240, 100),
            track=(20, 220, 360, 60),
            knob=(state[key]["knobX"] - 12, 230, 24, 40),
        )

    def inside(point, rect):
        return rect[0] <= point[0] < rect[0] + rect[2] and rect[1] <= point[1] < rect[1] + rect[3]

    def paint(key):
        window, colors = windows[key], gc[key]
        window.clear_area(exposures=False)
        for name in ("button", "scroll", "track", "knob"):
            window.fill_rectangle(colors[name], *rectangles(key)[name])
        d.flush()

    def save(reason):
        rows = {}
        for key, window in windows.items():
            geometry, origin = window.get_geometry(), root.translate_coords(window, 0, 0)
            rows[key] = dict(
                xid=window.id,
                title=definitions[key]["title"],
                rect=[origin.x, origin.y, geometry.width, geometry.height],
                mapped=window.get_attributes().map_state == X.IsViewable,
                colors={name: definitions[key][name] for name in ("background", "button", "scroll", "knob")},
                controls=rectangles(key),
                **state[key],
            )
        value = dict(reason=reason, command=command, sequence=sequence, windows=rows)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value))
        temporary.replace(path)

    for key, spec in definitions.items():
        pixel = screen.default_colormap.alloc_named_color(spec["background"]).pixel
        window = root.create_window(
            *spec["rect"],
            0,
            screen.root_depth,
            X.InputOutput,
            X.CopyFromParent,
            background_pixel=pixel,
            event_mask=X.ExposureMask
            | X.StructureNotifyMask
            | X.ButtonPressMask
            | X.ButtonReleaseMask
            | X.PointerMotionMask,
        )
        window.set_wm_name(spec["title"])
        window.set_wm_class("v5-pointer-fixture", "v5-pointer-fixture")
        window.set_wm_normal_hints(
            flags=Xutil.USPosition | Xutil.PSize,
            x=spec["rect"][0],
            y=spec["rect"][1],
            width=spec["rect"][2],
            height=spec["rect"][3],
        )
        window.change_property(atom("_NET_WM_NAME"), atom("UTF8_STRING"), 8, spec["title"].encode())
        window.change_property(atom("_NET_WM_PID"), Xatom.CARDINAL, 32, [os.getpid()])
        if key == "dialog":
            window.set_wm_transient_for(windows["a"])
            window.change_property(atom("_NET_WM_WINDOW_TYPE"), Xatom.ATOM, 32, [atom("_NET_WM_WINDOW_TYPE_DIALOG")])
            window.change_property(atom("_NET_WM_STATE"), Xatom.ATOM, 32, [atom("_NET_WM_STATE_MODAL")])
        windows[key] = window
        gc[key] = {
            name: window.create_gc(foreground=screen.default_colormap.alloc_named_color(color).pixel)
            for name, color in {**{n: spec[n] for n in ("button", "scroll", "knob")}, "track": "#e0d020"}.items()
        }
        if key != "dialog":
            window.map()
    d.sync()
    time.sleep(0.2)
    activate("b")
    d.sync()
    time.sleep(0.1)
    for key in ("a", "b"):
        paint(key)
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
                key = next((k for k, w in windows.items() if getattr(event, "window", None) == w), None)
                if key is None:
                    continue
                if event.type == X.Expose and event.count == 0:
                    paint(key)
                elif event.type in (X.ButtonPress, X.ButtonRelease, X.MotionNotify):
                    sequence += 1
                    button = event.detail if event.type != X.MotionNotify else None
                    name = {X.ButtonPress: "press", X.ButtonRelease: "release", X.MotionNotify: "motion"}[event.type]
                    point = event.event_x, event.event_y
                    row = state[key]
                    row["events"].append(
                        dict(
                            sequence=sequence,
                            kind=name,
                            button=button,
                            x=point[0],
                            y=point[1],
                            rootX=event.root_x,
                            rootY=event.root_y,
                            state=event.state,
                        )
                    )
                    row["events"] = row["events"][-512:]
                    if event.type == X.ButtonPress:
                        if button not in row["pressed"]:
                            row["pressed"].append(button)
                        if button == 1 and inside(point, rectangles(key)["button"]):
                            row["clicks"] += 1
                        if button in (4, 5) and inside(point, rectangles(key)["scroll"]):
                            row["wheels"] += 1 if button == 5 else -1
                        if button == 1 and inside(point, rectangles(key)["knob"]):
                            dragging[key] = point[0] - row["knobX"]
                    elif event.type == X.MotionNotify and key in dragging:
                        row["knobX"] = max(32, min(368, point[0] - dragging[key]))
                        paint(key)
                    elif event.type == X.ButtonRelease:
                        if button in row["pressed"]:
                            row["pressed"].remove(button)
                        if button == 1 and key in dragging:
                            row["knobX"] = max(32, min(368, point[0] - dragging.pop(key)))
                            row["drags"] += 1
                            paint(key)
                    save(name)
            ready, _, _ = select.select([d.fileno(), sys.stdin], [], [], 0.03)
            if sys.stdin not in ready:
                continue
            line = sys.stdin.readline()
            if not line:
                break
            request = json.loads(line)
            command = request["id"]
            kind = request["kind"]
            if kind == "cover":
                origin = root.translate_coords(windows["a"], 0, 0)
                windows["b"].configure(x=max(0, origin.x - 5), y=max(0, origin.y - 5), width=460, height=350)
                activate("b")
            elif kind == "move":
                windows["a"].configure(x=100, y=480, width=480, height=340)
            elif kind == "showModal":
                windows["dialog"].map()
                activate("dialog")
            elif kind == "hideModal":
                windows["dialog"].unmap()
            elif kind == "activate":
                assert request["window"] in windows
                activate(request["window"])
            elif kind == "stop":
                break
            else:
                raise ValueError("Unknown owned fixture command")
            d.sync()
            time.sleep(0.12)
            save(kind)
    finally:
        for key, window in windows.items():
            for resource in gc[key].values():
                resource.free()
            window.destroy()
        d.sync()
        d.close()


if __name__ == "__main__":
    main()
