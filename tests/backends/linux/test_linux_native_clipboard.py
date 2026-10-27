import sys
import threading
import time
import unittest
from collections import deque
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import patch

# Install the platform package path used by portable tests.
import backends.linux.test_linux_native as test_linux_native  # noqa: F401
from computer_use_linux.contracts.protocol import Failure, Request
from computer_use_linux.control.native_clipboard import NativeClipboard

"Real v5 selection transaction with only the X server transport substituted."


X = NS(
    CopyFromParent=0,
    PropertyChangeMask=1,
    PropertyNewValue=0,
    PropertyDelete=1,
    SelectionRequest=30,
    SelectionNotify=31,
    PropertyNotify=28,
    NONE=0,
    AnyPropertyType=0,
)


class NativeXError(Exception):
    pass


class BadWindow(NativeXError):
    pass


class Window:
    def __init__(self, server, xid, mask=0):
        self.s, self.id, self.mask, self.props = server, xid, mask, {}

    def create_window(self, *args, **kwargs):
        return self.s.window(event_mask=kwargs.get("event_mask", 0))

    def get_attributes(self):
        return NS(your_event_mask=self.mask)

    def change_attributes(self, event_mask, onerror=None):
        if self.id not in self.s.windows:
            if onerror:
                onerror(BadWindow(), None)
            return
        if self.s.attribute_error and event_mask == 0:
            onerror(NativeXError("unsubscribe failed"), None)
            return
        self.mask = event_mask

    def change_property(self, prop, type_, format_, data, onerror=None):
        if self.s.write_error and self.id == self.s.consumer_id:
            if onerror:
                onerror(NativeXError("write failed"), None)
            return
        value = bytes(data) if format_ == 8 else list(data)
        self.props[prop] = NS(property_type=type_, format=format_, value=value, bytes_after=0)
        if self.mask & X.PropertyChangeMask:
            self.s.events.append(
                NS(type=X.PropertyNotify, window=self, atom=prop, state=X.PropertyNewValue, time=self.s.tick())
            )

    def delete_property(self, prop):
        if prop in self.props:
            del self.props[prop]
            if self.mask & X.PropertyChangeMask:
                self.s.events.append(
                    NS(type=X.PropertyNotify, window=self, atom=prop, state=X.PropertyDelete, time=self.s.tick())
                )

    def get_property(self, prop, type_, offset, length):
        value = self.props.get(prop)
        if value is None:
            return None
        copy = NS(**vars(value))
        n = length * 4 * 8 // value.format
        copy.value = value.value[:n]
        copy.bytes_after = max(0, len(value.value) - n) * value.format // 8
        return copy

    def convert_selection(self, selection, target, prop, stamp):
        self.s.convert(self, selection, target, prop, stamp)

    def send_event(self, event, onerror=None):
        if self.s.notify_error and self.id == self.s.consumer_id:
            if onerror:
                onerror(NativeXError("notify failed"), None)
            return
        self.s.notifications.append(event)
        if self.id == self.s.consumer_id:
            if event.property:
                self.s.received.append(bytes(self.props[event.property].value))
                if self.s.consume:
                    self.delete_property(event.property)
        elif self.id == self.s.manager_id and self.s.saving:
            if event.property:
                self.s.saved_data[event.target] = self.props[event.property]
            self.s.save_left.discard(event.target)
            if not self.s.save_left:
                self.s.finish_save()

    def destroy(self):
        self.s.windows.pop(self.id, None)
        for selection, owner in tuple(self.s.owners.items()):
            if owner == self.id:
                self.s.set_owner(0, selection, self.s.tick())


class Server:
    def __init__(self):
        self.events, self.notifications, self.conversions, self.received = deque(), [], [], []
        self.names, self.atom_names, self.next_atom = {}, {}, 1000
        self.windows, self.next_window, self.clock = {}, 100, 100
        self.root = self.window(xid=1)
        self.original_id, self.manager_id, self.consumer_id = 10, 20, 30
        for xid in (10, 20, 30, 40):
            self.window(xid=xid)
        self.clip, self.manager = self.atom("CLIPBOARD"), self.atom("CLIPBOARD_MANAGER")
        self.owners, self.ownership_time = {self.clip: 10, self.manager: 20}, {self.clip: 1, self.manager: 1}
        self.original = {
            self.atom("UTF8_STRING"): (self.atom("UTF8_STRING"), 8, b"original"),
            self.atom("text/html"): (self.atom("text/html"), 8, b"<b>original</b>"),
        }
        self.advertised_extra = []
        self.grabbed, self.closed, self.consume = False, False, True
        self.write_error, self.notify_error, self.attribute_error = False, False, False
        self.save_ack, self.saving, self.save_left, self.saved_data = True, None, set(), {}
        self.manager_support = True
        self.owner_error, self.before_grab, self.before_publish, self.after_conversion = None, None, None, None
        self.before_save, self.before_save_ack = None, None
        self.max_request_length = 65535
        self.set_requests, self.grabs = [], 0

    def tick(self):
        self.clock += 1
        return self.clock

    def atom(self, name):
        if name not in self.names:
            self.names[name] = self.next_atom
            self.atom_names[self.next_atom] = name
            self.next_atom += 1
        return self.names[name]

    def window(self, xid=None, event_mask=0):
        if xid is None:
            xid, self.next_window = self.next_window, self.next_window + 1
        window = Window(self, xid, event_mask)
        self.windows[xid] = window
        return window

    def set_owner(self, window, selection, stamp, onerror=None):
        self.set_requests.append((window, selection, stamp))
        if self.owner_error:
            if onerror:
                onerror(NativeXError(self.owner_error), None)
            return
        if NativeClipboard.older(stamp, self.ownership_time[selection]):
            return
        self.owners[selection], self.ownership_time[selection] = window, stamp
        self.events.append(
            NS(type=100, selection=selection, owner=window, timestamp=self.tick(), selection_timestamp=stamp)
        )

    def copy(self, text=b"new copy", *, owner=10):
        self.original = {self.atom("UTF8_STRING"): (self.atom("UTF8_STRING"), 8, text)}
        self.set_owner(owner, self.clip, self.tick())

    def request_content(self, owner, *, target=None, stamp=None):
        assert not self.grabbed, "No client callbacks while X server is grabbed"
        target = target or self.atom("UTF8_STRING")
        prop = self.atom("_TEST_DATA_" + str(owner))
        self.events.append(
            NS(
                type=X.SelectionRequest,
                owner=self.owners[self.clip],
                requestor=self.windows[owner],
                selection=self.clip,
                target=target,
                property=prop,
                time=stamp or self.tick(),
            )
        )

    def convert(self, requestor, selection, target, prop, stamp):
        self.conversions.append((selection, target))
        if selection == self.manager and target == self.atom("TARGETS"):
            targets = [self.atom("TARGETS"), self.atom("TIMESTAMP")]
            if self.manager_support:
                targets.append(self.atom("SAVE_TARGETS"))
            requestor.change_property(prop, self.atom("ATOM"), 32, targets)
            self.events.append(
                NS(
                    type=X.SelectionNotify,
                    requestor=requestor,
                    selection=selection,
                    target=target,
                    property=prop,
                    time=stamp,
                )
            )
            return
        if selection == self.manager:
            if self.before_save:
                callback, self.before_save = self.before_save, None
                callback()
            # No other client can run under the server grab. Queue its work.
            self.saving = (requestor, prop, stamp)
            self.save_left = set(requestor.props[prop].value)
            self.saved_data = {}
            for format_ in self.save_left:
                self.events.append(
                    NS(
                        type=X.SelectionRequest,
                        owner=self.owners[self.clip],
                        requestor=self.windows[self.manager_id],
                        selection=self.clip,
                        target=format_,
                        property=self.atom("_SAVE_" + str(format_)),
                        time=stamp,
                    )
                )
            return
        assert not self.grabbed, "Snapshot must never wait on another client under grab"
        if target == self.atom("TARGETS"):
            data = (
                self.atom("ATOM"),
                32,
                [self.atom(name) for name in ("TARGETS", "TIMESTAMP", "MULTIPLE", "SAVE_TARGETS")]
                + list(self.original)
                + self.advertised_extra,
            )
        else:
            data = self.original.get(target)
        if data:
            requestor.change_property(prop, *data)
        self.events.append(
            NS(
                type=X.SelectionNotify,
                requestor=requestor,
                selection=selection,
                target=target,
                property=prop if data else 0,
                time=stamp,
            )
        )
        if self.after_conversion:
            callback, self.after_conversion = self.after_conversion, None
            callback()

    def finish_save(self):
        requestor, prop, stamp = self.saving
        self.saving = None
        if self.before_save_ack:
            callback, self.before_save_ack = self.before_save_ack, None
            callback()
        self.set_owner(self.manager_id, self.clip, stamp)
        if self.save_ack:
            self.events.append(
                NS(
                    type=X.SelectionNotify,
                    requestor=requestor,
                    selection=self.manager,
                    target=self.atom("SAVE_TARGETS"),
                    property=prop,
                    time=stamp,
                )
            )

    def modules(self):
        server = self

        class Display:
            def __init__(self):
                self.display = self
                self.info = NS(max_request_length=server.max_request_length)

            def screen(self):
                return NS(root=server.root)

            def has_extension(self, name):
                return True

            def xfixes_query_version(self):
                return NS(major_version=4)

            def xfixes_select_selection_input(self, *args):
                pass

            def intern_atom(self, name):
                return server.atom(name)

            def get_atom_name(self, atom):
                return server.atom_names[atom]

            def get_selection_owner(self, selection):
                return server.windows.get(server.owners[selection], 0)

            def pending_events(self):
                return len(server.events)

            def next_event(self):
                return server.events.popleft()

            def sync(self):
                pass

            def flush(self):
                pass

            def grab_server(self):
                if server.before_grab:
                    callback, server.before_grab = server.before_grab, None
                    callback()
                server.grabbed, server.grabs = True, server.grabs + 1

            def ungrab_server(self):
                server.grabbed = False

            def create_resource_object(self, kind, xid):
                return server.windows.get(xid) or Window(server, xid)

            def close(self):
                server.closed = True

        def set_owner(display, window, selection, time, onerror=None):
            if server.before_publish:
                callback, server.before_publish = server.before_publish, None
                callback()
            server.set_owner(window, selection, time, onerror)

        xlib, ext = ModuleType("Xlib"), ModuleType("Xlib.ext")
        xlib.X, xlib.Xatom = X, NS(ATOM=self.atom("ATOM"), INTEGER=self.atom("INTEGER"))
        xlib.display = NS(Display=Display)
        xlib.protocol = NS(
            event=NS(SelectionNotify=lambda **kw: NS(type=X.SelectionNotify, **kw)),
            request=NS(SetSelectionOwner=set_owner),
        )
        ext.xfixes = NS(
            XFixesSetSelectionOwnerNotifyMask=1,
            XFixesSelectionWindowDestroyNotifyMask=2,
            XFixesSelectionClientCloseNotifyMask=4,
        )
        return {"Xlib": xlib, "Xlib.ext": ext}


class LinuxNativeClipboardTests(unittest.TestCase):
    def setUp(self):
        self.server = Server()
        self.modules = patch.dict(sys.modules, self.server.modules())
        self.modules.start()
        self.clip = NativeClipboard()
        self.clip.TRANSFER_TIMEOUT = self.clip.CLEANUP_TIMEOUT = self.clip.CONVERSION_TIMEOUT = 0.08
        self.sent = 0

    def tearDown(self):
        try:
            self.clip.close()
        except Exception:
            pass
        self.modules.stop()

    @staticmethod
    def ctx():
        return Request(time.monotonic() + 2, threading.Event())

    def consumer(self, xid):
        self.assertFalse(self.server.grabbed)
        return xid == self.server.consumer_id

    def send(self, guard):
        guard()
        self.sent += 1
        self.server.request_content(self.server.consumer_id)

    def paste(self, send=None, ctx=None):
        return self.clip.paste("中文 😀\nsecond", ctx or self.ctx(), send or self.send, self.consumer)

    def test_unicode_multiline_and_all_data_formats_restore_with_real_manager_ack(self):
        result = self.paste()
        self.assertEqual(
            {k: v for k, v in result.items() if k != "error"}, dict(prepared=True, transferred=True, cleanup="complete")
        )
        self.assertIsNone(result["error"])
        self.assertEqual(self.sent, 1)
        self.assertEqual(self.server.received, ["中文 😀\nsecond".encode()])
        self.assertEqual(
            {k: bytes(v.value) for k, v in self.server.saved_data.items()},
            {k: value[2] for k, value in self.server.original.items()},
        )
        self.assertEqual(self.server.owners[self.server.clip], self.server.manager_id)
        self.assertFalse(self.server.grabbed)
        self.assertEqual(self.clip.watches, {})
        self.clip.close()
        self.assertEqual(self.server.owners[self.server.clip], self.server.manager_id)

    def test_metadata_and_known_side_effect_targets_are_never_converted(self):
        names = ("DELETE", "INSERT_PROPERTY", "INSERT_SELECTION", "_NET_MAX_SELECTION_SIZE", "TARGET_SIZES")
        self.server.advertised_extra += [self.server.atom(name) for name in names]
        self.assertIsNone(self.paste()["error"])
        requested = {self.server.atom_names[target] for _, target in self.server.conversions}
        self.assertFalse(set(names) & requested)

    def test_unknown_private_conversion_is_rejected_without_requesting_it(self):
        target = self.server.atom("PRIVATE_COMMAND")
        self.server.advertised_extra.append(target)
        with self.assertRaisesRegex(Failure, "safely preserved"):
            self.paste()
        self.assertNotIn((self.server.clip, target), self.server.conversions)
        self.assertEqual(self.sent, 0)
        self.assertEqual(self.server.set_requests, [])

    def test_resource_format_is_not_copied_as_an_unowned_xid(self):
        self.server.original[self.server.atom("image/test")] = (self.server.atom("PIXMAP"), 32, [400])
        with self.assertRaisesRegex(Failure, "external X resources"):
            self.paste()
        self.assertEqual(self.server.set_requests, [])

    def test_incremental_format_and_request_limit_fail_before_publication(self):
        self.server.original[self.server.atom("image/png")] = (self.server.atom("INCR"), 32, [100000])
        with self.assertRaisesRegex(Failure, "Incremental"):
            self.paste()
        self.server.original.pop(self.server.atom("image/png"))
        self.clip.max_property = 16
        self.server.original[self.server.atom("text/html")] = (self.server.atom("text/html"), 8, b"a" * 20)
        with self.assertRaisesRegex(Failure, "incremental restoration"):
            self.clip.paste("x", self.ctx(), self.send, self.consumer)
        self.assertEqual(self.server.set_requests, [])

    def test_existing_clipboard_without_manager_is_rejected_before_touch(self):
        self.server.owners[self.server.manager] = 0
        with self.assertRaisesRegex(Failure, "clipboard manager"):
            self.paste()
        self.assertEqual(self.server.set_requests, [])

    def test_manager_without_save_targets_is_refused_before_dispatch(self):
        self.server.manager_support = False
        with self.assertRaisesRegex(Failure, "SAVE_TARGETS"):
            self.paste()
        self.assertEqual(self.server.set_requests, [])
        self.assertEqual(self.sent, 0)

    def test_empty_clipboard_does_not_need_manager_and_returns_to_empty(self):
        self.server.owners[self.server.manager] = self.server.owners[self.server.clip] = 0
        result = self.paste()
        self.assertIsNone(result["error"])
        self.assertEqual(self.server.owners[self.server.clip], 0)

    def test_same_owner_copy_during_snapshot_is_detected(self):
        self.server.after_conversion = lambda: self.server.set_owner(10, self.server.clip, self.server.tick())
        with self.assertRaisesRegex(Failure, "changed during preservation"):
            self.paste()
        self.assertEqual(self.sent, 0)
        self.assertEqual(len(self.server.set_requests), 1)

    def test_copy_between_snapshot_and_final_cas_wins(self):
        self.server.before_grab = lambda: self.server.copy()
        result = self.paste()
        self.assertEqual((result["prepared"], result["cleanup"], self.sent), (False, "complete", 0))
        self.assertEqual(result["error"].code, "stale_target")
        self.assertEqual(self.server.original[self.server.atom("UTF8_STRING")][2], b"new copy")

    def test_copy_after_publication_is_not_overwritten_or_pasted(self):
        def send(guard):
            self.server.copy()
            guard()
            self.fail("a key must not be sent after the clipboard changed")

        result = self.paste(send)
        self.assertEqual((result["prepared"], result["cleanup"], result["transferred"]), (True, "complete", False))
        self.assertEqual(result["error"].code, "stale_target")
        self.assertEqual(self.server.owners[self.server.clip], 10)

    def test_cancellation_before_key_restores_independently(self):
        ctx = self.ctx()

        def cancel(guard):
            ctx.cancelled.set()
            ctx.check()

        result = self.paste(cancel, ctx)
        self.assertEqual((result["prepared"], result["cleanup"]), (True, "complete"))
        self.assertEqual(result["error"].code, "cancelled")
        self.assertEqual(self.server.owners[self.server.clip], 20)

    def test_after_key_cancellation_does_not_call_send_twice(self):
        ctx = self.ctx()

        def cancel(guard):
            guard()
            self.sent += 1
            ctx.cancelled.set()

        result = self.paste(cancel, ctx)
        self.assertEqual(self.sent, 1)
        self.assertEqual(result["cleanup"], "complete")
        self.assertEqual(result["error"].code, "cancelled")

    def test_manager_cannot_consume_temporary_payload_or_count_as_target_transfer(self):
        def send(guard):
            guard()
            self.sent += 1
            self.server.request_content(self.server.manager_id)

        result = self.paste(send)
        self.assertFalse(result["transferred"])
        self.assertEqual(self.sent, 1)
        self.assertEqual(result["cleanup"], "complete")
        self.assertEqual(result["error"].code, "execution_unknown")
        denied = [n for n in self.server.notifications if n.requestor.id == self.server.manager_id and not n.property]
        self.assertTrue(denied)

    def test_data_offer_without_property_deletion_is_not_consumption(self):
        self.server.consume = False
        result = self.paste()
        self.assertFalse(result["transferred"])
        self.assertEqual(self.sent, 1)
        self.assertEqual(result["error"].code, "execution_unknown")

    def test_property_write_or_notify_error_cannot_count_as_consumption(self):
        for field in ("write_error", "notify_error"):
            with self.subTest(field=field):
                setattr(self.server, field, True)
                result = self.paste()
                self.assertFalse(result["transferred"])
                self.assertEqual(result["cleanup"], "complete")
                setattr(self.server, field, False)

    def test_publication_rejection_still_returns_prepared_receipt(self):
        self.server.owner_error = "rejected"
        result = self.paste()
        self.assertTrue(result["prepared"])
        self.assertEqual(result["cleanup"], "complete")
        self.assertEqual(result["error"].code, "execution_unknown")
        self.assertEqual(self.sent, 0)

    def test_restoration_rejection_is_not_hidden_by_same_owner_xid(self):
        def send(guard):
            self.send(guard)
            self.server.owner_error = "restoration rejected"

        result = self.paste(send)
        self.assertEqual(result["cleanup"], "failed")
        self.assertTrue(self.clip.broken)
        with self.assertRaisesRegex(Failure, "unavailable"):
            self.paste()

    def test_manager_must_acknowledge_restoration_not_just_take_owner(self):
        self.server.save_ack = False
        result = self.paste()
        self.assertEqual(result["cleanup"], "failed")
        self.assertTrue(self.clip.broken)

    def test_new_copy_during_handoff_is_not_overwritten_by_driver(self):
        self.server.before_save_ack = lambda: self.server.copy()
        result = self.paste()
        self.assertEqual(result["cleanup"], "complete")
        self.assertEqual(self.server.owners[self.server.clip], 10)
        self.assertEqual(self.server.original[self.server.atom("UTF8_STRING")][2], b"new copy")

    def test_unwatch_unknown_error_is_reported_as_failed_cleanup(self):
        self.server.attribute_error = True
        result = self.paste()
        self.assertEqual(result["cleanup"], "failed")

    def test_destroyed_target_subscription_requires_no_cleanup(self):
        def send(guard):
            self.send(guard)
            self.clip.pump()
            self.clip.pump()
            self.server.windows[self.server.consumer_id].destroy()

        result = self.paste(send)
        self.assertEqual(result["cleanup"], "complete")

    def test_x_timestamp_wrap_comparison(self):
        self.assertTrue(NativeClipboard.older(0xFFFFFFF0, 4))
        self.assertFalse(NativeClipboard.older(4, 0xFFFFFFF0))
        self.assertFalse(NativeClipboard.older(4, 4))


if __name__ == "__main__":
    unittest.main()
