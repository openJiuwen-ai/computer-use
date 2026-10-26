from __future__ import annotations

import base64
import io
import os
import time

from computer_use_linux.contracts.protocol import Failure, finite, integer, now, require, token
from computer_use_linux.objects.registry import Window

"X11 window discovery, Composite capture and explicit XTest input."


class X11:
    session_type = "x11"
    can_capture = True
    notes = []

    def __init__(self, mode, retire):
        if os.environ.get("XDG_SESSION_TYPE") == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
            raise Failure(
                "This backend currently requires an X11 desktop; XWayland is not a complete desktop route.",
                "mode_unsupported",
                next="Use an X11 desktop session or an isolated X11 VM for this version.",
            )
        if not os.environ.get("DISPLAY"):
            raise Failure(
                "DISPLAY is missing. Connect to the logged-in graphical user's X11 session.", "observation_unavailable"
            )
        from PIL import Image
        from Xlib import XK, X, display, protocol

        self.X, self.XK, self.events, self.Image = X, XK, protocol.event, Image
        self.d = display.Display()
        self.screen = self.d.screen()
        self.root = self.screen.root
        self.mode, self.retire = mode, retire
        self.keys, self.buttons = set(), set()
        self.watched = set()
        self.window_captures = None
        self.assist = os.environ.get("COMPUTER_USE_FOREGROUND_ASSIST") == "1"
        self.can_input = self.d.has_extension("XTEST")
        self.root.change_attributes(event_mask=X.SubstructureNotifyMask)
        self.d.sync()

    def atom(self, name):
        return self.d.intern_atom(name)

    def prop(self, window, name, default=None):
        value = window.get_full_property(self.atom(name), self.X.AnyPropertyType)
        return default if value is None else value.value

    def native(self, xid):
        return self.d.create_resource_object("window", xid)

    def pump(self):
        for _ in range(min(self.d.pending_events(), 1024)):
            event = self.d.next_event()
            if getattr(self, "window_captures", None) is not None:
                self.window_captures.on_event(event)
            if event.type == self.X.DestroyNotify:
                self.retire(event.window.id)
                self.watched.discard(event.window.id)
            elif event.type == self.X.MappingNotify:
                self.d.refresh_keyboard_mapping(event)

    @staticmethod
    def process_start(pid):
        try:
            with open(f"/proc/{pid}/stat") as stream:
                return stream.read().rsplit(")", 1)[1].split()[19]
        except (OSError, IndexError):
            return ""

    def info(self, xid):
        w = self.native(xid)
        attrs, geometry = w.get_attributes(), w.get_geometry()
        translated = self.root.translate_coords(w, 0, 0)
        title = self.prop(w, "_NET_WM_NAME")
        if isinstance(title, bytes):
            title = title.decode("utf-8", "replace")
        else:
            title = w.get_wm_name() or ""
        wmclass = w.get_wm_class() or ()
        pid_data = self.prop(w, "_NET_WM_PID", [])
        pid = int(pid_data[0]) if (len(pid_data) > 0) else 0
        transient = w.get_wm_transient_for()
        states = self.prop(w, "_NET_WM_STATE", [])
        rect = (translated.x, translated.y, geometry.width, geometry.height)
        # AT-SPI top-level Component extents may include WM decorations, whereas
        # Composite pixels and coordinates use the client window. Retain both.
        extents = self.prop(w, "_NET_FRAME_EXTENTS", [])
        frame = None
        if len(extents) == 4 and all(0 <= int(v) <= 65536 for v in extents):
            left, right, top, bottom = map(int, extents)
            frame = (rect[0] - left, rect[1] - top, rect[2] + left + right, rect[3] + top + bottom)
        if xid not in self.watched:
            w.change_attributes(event_mask=attrs.your_event_mask | self.X.StructureNotifyMask)
            self.watched.add(xid)
        return Window(
            xid,
            str(title),
            str(wmclass[-1]) if wmclass else f"Window {xid}",
            pid,
            rect,
            transient.id if transient else None,
            self.atom("_NET_WM_STATE_MODAL") in states,
            attrs.map_state == self.X.IsViewable,
            self.process_start(pid),
            frame,
            bool(attrs.override_redirect),
            wm_class=tuple(wmclass) if isinstance(wmclass, (tuple, list)) else (),
        )

    def windows(self, ctx=None, notes=None):
        from computer_use_linux.contracts.protocol import gap

        self.pump()
        managed = self.prop(self.root, "_NET_CLIENT_LIST_STACKING")
        # An empty EWMH catalogue is authoritative, not a request to enumerate
        # hidden toolkit leaders/input helpers as document windows. Without a
        # window manager, only mapped root children are visual candidates.
        ids = list(managed) if managed is not None else []
        if managed is None:
            gap(notes, "No EWMH client catalogue; only mapped root windows can be enumerated.")
        # Override-redirect menus/popups are absent from EWMH's managed client list.
        for w in self.root.query_tree().children:
            if ctx:
                ctx.check()
            try:
                attrs = w.get_attributes()
                if (
                    (managed is None or attrs.override_redirect)
                    and attrs.map_state == self.X.IsViewable
                    and w.id not in ids
                ):
                    ids.append(w.id)
            except Exception as error:
                gap(notes, "X11 popup metadata unavailable: " + str(error))
                continue
        result = []
        if len(ids) > 1024:
            gap(notes, "X11 directory limited to 1024 windows.")
        for xid in ids[:1024]:
            if ctx:
                ctx.check()
            try:
                item = self.info(int(xid))
                if item.rect[2] > 1 and item.rect[3] > 1:
                    result.append(item)
            except Exception as error:
                gap(notes, "X11 window metadata unavailable: " + str(error))
                self.retire(int(xid))
        return result

    def validate(self, instance):
        self.pump()
        if not instance.alive:
            raise Failure("Working window was closed.", "stale_target")
        try:
            current = self.info(instance.window.key)
        except Exception as error:
            self.retire(instance.window.key)
            raise Failure("Working window no longer exists.", "stale_target") from error
        if (current.pid, current.process_start) != (instance.window.pid, instance.window.process_start):
            self.retire(current.key)
            raise Failure("Window identity changed.", "stale_target")
        return current

    def active(self):
        value = self.prop(self.root, "_NET_ACTIVE_WINDOW", [])
        return int(value[0]) if (len(value) > 0) else None

    def focus_belongs(self, xid):
        focus = self.d.get_input_focus().focus
        if not hasattr(focus, "id"):
            return False
        for _ in range(64):
            if focus.id == xid:
                return True
            if focus.id == self.root.id:
                break
            try:
                focus = focus.query_tree().parent
            except Exception:
                break
        return False

    def keyboard_focus(self):
        "Exact X input-focus window, not an application/control identity."
        focus = self.d.get_input_focus().focus
        require(
            hasattr(focus, "id") and focus.id not in (0, 1, self.root.id),
            "The native keyboard focus window is unavailable.",
            "input_unavailable",
        )
        return focus.id

    def prepare(self, instance, ctx, pointer=False):
        if not self.can_input:
            raise Failure("XTest is unavailable; native accessibility actions remain available.", "mode_unsupported")
        w = self.validate(instance)
        if not w.mapped:
            raise Failure("Window is not visible; restore it before using visual input.", "input_unavailable")
        if pointer and self.mode == "shared" and not self.assist:
            raise Failure(
                (
                    "X11 pointer input moves the shared pointer. Use a native element action or "
                    "authorize foreground "
                    "assistance."
                ),
                "mode_unsupported",
            )
        if pointer and w.override_redirect:
            # Menus/tooltips are not managed keyboard-focus windows. Pointer
            # routing uses their actual topmost hit target, never keyboard focus.
            return w
        if self.active() != w.key or not self.focus_belongs(w.key):
            if self.mode != "exclusive" and not self.assist:
                raise Failure(
                    "No background keyboard route to this window. Native element text input may still work.",
                    "mode_unsupported",
                )
            self.activate(w, ctx)
        self.validate(instance)
        return w

    def _request_activation(self, w, ctx):
        ctx.sent()
        message = self.events.ClientMessage(
            window=w.key, client_type=self.atom("_NET_ACTIVE_WINDOW"), data=(32, [2, self.X.CurrentTime, 0, 0, 0])
        )
        self.root.send_event(message, event_mask=self.X.SubstructureRedirectMask | self.X.SubstructureNotifyMask)
        self.d.flush()

    def activate(self, w, ctx):
        self._request_activation(w, ctx)
        until = min(ctx.deadline, time.monotonic() + 1)
        while time.monotonic() < until:
            if self.active() == w.key and self.focus_belongs(w.key):
                return
            ctx.pause()
        raise Failure("Window manager did not grant the target input focus.", "input_unavailable", effect=ctx.effect)

    def expose(self, instance, point, ctx, *, is_reachable=None):
        """Request the selected window's exposure for pointer input, not keys.

        The caller owns mode admission and records the preparation attempt.
        A window can receive pointer input without owning keyboard focus. Its
        current hit route, checked after the activation request, is the relevant
        readiness fact. Capture/element identity and geometry are checked again
        by the click pipeline before any requested pointer event.
        """
        window = self.validate(instance)
        self._request_activation(window, ctx)
        until = min(ctx.deadline, time.monotonic() + 1)
        while time.monotonic() < until:
            ctx.check()
            self.validate(instance)
            if is_reachable is not None:
                # Element geometry may legitimately move during preparation.
                # The caller resamples the same binding; capture routes instead
                # retain their original coordinates and reject geometry drift.
                if is_reachable():
                    return
                ctx.pause()
                continue
            try:
                self.require_hit(instance, point)
                return
            except Failure as error:
                if error.code not in ("stale_target", "input_unavailable"):
                    raise
            ctx.pause()
        raise Failure(
            "Window manager did not expose the selected pointer target.", "input_unavailable", effect=ctx.effect
        )

    def present(self, instance, ctx):
        w = self.validate(instance)
        # A popup is not a managed focus window. Re-activating its document can
        # dismiss it; leave already presented menus/tooltips untouched.
        if w.override_redirect:
            return
        if self.active() != w.key or not self.focus_belongs(w.key):
            self.require_idle_input()
            self.activate(w, ctx)
        self.validate(instance)

    def configure_launch(self, context):
        context.setenv("GDK_BACKEND", "x11")
        context.setenv("QT_QPA_PLATFORM", "xcb")
        context.unsetenv("WAYLAND_DISPLAY")

    def require_focus(self, instance):
        self.validate(instance)
        if self.active() != instance.window.key or not self.focus_belongs(instance.window.key):
            raise Failure("Input receiver changed; input stopped.", "stale_target")

    def require_hit(self, instance, point):
        "Check the current topmost X11 child chain without moving the pointer."
        window = self.root
        for _ in range(64):
            if window.id == instance.window.key:
                return
            result = window.translate_coords(self.root, *point)
            child = result.child
            if not child or not hasattr(child, "id") or child.id == window.id:
                break
            window = child
        raise Failure("Target point is covered or outside its window; no click was sent.", "stale_target")

    def require_idle_input(self):
        keys = self.d.query_keymap()
        if any(value for value in keys):
            raise Failure("A keyboard key is already held; input was not started.", "input_unavailable")
        mask = self.root.query_pointer().mask
        if mask & (
            self.X.Button1Mask | self.X.Button2Mask | self.X.Button3Mask | self.X.Button4Mask | self.X.Button5Mask
        ):
            raise Failure("A mouse button is already held; input was not started.", "input_unavailable")

    def _clipboard_resource_version(self):
        if not hasattr(self, "_resource_version"):
            self._resource_version = None
            try:
                if self.d.has_extension("X-Resource"):
                    version = self.d.res_query_version()
                    self._resource_version = (version.server_major, version.server_minor)
            except Exception:
                pass  # Legacy properties remain usable when XRes is unavailable.
        return self._resource_version

    def _resource_pid(self, xid):
        version = self._clipboard_resource_version()
        if version is None or version < (1, 2):
            return None
        try:
            # XRes accepts any resource XID, including an undecorated auxiliary
            # clipboard requestor. The PID comes from the server, not a property
            # that the toolkit may omit. LocalClientPIDMask is 1 << 1.
            reply = self.d.res_query_client_ids([dict(client=xid, mask=2)])
            pids = {
                int(item.value[0])
                for item in reply.ids
                if item.spec.client == xid and item.spec.mask == 2 and len(item.value) == 1
            }
            if pids:
                return next(iter(pids)) if len(pids) == 1 else 0
        except Exception:
            pass
        return None

    def _same_resource_client(self, first, second):
        if self._clipboard_resource_version() is None:
            return False
        try:
            ranges = self.d.res_query_clients().clients
            if len(ranges) > 4096:
                return False

            def owner(xid):
                result = set()
                for item in ranges:
                    base, mask = int(item.resource_base), int(item.resource_mask)
                    if 0 < base <= 0xFFFFFFFF and 0 <= mask <= 0xFFFFFFFF and not base & mask:
                        if xid & ~mask == base:
                            result.add((base, mask))
                return result

            first_owner, second_owner = owner(first), owner(second)
            return len(first_owner) == 1 and first_owner == second_owner
        except Exception:
            return False

    def clipboard_consumer(self, instance):
        w = self.validate(instance)
        if not w.pid or not w.process_start:
            raise Failure("Clipboard receiver process cannot be confirmed.", "input_unavailable")

        def current():
            live = self.validate(instance)
            return (live.key, live.pid, live.process_start) == (w.key, w.pid, w.process_start) and self.process_start(
                w.pid
            ) == w.process_start

        def belongs(xid):
            try:
                if not xid or xid == self.root.id or not current():
                    return False
                self.native(xid).get_attributes()  # An XID range alone does not prove this window still exists.
                pid = self._resource_pid(xid)
                if pid is not None:
                    # A contradictory server PID must never be overridden by
                    # weaker WM_CLIENT_LEADER / _NET_WM_PID properties.
                    return pid == w.pid and current()
                if self._same_resource_client(xid, w.key):
                    return current()
            except Exception:
                return False
            # Older servers/toolkits can identify an auxiliary requestor through
            # its client leader or parent. No application/window-name inference.
            pending, seen = [xid], set()
            while pending and len(seen) < 16:
                candidate = pending.pop()
                if candidate in seen or candidate == self.root.id:
                    continue
                seen.add(candidate)
                try:
                    native = self.native(candidate)
                    pid = self.prop(native, "_NET_WM_PID", [])
                    if len(pid) > 0:
                        if int(pid[0]) == w.pid:
                            return current()
                        continue
                    pending.extend(int(i) for i in self.prop(native, "WM_CLIENT_LEADER", []))
                    pending.append(native.query_tree().parent.id)
                except Exception:
                    continue
            return False

        return belongs

    def read_pixels(self, drawable, width, height):
        if width * height > 64 * 1024 * 1024:
            raise Failure("Capture exceeds pixel budget.", "observation_unavailable")
        reply = drawable.get_image(0, 0, width, height, self.X.ZPixmap, 0xFFFFFFFF)
        if reply.depth not in (24, 32):
            raise Failure(f"Unsupported X11 image depth {reply.depth}.", "observation_unavailable")
        fmt = next((f for f in self.d.display.info.pixmap_formats if f.depth == reply.depth), None)
        if fmt is None or fmt.bits_per_pixel not in (24, 32):
            raise Failure("Unsupported X11 pixel format.", "observation_unavailable")
        stride = ((width * fmt.bits_per_pixel + fmt.scanline_pad - 1) // fmt.scanline_pad) * (fmt.scanline_pad // 8)
        little = self.d.display.info.image_byte_order == self.X.LSBFirst
        raw = ("BGRX" if little else "XRGB") if fmt.bits_per_pixel == 32 else ("BGR" if little else "RGB")
        return self.Image.frombytes("RGB", (width, height), reply.data, "raw", raw, stride, 1)

    def capture(self, instance, scope, ctx, *, max_dimension=4096):
        integer(max_dimension, 1, 4096)
        ctx.check()
        start = now()
        notes = []
        if instance:
            if getattr(self, "window_captures", None) is None:
                from computer_use_linux.system.x11_capture import X11WindowCaptures

                self.window_captures = X11WindowCaptures(self)
            image, w, notes = self.window_captures.read(instance, ctx)
            x, y, native_width, native_height = w.rect
            kind = "window"
        else:
            geom = self.root.get_geometry()
            image = self.read_pixels(self.root, geom.width, geom.height)
            current = self.root.get_geometry()
            if (current.width, current.height) != (geom.width, geom.height):
                raise Failure("Desktop geometry changed during capture.", "stale_target")
            x, y, native_width, native_height = 0, 0, geom.width, geom.height
            kind = "desktop"
        image.thumbnail((max_dimension, max_dimension), self.Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        ctx.check()
        capture = dict(
            id=token("image"),
            kind=kind,
            scope=scope,
            startedAt=start,
            completedAt=now(),
            width=image.width,
            height=image.height,
            png=base64.b64encode(buffer.getvalue()).decode("ascii"),
        )
        if notes:
            capture["stopReasons"] = notes
        return capture, (x, y, native_width / image.width, native_height / image.height)

    def point(self, capture, x, y):
        if not capture.image:
            raise Failure("No captured image establishes these coordinates.", "stale_target")
        if not (0 <= x < capture.image["width"] and 0 <= y < capture.image["height"]):
            raise Failure("Coordinates are outside the captured image.", "invalid_request")
        if capture.instance:
            current = self.validate(capture.instance)
            if current.rect != capture.window.rect:
                raise Failure("Window geometry changed since capture.", "stale_target")
        else:
            geom = self.root.get_geometry()
            if (geom.width, geom.height) != (
                round(capture.image["width"] * capture.transform[2]),
                round(capture.image["height"] * capture.transform[3]),
            ):
                raise Failure("Desktop geometry changed since capture.", "stale_target")
        ox, oy, sx, sy = capture.transform
        return round(ox + x * sx), round(oy + y * sy)

    def _event(self, kind, detail=0, x=0, y=0):
        from Xlib.ext import xtest

        errors = []

        def rejected(error, request):
            errors.append(error)
            return True

        # The convenience wrapper drops per-request errors. A successful sync
        # alone cannot establish that a key (especially KeyRelease) was accepted.
        xtest.FakeInput(
            display=self.d.display,
            opcode=self.d.display.get_extension_major(xtest.extname),
            event_type=kind,
            detail=detail,
            time=self.X.CurrentTime,
            root=self.root.id,
            x=x,
            y=y,
            onerror=rejected,
        )
        self.d.sync()
        if errors:
            raise Failure("XTEST rejected synthetic input: " + str(errors[0]), "execution_unknown", effect="possible")

    def keycode(self, name):
        aliases = {"super": "Super_L", "ctrl": "Control_L", "shift": "Shift_L", "alt": "Alt_L"}
        # Printable ASCII has the same value in X11's Latin-1 keysym range.
        # string_to_keysym expects names such as "bracketleft", not "[".
        keysym = (
            ord(name)
            if len(name) == 1 and 0x21 <= ord(name) <= 0x7E
            else self.XK.string_to_keysym(aliases.get(name, name))
        )
        code = self.d.keysym_to_keycode(keysym)
        if not code:
            raise Failure(f"Keyboard layout has no mapping for {name}.", "input_unavailable")
        return code

    def press_key(self, key, ctx, validate=lambda: None, before_dispatch=lambda: None):
        if not self.can_input:
            raise Failure("XTest keyboard synthesis is unavailable.", "mode_unsupported")
        parts = key.split("+")
        codes = [self.keycode(name) for name in parts]
        self.require_idle_input()
        validate()
        ctx.check()
        try:
            for index, code in enumerate(codes):
                validate()
                ctx.sent()
                if index == 0:
                    before_dispatch()
                # Keep ownership even when the X server reply is lost.
                self.keys.add(code)
                self._event(self.X.KeyPress, code)
            for code in reversed(codes):
                self._event(self.X.KeyRelease, code)
                self.keys.discard(code)
        finally:
            self.release()

    def text_preflight(self, text):
        from computer_use_linux.system.x11_text import keyboard_for, plan_text

        if not self.can_input:
            raise Failure("XTest keyboard synthesis is unavailable.", "mode_unsupported")
        return plan_text(text, keyboard_for(self).snapshot())

    def type_text(self, text, ctx, validate=lambda: None, before_dispatch=lambda: None, before_character=lambda: None):
        from computer_use_linux.system.x11_text import check_plan, keyboard_for

        plan = self.text_preflight(text)  # Entire payload before any key press.
        keyboard = keyboard_for(self)
        self.require_idle_input()
        dispatched = False

        def guard(shifted):
            ctx.check()
            validate()
            check_plan(plan, keyboard.snapshot(), shifted)
            ctx.check()

        def press(code):
            nonlocal dispatched
            ctx.sent()
            if not dispatched:
                before_dispatch()
                dispatched = True
            # Own the release before sending: a lost reply is not no effect.
            self.keys.add(code)
            self._event(self.X.KeyPress, code)

        try:
            for code, shifted in plan.keys:
                guard(False)
                if shifted:
                    press(plan.snapshot.shift)
                    guard(True)  # A modifier may trigger provider callbacks.
                before_character()
                # Re-check the map after that callback, without asking callers
                # to re-establish a full replacement selection after typing began.
                check_plan(plan, keyboard.snapshot(), shifted)
                ctx.check()
                press(code)
                self._event(self.X.KeyRelease, code)
                self.keys.discard(code)
                if shifted:
                    self._event(self.X.KeyRelease, plan.snapshot.shift)
                    self.keys.discard(plan.snapshot.shift)
        finally:
            self.release()  # Cleanup must ignore cancellation and target loss.

    def pointer_preflight(self, kind, points, button="left", count=1, direction=None, pages=1):
        """Resolve a bounded XTEST event plan without moving focus or the pointer.

        Points are absolute root-window pixels. The caller owns capture mapping,
        target/hit testing and policy; this layer checks the entire native plan
        before its first event. Returning the plan also permits preflight before
        any higher-level receiver preparation.
        """
        if not self.can_input:
            raise Failure("XTest pointer synthesis is unavailable.", "mode_unsupported")
        require(kind in ("click", "scroll", "drag"), "Invalid pointer operation.")
        require(button in ("left", "middle", "right"), "Invalid pointer button.")
        integer(count, 1, 2)
        require(type(pages) in (int, float) and 0 < pages <= 100, "Expected positive scroll pages up to 100.")
        finite(pages)
        require(
            isinstance(points, (list, tuple)) and len(points) == (2 if kind == "drag" else 1),
            "Invalid pointer point count.",
        )
        if kind == "scroll":
            require(direction in ("up", "down", "left", "right"), "Invalid scroll direction.")
        else:
            require(direction is None, "Scroll direction is only valid for scrolling.")
        geometry = self.root.get_geometry()
        normalized = []
        for point in points:
            require(isinstance(point, (list, tuple)) and len(point) == 2, "Invalid pointer point.")
            pair = []
            for value, limit in zip(point, (geometry.width, geometry.height)):
                # Range-check before finite()/round(), including huge JSON ints.
                require(
                    type(value) in (int, float) and 0 <= value < limit,
                    "Pointer coordinates are outside the X11 desktop.",
                )
                finite(value)
                pixel = round(value)
                require(
                    0 <= pixel < limit and pixel <= 32767,
                    "Pointer coordinates exceed XTEST's absolute coordinate range.",
                )
                pair.append(pixel)
            normalized.append(tuple(pair))
        number = {"left": 1, "middle": 2, "right": 3}[button]
        motion = ()
        if kind == "drag":
            first, last = normalized
            motion = tuple(
                (round(first[0] + (last[0] - first[0]) * i / 24), round(first[1] + (last[1] - first[1]) * i / 24))
                for i in range(1, 25)
            )
        elif kind == "scroll":
            number = {"up": 4, "down": 5, "left": 6, "right": 7}[direction]
            count = max(1, round(pages * 8))
        return dict(points=tuple(normalized), button=number, count=count, motion=motion)

    def pointer_sequence(
        self,
        kind,
        points,
        ctx,
        button="left",
        count=1,
        direction=None,
        pages=1,
        validate=lambda: None,
        before_dispatch=lambda: None,
        validate_drag=None,
        scroll_step_complete=lambda: None,
        event_progress=None,
    ):
        """Dispatch one prepared gesture; policy and target resolution stay above.

        validate runs before the first motion and each button press. Once a drag
        button is down, validate_drag checks its continuing scope without requiring
        the original hit/layout: X11's implicit grab routes the remaining gesture.
        Releases are cleanup and never depend on a surviving target or request.
        """
        plan = self.pointer_preflight(kind, points, button, count, direction, pages)
        ctx.check()
        self.require_idle_input()
        validate()
        ctx.check()
        number = plan["button"]
        dragging = validate if validate_drag is None else validate_drag

        def send(kind, detail=0, x=0, y=0):
            if event_progress is not None:
                event_progress("enter", kind, detail)
            self._event(kind, detail, x, y)
            if event_progress is not None:
                event_progress("return", kind, detail)

        try:
            ctx.sent()
            before_dispatch()
            send(self.X.MotionNotify, x=plan["points"][0][0], y=plan["points"][0][1])
            if kind == "drag":
                validate()
                ctx.check()
                # Ownership begins before dispatch: even a lost reply requires a
                # release, but never another requested press or gesture replay.
                self.buttons.add(number)
                send(self.X.ButtonPress, number)
                for x, y in plan["motion"]:
                    ctx.pause(0.012)
                    dragging()
                    ctx.check()
                    send(self.X.MotionNotify, x=x, y=y)
            else:
                for index in range(plan["count"]):
                    validate()
                    ctx.check()
                    self.buttons.add(number)
                    send(self.X.ButtonPress, number)
                    send(self.X.ButtonRelease, number)
                    self.buttons.discard(number)
                    if kind == "scroll":
                        scroll_step_complete()
                    if index + 1 < plan["count"]:
                        ctx.pause(0.035)
        finally:
            if event_progress is None:
                self.release()
            else:
                self.release(event_progress=event_progress)

    def pointer(
        self,
        kind,
        points,
        ctx,
        button="left",
        count=1,
        direction=None,
        pages=1,
        validate=lambda: None,
        before_dispatch=lambda: None,
        validate_drag=None,
    ):
        # v3 keeps its historical shared-pointer policy. v5's explicit contract
        # authorizes the sequence and handles blocked routes before reaching it.
        if self.mode == "shared" and not self.assist:
            raise Failure(
                "Shared X11 visual pointer input requires host foreground-assist authorization.", "mode_unsupported"
            )
        return self.pointer_sequence(
            kind, points, ctx, button, count, direction, pages, validate, before_dispatch, validate_drag
        )

    def release(self, *, event_progress=None):
        for kind, held in ((self.X.KeyRelease, self.keys), (self.X.ButtonRelease, self.buttons)):
            for number in list(held):
                try:
                    if event_progress is not None:
                        event_progress("enter", kind, number, cleanup=True)
                    self._event(kind, number)
                    if event_progress is not None:
                        event_progress("return", kind, number, cleanup=True)
                except Exception:
                    continue
                held.discard(number)
        if self.keys or self.buttons:
            raise Failure(
                "Some synthetic input could not be released; retire this session.",
                "execution_unknown",
                effect="possible",
            )

    def close(self):
        try:
            self.release()
        finally:
            try:
                if getattr(self, "window_captures", None) is not None:
                    self.window_captures.close()
            finally:
                try:
                    if getattr(self, "text_keyboard", None) is not None:
                        self.text_keyboard.close()
                finally:
                    self.d.close()
