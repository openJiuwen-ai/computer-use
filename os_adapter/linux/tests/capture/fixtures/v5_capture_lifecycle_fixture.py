"""Uncomposited X11 clients whose real Expose handling has a controlled delay.

Only this fixture knows its expected colors. It never redirects or captures a
window, synthesizes input, or redraws on a timer unrelated to a native Expose.
Stdin changes only its own content and window lifecycle.
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

APP = "v5-capture-lifecycle-fixture"


def main():
    path = Path(sys.argv[1])
    d = display.Display()
    screen, root = d.screen(), d.screen().root
    specs = {
        "a": dict(title="v5 Capture Lifecycle A", rect=(100, 120, 440, 320), color="#d0b080", delayMs=200, solid=False),
        "solid": dict(
            title="v5 Capture Lifecycle Solid", rect=(110, 130, 260, 180), color="#355b81", delayMs=200, solid=True
        ),
        "late": dict(
            title="v5 Capture Lifecycle Late", rect=(120, 140, 320, 220), color="#c0a090", delayMs=2500, solid=False
        ),
        "b": dict(
            title="v5 Capture Lifecycle Cover B", rect=(40, 60, 620, 480), color="#7080b0", delayMs=0, solid=True
        ),
    }
    windows, brushes, pending = {}, {}, {}
    state = {
        key: dict(
            revision=0,
            paintedRevision=-1,
            paints=0,
            exposes=0,
            lastExpose=None,
            lastPaint=None,
            requestedDelayMs=spec["delayMs"],
        )
        for key, spec in specs.items()
    }
    running, command = True, 0

    def atom(name):
        return d.intern_atom(name)

    def activate_b():
        root.send_event(
            protocol.event.ClientMessage(
                window=windows["b"], client_type=atom("_NET_ACTIVE_WINDOW"), data=(32, [2, X.CurrentTime, 0, 0, 0])
            ),
            event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask,
        )
        d.flush()

    def colors(key):
        return dict(
            background=specs[key]["color"],
            first="#e03020" if state[key]["revision"] % 2 == 0 else "#9020d0",
            second="#20b050",
            bar="#2080e0",
        )

    def regions(key):
        width, height = windows[key].get_geometry().width, windows[key].get_geometry().height
        if specs[key]["solid"]:
            return []
        return [
            ("first", (16, 24, 90, 70)),
            ("second", (width - 110, 24, 90, 70)),
            ("bar", (16, height - 50, width - 32, 26)),
        ]

    def paint(key, reason):
        w = windows[key]
        # The entire client painting batch is flushed together. This is a
        # normal delayed Expose redraw, not an accessibility/capture hook.
        w.clear_area(exposures=False)
        palette = colors(key)
        if specs[key]["solid"]:
            g = w.get_geometry()
            w.fill_rectangle(brushes[key][palette["background"]], 0, 0, g.width, g.height)
        for name, rectangle in regions(key):
            w.fill_rectangle(brushes[key][palette[name]], *rectangle)
        d.flush()
        row = state[key]
        row.update(
            paints=row["paints"] + 1, paintedRevision=row["revision"], lastPaint=time.monotonic(), paintReason=reason
        )

    def save(reason):
        rows = {}
        for key, w in windows.items():
            geometry, origin = w.get_geometry(), root.translate_coords(w, 0, 0)
            rows[key] = dict(
                xid=w.id,
                title=specs[key]["title"],
                rect=[origin.x, origin.y, geometry.width, geometry.height],
                mapped=w.get_attributes().map_state == X.IsViewable,
                solid=specs[key]["solid"],
                colors=colors(key),
                regions=regions(key),
                pendingPaint=pending.get(key),
                **state[key],
            )
        value = dict(reason=reason, command=command, pid=os.getpid(), time=time.monotonic(), windows=rows)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value))
        temporary.replace(path)

    def stop(*_):
        nonlocal running
        running = False

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)
    try:
        for key, spec in specs.items():
            pixel = screen.default_colormap.alloc_named_color(spec["color"]).pixel
            w = root.create_window(
                *spec["rect"],
                0,
                screen.root_depth,
                X.InputOutput,
                X.CopyFromParent,
                background_pixel=pixel,
                event_mask=X.ExposureMask | X.StructureNotifyMask,
            )
            w.set_wm_name(spec["title"])
            w.set_wm_class(APP, APP)
            w.set_wm_normal_hints(
                flags=Xutil.USPosition | Xutil.PSize,
                x=spec["rect"][0],
                y=spec["rect"][1],
                width=spec["rect"][2],
                height=spec["rect"][3],
            )
            w.change_property(atom("_NET_WM_NAME"), atom("UTF8_STRING"), 8, spec["title"].encode())
            w.change_property(atom("_NET_WM_PID"), Xatom.CARDINAL, 32, [os.getpid()])
            windows[key] = w
            brushes[key] = {
                color: w.create_gc(foreground=screen.default_colormap.alloc_named_color(color).pixel)
                for color in {spec["color"], "#e03020", "#9020d0", "#20b050", "#2080e0"}
            }
            w.map()
        d.sync()
        time.sleep(0.2)
        # Finish ordinary initial drawing before the driver exists. B covers
        # every test client; subsequent redirect-induced Expose is delayed.
        while d.pending_events():
            d.next_event()
        for key in windows:
            paint(key, "initial")
        activate_b()
        d.sync()
        time.sleep(0.1)
        save("ready")
        while running:
            dirty = False
            while d.pending_events():
                event = d.next_event()
                key = next((k for k, w in windows.items() if getattr(event, "window", None) == w), None)
                if key is not None and event.type == X.Expose and event.count == 0:
                    row = state[key]
                    row["exposes"] += 1
                    row["lastExpose"] = time.monotonic()
                    pending.setdefault(key, row["lastExpose"] + row["requestedDelayMs"] / 1000)
                    dirty = True
            for key, due in list(pending.items()):
                if time.monotonic() >= due:
                    pending.pop(key)
                    paint(key, "expose")
                    dirty = True
            if dirty:
                save("paint-state")
            ready, _, _ = select.select([d.fileno(), sys.stdin], [], [], 0.01)
            if sys.stdin not in ready:
                continue
            line = sys.stdin.readline()
            if not line:
                break
            request = json.loads(line)
            command, kind = request["id"], request["kind"]
            key = request.get("window", "a")
            assert key in windows
            if kind == "content":
                state[key]["revision"] += 1
                paint(key, "content-change")
            elif kind == "resize":
                state[key]["revision"] += 1
                windows[key].configure(width=request["width"], height=request["height"])
            elif kind == "remap":
                pending.pop(key, None)
                state[key]["revision"] += 1
                windows[key].unmap()
                d.sync()
                time.sleep(0.06)
                windows[key].map()
                windows["b"].configure(stack_mode=X.Above)
                activate_b()
            elif kind == "delay":
                state[key]["requestedDelayMs"] = request["milliseconds"]
            elif kind == "close":
                windows[key].destroy()
                d.sync()
                for gc in brushes.pop(key).values():
                    gc.free()
                windows.pop(key)
                pending.pop(key, None)
            elif kind == "stop":
                break
            else:
                raise ValueError("Unsupported owned fixture command: " + kind)
            d.sync()
            save(kind)
    finally:
        for key, w in windows.items():
            for gc in brushes[key].values():
                gc.free()
            w.destroy()
        d.sync()
        d.close()


if __name__ == "__main__":
    main()
