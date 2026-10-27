"""Owned no-AX Xlib controls with a real ICCCM clipboard selection consumer.

Only received X11 events change text, selection and commits. Stdin can arrange
focus, close or recreate these two windows; it cannot synthesize requested keys.
Core X fonts may replace Unicode glyphs. The exact value oracle is the actual
UTF8_STRING property consumed by the target field, recorded in its event journal.
"""

from __future__ import annotations

import json
import os
import select
import signal
import sys
import time
from copy import deepcopy
from pathlib import Path

from Xlib import XK, X, Xatom, Xutil, display, protocol

DEFINITIONS = {
    "a": dict(
        title="Computer Use v5 No AX Paste A",
        rect=(70, 110, 460, 250),
        background="#ead6ad",
        colors=("#e03020", "#20d0d0"),
    ),
    "b": dict(
        title="Computer Use v5 No AX Paste B",
        rect=(710, 110, 460, 250),
        background="#adc6ea",
        colors=("#7040d0", "#30b040"),
    ),
}
FIELD_RECTS = ((30, 50, 390, 58), (30, 140, 390, 58))


def main(path):
    d = display.Display()
    screen, root = d.screen(), d.screen().root
    windows, fields, graphics, rows = {}, {}, {}, {}
    pending_pastes = {}
    paste_property = d.intern_atom("_V5_NO_AX_PASTE_DATA")
    clipboard_atom = d.intern_atom("CLIPBOARD")
    utf8_atom = d.intern_atom("UTF8_STRING")
    generations = dict(a=0, b=0)
    retired, held, events = [], set(), []
    running, sequence, command = True, 0, 0
    active_field = dict(a=0, b=0)

    def atom(name):
        return d.intern_atom(name)

    def paint(key):
        window = windows[key]
        window.clear_area(exposures=False)
        window.draw_text(
            graphics[key]["label"],
            30,
            25,
            (key.upper() + " / Xlib no-AX paste fixture / generation " + str(generations[key])).encode(),
        )
        for index, field in enumerate(fields[key]):
            field.clear_area(exposures=False)
            row = rows[key]["fields"][index]
            label = "%s%d: %s%s" % (key.upper(), index + 1, row["text"], " [selected]" if row["selected"] else "")
            field.draw_text(graphics[key]["label"], 14, 33, label.encode("ascii", "replace"))
        d.flush()

    def focus(key, index):
        active_field[key] = index
        fields[key][index].set_input_focus(X.RevertToParent, X.CurrentTime)
        d.flush()

    def activate(key):
        root.send_event(
            protocol.event.ClientMessage(
                window=windows[key], client_type=atom("_NET_ACTIVE_WINDOW"), data=(32, [2, X.CurrentTime, 0, 0, 0])
            ),
            event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask,
        )
        d.sync()
        time.sleep(0.06)
        focus(key, active_field[key])

    def create(key):
        generations[key] += 1
        spec = DEFINITIONS[key]
        color = screen.default_colormap.alloc_named_color(spec["background"]).pixel
        window = root.create_window(
            *spec["rect"],
            0,
            screen.root_depth,
            X.InputOutput,
            X.CopyFromParent,
            background_pixel=color,
            event_mask=X.ExposureMask | X.StructureNotifyMask | X.FocusChangeMask | X.KeyPressMask | X.KeyReleaseMask,
        )
        window.set_wm_name(spec["title"])
        window.set_wm_class("v5-no-ax-paste-fixture", "v5-no-ax-paste-fixture")
        window.set_wm_hints(input=True, flags=Xutil.InputHint)
        window.set_wm_normal_hints(
            flags=Xutil.USPosition | Xutil.PSize,
            x=spec["rect"][0],
            y=spec["rect"][1],
            width=spec["rect"][2],
            height=spec["rect"][3],
        )
        window.change_property(atom("_NET_WM_NAME"), atom("UTF8_STRING"), 8, spec["title"].encode())
        window.change_property(atom("_NET_WM_PID"), Xatom.CARDINAL, 32, [os.getpid()])
        windows[key], fields[key] = window, []
        graphics[key] = {"label": window.create_gc(foreground=screen.black_pixel)}
        rows[key] = dict(
            generation=generations[key],
            xid=window.id,
            title=spec["title"],
            clicks=0,
            keyPresses=0,
            keyReleases=0,
            fields=[
                dict(
                    text=key.upper() + str(i + 1) + " seed",
                    selected=False,
                    changes=0,
                    commits=[],
                    focusEvents=0,
                    pasteKeys=0,
                    pasteRequests=0,
                    pastes=0,
                    pasteValues=[],
                    pasteErrors=[],
                )
                for i in range(2)
            ],
        )
        for index, rect in enumerate(FIELD_RECTS):
            field = window.create_window(
                *rect,
                2,
                screen.root_depth,
                X.InputOutput,
                X.CopyFromParent,
                background_pixel=screen.default_colormap.alloc_named_color(spec["colors"][index]).pixel,
                border_pixel=screen.black_pixel,
                event_mask=X.ExposureMask
                | X.StructureNotifyMask
                | X.FocusChangeMask
                | X.KeyPressMask
                | X.KeyReleaseMask
                | X.ButtonPressMask
                | X.ButtonReleaseMask
                | X.PropertyChangeMask,
            )
            fields[key].append(field)
            field.map()
        window.map()
        active_field[key] = 0
        d.sync()

    def close(key):
        retired.append(deepcopy(rows[key]))
        for resource in graphics.pop(key).values():
            resource.free()
        windows.pop(key).destroy()
        fields.pop(key)
        rows.pop(key)
        d.sync()

    def save(reason):
        result = deepcopy(rows)
        for key, window in windows.items():
            geometry, origin = window.get_geometry(), root.translate_coords(window, 0, 0)
            result[key].update(
                rect=[origin.x, origin.y, geometry.width, geometry.height],
                mapped=window.get_attributes().map_state == X.IsViewable,
                fieldXids=[field.id for field in fields[key]],
                controls=FIELD_RECTS,
                colors=DEFINITIONS[key]["colors"],
                activeField=active_field[key],
            )
        actual_focus = d.get_input_focus().focus
        actual_focus = actual_focus.id if hasattr(actual_focus, "id") else actual_focus
        value = dict(
            pid=os.getpid(),
            processStart=Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19],
            reason=reason,
            command=command,
            sequence=sequence,
            windows=result,
            retired=retired,
            held=sorted(held),
            focus=actual_focus,
            events=events[-1024:],
        )
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value))
        temporary.replace(path)

    def identify(window):
        for key, children in fields.items():
            for index, field in enumerate(children):
                if window == field:
                    return key, index
            if window == windows[key]:
                return key, None
        return None, None

    def received_key(key, index, event):
        nonlocal sequence
        sequence += 1
        pressed = event.type == X.KeyPress
        symbol = d.keycode_to_keysym(event.detail, 1 if event.state & X.ShiftMask else 0)
        name = XK.keysym_to_string(symbol) or str(symbol)
        row = rows[key]
        row["keyPresses" if pressed else "keyReleases"] += 1
        events.append(
            dict(
                sequence=sequence,
                kind="keyDown" if pressed else "keyUp",
                window=key,
                generation=generations[key],
                field=index,
                keycode=event.detail,
                symbol=symbol,
                name=name,
                state=event.state,
                serverTime=event.time,
            )
        )
        if pressed:
            held.add(event.detail)
        else:
            held.discard(event.detail)
        if not pressed or index is None:
            return
        item = row["fields"][index]
        if symbol in (XK.XK_Tab, 0xFE20):
            focus(key, (index - 1 if event.state & X.ShiftMask else index + 1) % 2)
        elif symbol in (XK.XK_Return, XK.XK_KP_Enter):
            item["commits"].append(item["text"])
        elif event.state & X.ControlMask and symbol in (XK.XK_v, XK.XK_V):
            item["pasteKeys"] += 1
            requestor = fields[key][index]
            if requestor.id in pending_pastes:
                item["pasteErrors"].append("A previous selection conversion is still pending")
            else:
                pending_pastes[requestor.id] = (key, index, generations[key], event.time)
                requestor.delete_property(paste_property)
                requestor.convert_selection(clipboard_atom, utf8_atom, paste_property, event.time)
                d.flush()
                item["pasteRequests"] += 1
                events.append(
                    dict(
                        sequence=sequence,
                        kind="pasteRequested",
                        window=key,
                        field=index,
                        requestor=requestor.id,
                        target="UTF8_STRING",
                        serverTime=event.time,
                    )
                )
        elif event.state & X.ControlMask and symbol in (XK.XK_a, XK.XK_A):
            item["selected"] = True
        elif symbol == XK.XK_BackSpace:
            item["text"] = "" if item["selected"] else item["text"][:-1]
            item["selected"] = False
            item["changes"] += 1
        elif 32 <= symbol <= 126 and not event.state & (X.ControlMask | X.Mod1Mask | X.Mod4Mask):
            item["text"] = ("" if item["selected"] else item["text"]) + chr(symbol)
            item["selected"] = False
            item["changes"] += 1
        paint(key)

    def selection_received(event):
        nonlocal sequence
        requestor_id = event.requestor.id if hasattr(event.requestor, "id") else event.requestor
        pending = pending_pastes.get(requestor_id)
        if pending is None or event.selection != clipboard_atom or event.target != utf8_atom:
            return
        key, index, generation, timestamp = pending
        if event.time != timestamp:
            return
        pending_pastes.pop(requestor_id)
        sequence += 1
        if key not in rows or generations[key] != generation:
            return
        item, requestor = rows[key]["fields"][index], fields[key][index]
        try:
            if event.property != paste_property:
                raise ValueError("Clipboard owner refused the actual UTF8_STRING request")
            value = requestor.get_full_property(paste_property, X.AnyPropertyType)
            if value is None or value.format != 8 or value.property_type != utf8_atom:
                raise ValueError("Unexpected selection encoding or incremental payload")
            data = bytes(value.value)
            text = data.decode("utf-8", errors="strict")
            item["text"] = ("" if item["selected"] else item["text"]) + text
            item["selected"] = False
            item["changes"] += 1
            item["pastes"] += 1
            item["pasteValues"].append(text)
            events.append(
                dict(
                    sequence=sequence,
                    kind="pasteConsumed",
                    window=key,
                    field=index,
                    requestor=requestor.id,
                    target="UTF8_STRING",
                    bytes=len(data),
                    text=text,
                    serverTime=event.time,
                )
            )
            # ICCCM consumption acknowledgement. Production NativeClipboard
            # observes PropertyNewValue -> PropertyDelete independently.
            requestor.delete_property(paste_property)
            d.flush()
            paint(key)
        except Exception as error:
            item["pasteErrors"].append(str(error))
            events.append(dict(sequence=sequence, kind="pasteFailed", window=key, field=index, error=str(error)))
        save("selection")

    def stop(*_):
        nonlocal running
        running = False

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)
    try:
        for key in DEFINITIONS:
            create(key)
        time.sleep(0.2)
        activate("b")
        for key in windows:
            paint(key)
        save("ready")
        while running:
            while d.pending_events():
                event = d.next_event()
                if event.type == X.SelectionNotify:
                    selection_received(event)
                    continue
                key, index = identify(getattr(event, "window", None))
                if key is None:
                    continue
                if event.type == X.Expose and event.count == 0:
                    paint(key)
                elif event.type in (X.KeyPress, X.KeyRelease):
                    received_key(key, index, event)
                    save("key")
                elif event.type in (X.ButtonPress, X.ButtonRelease):
                    sequence += 1
                    events.append(
                        dict(
                            sequence=sequence,
                            kind="mouseDown" if event.type == X.ButtonPress else "mouseUp",
                            window=key,
                            generation=generations[key],
                            field=index,
                            button=event.detail,
                            x=event.event_x,
                            y=event.event_y,
                            serverTime=event.time,
                        )
                    )
                    if event.type == X.ButtonPress and event.detail == 1 and index is not None:
                        rows[key]["clicks"] += 1
                        focus(key, index)
                    save("mouse")
                elif event.type in (X.FocusIn, X.FocusOut):
                    if event.type == X.FocusIn and index is not None:
                        active_field[key] = index
                        rows[key]["fields"][index]["focusEvents"] += 1
                    save("focus")
            ready, _, _ = select.select([d.fileno(), sys.stdin], [], [], 0.02)
            if sys.stdin not in ready:
                continue
            line = sys.stdin.readline()
            if not line:
                break
            request = json.loads(line)
            command, kind = request["id"], request["kind"]
            if kind == "activate":
                activate(request["window"])
            elif kind == "close":
                close(request["window"])
            elif kind == "recreate":
                key = request["window"]
                assert key not in windows
                create(key)
                time.sleep(0.12)
                activate(key)
            elif kind == "stop":
                break
            else:
                raise ValueError("Unknown fixture arrangement")
            d.sync()
            time.sleep(0.06)
            save(kind)
    finally:
        for key in list(windows):
            close(key)
        d.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]))
