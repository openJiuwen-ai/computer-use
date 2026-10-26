"""One X11 clipboard lease around one externally guarded paste shortcut.

This is an ICCCM selection owner, not an input strategy. It never sends keys,
changes focus, or retries the requested operation. XFixes generations and short
server grabs make ownership changes conditional; no grab waits on another client.
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque

from computer_use_linux.contracts.protocol import Failure, Request, require


class NativeClipboard:
    MAX_TEXT = 64 * 1024
    MAX_BYTES = 2 * 1024 * 1024
    MAX_FORMATS = 64
    TRANSFER_TIMEOUT = 1.5
    CLEANUP_TIMEOUT = 1.5
    CONVERSION_TIMEOUT = 0.8

    def __init__(self):
        from Xlib import X, Xatom, display, protocol
        from Xlib.ext import xfixes

        self.X, self.atoms, self.event, self.request = X, Xatom, protocol.event, protocol.request
        self.d = display.Display()
        self.max_property = min(self.MAX_BYTES, (min(self.d.display.info.max_request_length, 65535) - 6) * 4)
        self.closed = False
        try:
            require(self.d.has_extension("XFIXES"), "Clipboard transactions require XFixes.", "mode_unsupported")
            version = self.d.xfixes_query_version()
            require(version.major_version >= 2, "Clipboard transactions require XFixes 2.", "mode_unsupported")
            self.window = self.new_window()
            names = (
                "CLIPBOARD",
                "CLIPBOARD_MANAGER",
                "TARGETS",
                "TIMESTAMP",
                "MULTIPLE",
                "SAVE_TARGETS",
                "TARGET_SIZES",
                "INCR",
                "ATOM_PAIR",
                "_COMPUTER_USE_CLIPBOARD_DATA",
                "_COMPUTER_USE_CLIPBOARD_TIME",
                "_COMPUTER_USE_CLIPBOARD_SAVE",
            )
            self.atom = {name: self.d.intern_atom(name) for name in names}
            self.selection, self.manager = self.atom["CLIPBOARD"], self.atom["CLIPBOARD_MANAGER"]
            self.versions = {self.selection: 0, self.manager: 0}
            self.notifies, self.properties, self.requests = deque(maxlen=256), deque(maxlen=256), deque()
            self.data, self.stamp, self.phase, self.consumer = {}, 0, None, None
            self.transferred, self.pending, self.watches = False, {}, {}
            self.active = None
            self.broken = False
            mask = (
                xfixes.XFixesSetSelectionOwnerNotifyMask
                | xfixes.XFixesSelectionWindowDestroyNotifyMask
                | xfixes.XFixesSelectionClientCloseNotifyMask
            )
            for selection in self.versions:
                self.d.xfixes_select_selection_input(self.window, selection, mask)
            self.d.sync()
            self.pump()
        except Exception:
            self.d.close()
            self.closed = True
            raise

    def new_window(self):
        return self.d.screen().root.create_window(
            0, 0, 1, 1, 0, self.X.CopyFromParent, event_mask=self.X.PropertyChangeMask
        )

    @staticmethod
    def xid(value):
        return value.id if hasattr(value, "id") else int(value or 0)

    def owner(self, selection=None):
        return self.xid(self.d.get_selection_owner(self.selection if selection is None else selection))

    @staticmethod
    def older(left, right):
        # X timestamps are unsigned 32-bit milliseconds and wrap every 49 days.
        return left != right and ((right - left) & 0xFFFFFFFF) < 0x80000000

    def pump(self, *, service=True):
        for _ in range(min(self.d.pending_events(), 4096)):
            event = self.d.next_event()
            if hasattr(event, "selection_timestamp") and event.selection in self.versions:
                self.versions[event.selection] += 1
            elif event.type == self.X.SelectionRequest:
                self.requests.append(event)
            elif event.type == self.X.SelectionNotify:
                self.notifies.append(event)
            elif event.type == self.X.PropertyNotify:
                key = self.xid(event.window), event.atom
                if key in self.pending:
                    if event.state == self.X.PropertyNewValue:
                        self.pending[key] = True
                    elif event.state == self.X.PropertyDelete and self.pending[key]:
                        self.transferred = True
                        del self.pending[key]
                if self.xid(event.window) == self.window.id:
                    self.properties.append(event)
        if service:
            # A consumer predicate can use a different X connection. Never call
            # it while this connection holds the server grab.
            for _ in range(min(len(self.requests), 256)):
                self.respond(self.requests.popleft())

    def sync(self, *, service=True):
        self.d.sync()
        self.pump(service=service)

    def version(self, selection=None, *, service=True):
        selection = self.selection if selection is None else selection
        self.sync(service=service)
        owner = self.owner(selection)
        self.pump(service=service)
        return owner, self.versions[selection]

    def wait(self, predicate, ctx, timeout):
        until = min(ctx.deadline, time.monotonic() + timeout)
        while True:
            ctx.check()
            self.pump()
            value = predicate()
            if value is not None:
                return value
            if time.monotonic() >= until:
                raise Failure("X11 clipboard transfer timed out.", "input_unavailable", effect=ctx.effect)
            # Only our event dispatcher consumes our X connection. Other backend
            # pumps may run from Request.pause and are safe to interleave.
            ctx.pause(min(0.01, max(0, until - time.monotonic())))

    def timestamp(self, ctx):
        atom = self.atom["_COMPUTER_USE_CLIPBOARD_TIME"]
        self.properties = deque((e for e in self.properties if e.atom != atom), maxlen=256)
        self.window.change_property(atom, self.atoms.INTEGER, 8, b"")
        self.d.flush()

        def ready():
            for event in tuple(self.properties):
                if event.atom == atom and event.state == self.X.PropertyNewValue:
                    self.properties.remove(event)
                    return event.time
            return None

        return self.wait(ready, ctx, self.CONVERSION_TIMEOUT)

    def notify(self, window, selection, target, stamp, ctx, timeout):
        def ready():
            for event in tuple(self.notifies):
                if (
                    self.xid(event.requestor) == window.id
                    and event.selection == selection
                    and event.target == target
                    and event.time == stamp
                ):
                    self.notifies.remove(event)
                    return event
            return None

        return self.wait(ready, ctx, timeout)

    def property_value(self, window, prop, limit):
        value = window.get_property(prop, self.X.AnyPropertyType, 0, (limit + 3) // 4 + 1)
        require(
            value is not None and value.format in (8, 16, 32),
            "Clipboard format has no preservable value.",
            "input_unavailable",
        )
        require(
            not value.bytes_after and len(value.value) * value.format // 8 <= limit,
            "Clipboard exceeds the preservation budget.",
            "input_unavailable",
        )
        require(
            value.property_type != self.atom["INCR"],
            "Incremental clipboard transfers are not implemented; paste was not started.",
            "input_unavailable",
        )
        return value

    def convert(self, selection, target, ctx):
        # A fresh requestor distinguishes delayed replies after a timeout, without
        # creating unbounded atoms on the server.
        window = self.new_window()
        prop = self.atom["_COMPUTER_USE_CLIPBOARD_DATA"]
        try:
            stamp = self.timestamp(ctx)
            window.convert_selection(selection, target, prop, stamp)
            self.d.flush()
            event = self.notify(window, selection, target, stamp, ctx, self.CONVERSION_TIMEOUT)
            require(event.property == prop, "Existing clipboard format could not be preserved.", "input_unavailable")
            value = self.property_value(window, prop, self.MAX_BYTES)
            window.delete_property(prop)
            self.d.flush()
            return value
        finally:
            window.destroy()
            self.d.flush()

    def snapshot(self, ctx):
        stamp = self.timestamp(ctx)
        version = self.version()
        if not version[0]:
            return stamp, version, {}
        manager = self.owner(self.manager)
        require(
            manager,
            "Existing clipboard needs an X11 clipboard manager for durable restoration; paste was not started.",
            "input_unavailable",
        )
        manager_targets = self.convert(self.manager, self.atom["TARGETS"], ctx)
        require(
            manager_targets.property_type == self.atoms.ATOM
            and manager_targets.format == 32
            and self.atom["SAVE_TARGETS"] in manager_targets.value,
            "Clipboard manager does not declare durable restoration (SAVE_TARGETS); paste was not started.",
            "input_unavailable",
        )
        targets = self.convert(self.selection, self.atom["TARGETS"], ctx)
        require(
            targets.property_type == self.atoms.ATOM
            and targets.format == 32
            and len(targets.value) <= self.MAX_FORMATS,
            "Existing clipboard has unsupported targets.",
            "input_unavailable",
        )
        metadata = {
            "TARGETS",
            "TIMESTAMP",
            "MULTIPLE",
            "SAVE_TARGETS",
            "TARGET_SIZES",
            "DELETE",
            "INSERT_PROPERTY",
            "INSERT_SELECTION",
            "_NET_MAX_SELECTION_SIZE",
        }
        text = {"UTF8_STRING", "STRING", "TEXT", "COMPOUND_TEXT"}
        saved, size = {}, 0
        for target in dict.fromkeys(int(value) for value in targets.value):
            ctx.check()
            name = self.d.get_atom_name(target)
            if name in metadata:
                continue
            # Unknown private conversions can have side effects. MIME targets
            # declare data; standard textual targets are also safe to retrieve.
            require(
                name in text or re.fullmatch(r"[A-Za-z0-9!#$&^_.+\-]+/[A-Za-z0-9!#$&^_.+\-]+(?:;[^\x00-\x1f]*)?", name),
                "Clipboard target cannot be safely preserved: " + name,
                "input_unavailable",
            )
            value = self.convert(self.selection, target, ctx)
            type_name = self.d.get_atom_name(value.property_type)
            require(
                type_name not in {"PIXMAP", "DRAWABLE", "BITMAP", "WINDOW", "COLORMAP"},
                "Clipboard format refers to external X resources; paste was not started.",
                "input_unavailable",
            )
            size += len(value.value) * value.format // 8
            require(size <= self.MAX_BYTES, "Clipboard exceeds the preservation budget.", "input_unavailable")
            require(
                len(value.value) * value.format // 8 <= self.max_property,
                "Clipboard format needs incremental restoration; paste was not started.",
                "input_unavailable",
            )
            saved[target] = (value.property_type, value.format, value.value[:])
        require(saved, "Existing clipboard has no safely preservable data.", "input_unavailable")
        require(
            self.version() == version, "Clipboard changed during preservation; paste was not started.", "stale_target"
        )
        return stamp, version, saved

    def checked(self, function, *args, **kwargs):
        errors = []

        def rejected(error, request):
            errors.append(error)
            return True

        function(*args, **kwargs, onerror=rejected)
        self.d.sync()
        if errors:
            raise Failure("X server rejected a clipboard transfer.", "execution_unknown") from errors[0]

    def watch(self, requestor):
        if requestor.id not in self.watches:
            mask = requestor.get_attributes().your_event_mask
            self.checked(requestor.change_attributes, event_mask=mask | self.X.PropertyChangeMask)
            self.watches[requestor.id] = mask

    def conversion(self, requestor, target, prop):
        if not prop:
            return False
        if target == self.atom["TARGETS"]:
            values = [self.atom[k] for k in ("TARGETS", "TIMESTAMP", "MULTIPLE", "SAVE_TARGETS")] + list(self.data)
            self.checked(requestor.change_property, prop, self.atoms.ATOM, 32, values)
        elif target == self.atom["TIMESTAMP"]:
            self.checked(requestor.change_property, prop, self.atoms.INTEGER, 32, [self.stamp])
        elif target == self.atom["SAVE_TARGETS"]:
            self.checked(requestor.change_property, prop, self.atoms.ATOM, 32, [])
        elif target in self.data:
            temporary = self.phase == "temporary"
            if temporary and not (self.consumer and self.consumer(requestor.id)):
                return False
            if temporary:
                self.watch(requestor)
                self.pending[(requestor.id, prop)] = False
            type_, format_, value = self.data[target]
            self.checked(requestor.change_property, prop, type_, format_, value)
        else:
            return False
        return True

    def respond(self, event):
        requestor = self.d.create_resource_object("window", self.xid(event.requestor))
        prop = event.property or event.target
        okay = bool(
            self.phase
            and event.selection == self.selection
            and self.xid(event.owner) == self.window.id
            and (not event.time or not self.older(event.time, self.stamp))
        )
        try:
            if okay and event.target == self.atom["MULTIPLE"]:
                okay = bool(event.property)
                if okay:
                    value = self.property_value(requestor, prop, 8 * self.MAX_FORMATS)
                    okay = (
                        value.format == 32
                        and value.property_type == self.atom["ATOM_PAIR"]
                        and len(value.value) % 2 == 0
                    )
                    if okay:
                        pairs = list(value.value)
                        for i in range(0, len(pairs), 2):
                            try:
                                converted = pairs[i] != self.atom["MULTIPLE"] and self.conversion(
                                    requestor, pairs[i], pairs[i + 1]
                                )
                            except Exception:
                                converted = False
                            if not converted:
                                pairs[i + 1] = self.X.NONE
                        self.checked(requestor.change_property, prop, self.atom["ATOM_PAIR"], 32, pairs)
            elif okay:
                okay = self.conversion(requestor, event.target, prop)
            self.checked(
                requestor.send_event,
                self.event.SelectionNotify(
                    time=event.time,
                    requestor=requestor,
                    selection=event.selection,
                    target=event.target,
                    property=prop if okay else self.X.NONE,
                ),
            )
        except Exception:
            # A destroyed requestor is a failed transfer, never consumption proof.
            for key in tuple(self.pending):
                if key[0] == requestor.id:
                    del self.pending[key]

    def change_owner(self, expected, stamp, data, phase, ctx, *, clear=False):
        """Conditional publication; the grab covers only this connection's IO."""
        ctx.check()
        self.d.grab_server()
        try:
            self.sync(service=False)
            require(
                self.version(service=False) == expected,
                "Clipboard ownership changed; newer clipboard content was preserved.",
                "stale_target",
            )
            ctx.check()
            ctx.sent()
            if not clear:
                self.data, self.stamp, self.phase = data, stamp, phase
            # Mark possible change before asking the server, even if the reply is lost.
            if self.active is not None:
                self.active["changing"] = True
            self.checked(
                self.request.SetSelectionOwner,
                display=self.d.display,
                window=self.X.NONE if clear else self.window.id,
                selection=self.selection,
                time=stamp,
            )
            self.sync(service=False)
            actual = self.version(service=False)
            require(
                actual[0] == (0 if clear else self.window.id) and actual != expected,
                "Conditional clipboard publication was refused.",
                "input_unavailable",
            )
            return actual
        finally:
            self.d.ungrab_server()
            self.d.flush()

    def guard(self):
        require(
            self.active is not None and self.active.get("owned") is not None,
            "No active clipboard payload is bound to this paste.",
            "stale_target",
        )
        require(
            self.version() == self.active["owned"],
            "Clipboard changed before the paste key; no further key was sent.",
            "stale_target",
        )

    def handoff(self, expected, ctx):
        manager = self.owner(self.manager)
        require(manager, "Clipboard manager disappeared before restoration completed.", "input_unavailable")
        stamp = self.timestamp(ctx)
        prop = self.atom["_COMPUTER_USE_CLIPBOARD_SAVE"]
        self.d.grab_server()
        try:
            self.sync(service=False)
            if self.version(service=False) != expected:
                require(self.owner() != self.window.id, "Clipboard generation changed before handoff.", "stale_target")
                return  # A copy made after restoring the saved formats wins.
            self.checked(self.window.change_property, prop, self.atoms.ATOM, 32, list(self.data))
            self.window.convert_selection(self.manager, self.atom["SAVE_TARGETS"], prop, stamp)
            self.d.flush()
        finally:
            self.d.ungrab_server()
            self.d.flush()
        event = self.notify(self.window, self.manager, self.atom["SAVE_TARGETS"], stamp, ctx, self.CLEANUP_TIMEOUT)
        require(
            event.property == prop and self.owner() != self.window.id,
            "Clipboard manager did not acknowledge durable restoration.",
            "input_unavailable",
        )
        self.window.delete_property(prop)
        self.d.flush()

    def restore(self, lease):
        ctx = Request(time.monotonic() + self.CLEANUP_TIMEOUT, threading.Event())
        current = self.version()
        if current[0] != self.window.id:
            return  # A user's newer copy owns the selection. Never overwrite it.
        owned = lease.get("owned")
        require(
            owned is None or current == owned,
            "Clipboard generation changed unexpectedly during cleanup.",
            "stale_target",
        )
        stamp = self.timestamp(ctx)
        try:
            lease["owned"] = self.change_owner(
                current, stamp, lease["saved"], "restore", ctx, clear=not lease["previous"][0]
            )
        except Failure as error:
            if error.code == "stale_target" and self.owner() != self.window.id:
                return
            raise
        if lease["previous"][0]:
            self.handoff(lease["owned"], ctx)

    def unwatch(self):
        for xid, mask in tuple(self.watches.items()):
            try:
                window = self.d.create_resource_object("window", xid)
                self.checked(window.change_attributes, event_mask=mask)
            except Exception as error:
                cause = error.__cause__ or error
                if type(cause).__name__ != "BadWindow":
                    raise
            self.watches.pop(xid, None)
        self.pending.clear()

    def paste(self, text, ctx, send, consumer):
        require(
            not self.closed and not self.broken and self.active is None,
            "Clipboard connection is unavailable or already leased.",
            "input_unavailable",
        )
        require(isinstance(text, str), "Paste expects text.")
        encoded = text.encode("utf-8")
        require(
            len(encoded) <= min(self.MAX_TEXT, self.max_property),
            "X11 paste supports at most 64 KiB per operation.",
            "input_unavailable",
        )
        stamp, previous, saved = self.snapshot(ctx)
        targets = [self.d.intern_atom(name) for name in ("UTF8_STRING", "text/plain;charset=utf-8", "text/plain")]
        data = {target: (target, 8, encoded) for target in targets}
        lease = dict(previous=previous, saved=saved, changing=False, owned=None)
        self.active, self.consumer, self.transferred = lease, consumer, False
        error, cleanup = None, "complete"
        try:
            lease["owned"] = self.change_owner(previous, stamp, data, "temporary", ctx)
            send(self.guard)
            try:
                self.wait(lambda: True if self.transferred else None, ctx, self.TRANSFER_TIMEOUT)
            except Failure as failure:
                raise Failure(
                    "Paste shortcut was sent, but target clipboard consumption was not confirmed.",
                    failure.code if failure.code in ("cancelled", "deadline_exceeded") else "execution_unknown",
                    effect="possible",
                ) from failure
        except Exception as failure:
            error = failure
        finally:
            try:
                if lease["changing"]:
                    self.restore(lease)
            except Exception as failure:
                cleanup, self.broken = "failed", True
                if error is None:
                    error = failure
            self.consumer = None
            try:
                self.unwatch()
            except Exception as failure:
                cleanup, self.broken = "failed", True
                if error is None:
                    error = failure
            if cleanup == "complete":
                self.active, self.phase, self.data = None, None, {}
        return dict(prepared=lease["changing"], transferred=self.transferred, cleanup=cleanup, error=error)

    def close(self):
        if self.closed:
            return
        error = None
        try:
            if self.active is not None and self.active.get("changing"):
                try:
                    self.restore(self.active)
                except Exception as failure:
                    error = failure
            self.unwatch()
            self.window.destroy()
            self.d.flush()
        finally:
            self.closed = True
            self.d.close()
        if error is not None:
            raise error
