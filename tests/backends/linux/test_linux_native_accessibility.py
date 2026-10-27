"""Optional AT-SPI lifecycle against GI and native-window seams, not a GUI test."""

import io
import json
import threading
import time
import unittest
from contextlib import ExitStack
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from computer_use_linux.contracts.protocol import Failure, Request
from computer_use_linux.objects.native_accessibility import NativeAccessibility
from computer_use_linux.objects.native_store import NativeStore
from computer_use_linux.objects.registry import Registry, Window
from computer_use_linux.runtime.native_backend import NativeBackend
from computer_use_linux.runtime.server import Server
from computer_use_linux.windowing.native_directory import NativeDirectory


class NativeError(Exception):
    pass


def context():
    return Request(time.monotonic() + 10, threading.Event())


class OptionalAccessibilityTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.loop = NS(pending=Mock(return_value=False), iteration=Mock())
        self.GLib = NS(Error=NativeError, MainContext=NS(default=lambda: self.loop))
        self.listener = NS(register=Mock(return_value=True), deregister=Mock(return_value=True))
        self.Atspi = NS(
            set_timeout=Mock(),
            init=Mock(return_value=0),
            exit=Mock(),
            EventListener=NS(new=Mock(return_value=self.listener)),
            get_desktop=Mock(return_value=None),
            Accessible=NS(clear_cache_single=Mock()),
            StateType=NS(),
            Role=NS(),
        )
        self.Gio = NS(AppInfo=NS(get_all=lambda: []))
        gi = NS(require_version=Mock())
        repository = NS(Atspi=self.Atspi, Gio=self.Gio, GLib=self.GLib, GObject=NS(Object=object))
        self.stack.enter_context(patch.dict("sys.modules", {"gi": gi, "gi.repository": repository}))
        self.connector = self.stack.enter_context(
            patch("computer_use_linux.objects.accessibility_bus.connect_accessibility_bus")
        )
        self.ax = NativeAccessibility()
        self.addCleanup(self.ax.close)

    def begin(self):
        ctx = context()
        self.ax.begin(ctx)
        return ctx

    def assert_failure(self, code, fn):
        with self.assertRaises(Failure) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)

    def test_constructor_begin_and_pump_do_not_connect_but_serve_glib(self):
        self.begin()
        self.loop.pending.side_effect = [True, False]
        self.ax.pump()
        self.loop.iteration.assert_called_once_with(False)
        self.connector.assert_not_called()
        self.Atspi.init.assert_not_called()
        self.ax.close()
        self.ax.close()
        self.Atspi.exit.assert_not_called()

    def test_focus_event_candidates_are_bounded_and_retired_individually(self):
        self.begin()
        self.ax.applications([])
        objects = [object() for _ in range(34)]

        def event(obj, kind="object:state-changed:focused", active=1):
            self.ax.event(NS(type=kind, source=obj, detail1=active))

        for obj in objects:
            event(obj)
        self.assertEqual(list(self.ax.focus_events.values()), objects[-32:])
        event(objects[3])
        self.assertIs(list(self.ax.focus_events.values())[-1], objects[3])
        event(objects[4], active=0)
        event(objects[5], kind="object:state-changed:defunct")
        event(objects[6], kind="window:destroy")
        self.assertEqual(len(self.ax.focus_events), 30)
        self.assertNotIn(self.ax.native_key(objects[4]), self.ax.focus_events)
        self.assertNotIn(
            self.ax.native_key(objects[5]),
            self.ax.dead,
            "Local proxy disposal is not evidence of remote target destruction",
        )
        self.ax.close()
        self.assertEqual(self.ax.focus_events, {})
        self.Atspi.exit.assert_called_once_with()

    def test_defunct_lifecycle_event_never_materializes_disposing_source(self):
        self.begin()
        self.ax.applications([])

        class DisposingEvent:
            type = "object:state-changed:defunct"

            @property
            def source(self):
                raise AssertionError("A disposing GI source must not be materialized")

        self.ax.event(DisposingEvent())
        self.Atspi.Accessible.clear_cache_single.assert_not_called()
        self.assertEqual(self.ax.dead, {})

    def test_real_focus_loss_invalidates_context_without_native_queries(self):
        self.begin()
        self.ax.applications([])
        forgotten = []
        self.ax.focus_lost = lambda: forgotten.append(True)
        self.ax.event(NS(type="object:state-changed:focused", source=object(), detail1=1))
        self.assertEqual(forgotten, [])
        self.Atspi.Accessible.clear_cache_single.reset_mock()
        self.ax.event(NS(type="object:state-changed:focused", source=object(), detail1=0))
        self.assertEqual(forgotten, [True])
        self.Atspi.Accessible.clear_cache_single.assert_called_once()
        self.ax.close()
        self.assertIsNone(self.ax.focus_lost)

    def test_live_rejects_real_stale_state_role_and_bus_without_defunct_event(self):
        self.begin()
        self.ax.applications([])
        self.Atspi.StateType.DEFUNCT = "defunct"
        self.Atspi.Role.INVALID = "invalid"
        obj = object()
        self.Atspi.Accessible.get_state_set = Mock(return_value=NS(contains=lambda flag: False))
        self.Atspi.Accessible.get_role = Mock(return_value="entry")
        self.ax.live(obj)
        for failure in ("defunct", "invalid-role", "unreachable"):
            with self.subTest(failure=failure):
                self.assertEqual(self.ax.dead, {}, "No event-derived dead cache may be needed")
                self.Atspi.Accessible.get_state_set.side_effect = None
                self.Atspi.Accessible.get_state_set.return_value = NS(
                    contains=lambda flag, *, failure=failure: failure == "defunct"
                )
                self.Atspi.Accessible.get_role.return_value = "invalid" if failure == "invalid-role" else "entry"
                if failure == "unreachable":
                    self.Atspi.Accessible.get_state_set.side_effect = NativeError("Remote object no longer exists")
                self.assert_failure("stale_target", lambda: self.ax.live(obj))
        self.assertGreaterEqual(self.Atspi.Accessible.clear_cache_single.call_count, 4)

    def test_actual_v5_hello_with_native_backend_does_not_require_ax_bus(self):
        self.connector.side_effect = Failure("bus unavailable", "observation_unavailable")
        desktop = NS(can_capture=False, can_input=False, close=Mock())
        with patch("computer_use_linux.runtime.native_backend.connect_desktop", return_value=desktop):
            out = io.StringIO()
            server = Server(NativeBackend, output=out)
            server.execute(dict(version=6, id=0, method="hello", params=dict(desktopMode="shared")), context())
            try:
                self.assertIn("result", json.loads(out.getvalue()))
                self.connector.assert_not_called()
                self.Atspi.init.assert_not_called()
            finally:
                if server.native:
                    server.native.close()
                if server.backend:
                    server.backend.close()

    def test_first_native_access_initializes_once_and_close_releases_once(self):
        self.begin()
        for _ in range(2):
            self.ax.applications([])
        self.connector.assert_called_once_with(self.Gio, self.GLib)
        self.Atspi.init.assert_called_once_with()
        self.assertEqual([c.args[0] for c in self.listener.register.call_args_list], list(self.ax.events))
        self.ax.close()
        self.ax.close()
        self.assertEqual([c.args[0] for c in self.listener.deregister.call_args_list], list(self.ax.events))
        self.Atspi.exit.assert_called_once_with()
        self.assert_failure("observation_unavailable", lambda: self.ax.A)
        self.Atspi.init.assert_called_once_with()

    def test_bus_failure_is_retained_without_reconnect_or_init(self):
        self.connector.side_effect = Failure("bus unavailable", "observation_unavailable")
        self.begin()
        for _ in range(2):
            self.assert_failure("observation_unavailable", lambda: self.ax.applications([]))
            self.ax.begin(context())
            self.ax.pump()
        self.connector.assert_called_once()
        self.Atspi.init.assert_not_called()
        self.ax.close()
        self.Atspi.exit.assert_not_called()

    def test_glib_connection_failure_is_retained(self):
        self.connector.side_effect = NativeError("bus disconnected")
        self.begin()
        for _ in range(2):
            self.assert_failure("observation_unavailable", lambda: self.ax.A)
        self.connector.assert_called_once()

    def test_init_failure_and_external_initialized_status_are_not_owned(self):
        for status in (1, 2, -1):
            with self.subTest(status=status):
                ax = NativeAccessibility()
                ax.begin(context())
                self.Atspi.init.return_value = status
                self.assert_failure("observation_unavailable", lambda *, ax=ax: ax.A)
                ax.close()
                ax.close()
        self.listener.register.assert_not_called()
        self.Atspi.exit.assert_not_called()

    def test_partial_listener_failure_releases_only_successful_registrations(self):
        self.listener.register.side_effect = [True, NativeError("registration unavailable")]
        self.begin()
        self.assert_failure("observation_unavailable", lambda: self.ax.A)
        self.listener.deregister.assert_called_once_with(self.ax.events[0])
        self.Atspi.exit.assert_called_once_with()
        self.ax.close()
        self.ax.close()
        self.Atspi.exit.assert_called_once_with()

    def test_listener_false_result_and_null_listener_do_not_become_ready(self):
        self.listener.register.return_value = False
        self.begin()
        self.assert_failure("observation_unavailable", lambda: self.ax.A)
        self.listener.deregister.assert_not_called()
        self.Atspi.exit.assert_called_once_with()
        self.Atspi.EventListener.new.return_value = None
        ax = NativeAccessibility()
        ax.begin(context())
        self.assert_failure("observation_unavailable", lambda: ax.A)
        ax.close()
        self.assertEqual(self.Atspi.exit.call_count, 2)

    def test_request_cancel_and_deadline_during_connection_do_not_poison_provider(self):
        for code in ("cancelled", "deadline_exceeded"):
            with self.subTest(code=code):
                ax = NativeAccessibility()
                ctx = context()
                ax.begin(ctx)

                def interrupted(*_, code=code, ctx=ctx):
                    if code == "cancelled":
                        ctx.cancelled.set()
                    else:
                        ctx.deadline = 0
                    raise NativeError("connection was interrupted")

                self.connector.side_effect = interrupted
                self.assert_failure(code, lambda *, ax=ax: ax.A)
                self.connector.side_effect = None
                ax.begin(context())
                self.assertIs(ax.A, self.Atspi)
                ax.close()

    def test_cancellation_after_init_and_after_registration_rolls_back_resources(self):
        for phase in ("init", "register"):
            with self.subTest(phase=phase):
                ax = NativeAccessibility()
                ctx = context()
                ax.begin(ctx)
                self.Atspi.exit.reset_mock()
                self.listener.deregister.reset_mock()

                def init(*, ctx=ctx, phase=phase):
                    if phase == "init":
                        ctx.cancelled.set()
                    return 0

                def register(_, *, ctx=ctx):
                    ctx.cancelled.set()
                    return True

                self.Atspi.init.side_effect = init
                self.listener.register.side_effect = register
                self.assert_failure("cancelled", lambda *, ax=ax: ax.A)
                self.Atspi.exit.assert_called_once()
                self.assertEqual(self.listener.deregister.call_count, 1 if phase == "register" else 0)
                ax.close()
                self.Atspi.exit.assert_called_once()

    def test_unknown_failure_is_not_relabelled_as_provider_unavailable(self):
        self.begin()
        self.connector.side_effect = Failure("bad request", "invalid_request")
        self.assert_failure("invalid_request", lambda: self.ax.A)
        self.connector.side_effect = None
        self.assertIs(self.ax.A, self.Atspi)

    def test_native_call_rechecks_request_after_synchronous_completion(self):
        ctx = self.begin()
        fn = Mock(side_effect=lambda: ctx.cancelled.set())
        self.assert_failure("cancelled", lambda: self.ax.call(fn))
        fn.assert_called_once()

    def test_native_failure_does_not_mask_cancel_during_the_call(self):
        ctx = self.begin()

        def interrupted():
            ctx.cancelled.set()
            raise NativeError("call interrupted")

        self.assert_failure("cancelled", lambda: self.ax.call(interrupted))

    def test_glib_event_pump_uses_no_old_request_deadline(self):
        ctx = self.begin()
        self.assertIs(self.ax.A, self.Atspi)
        ctx.deadline = 0
        obj = object()
        self.ax.event(NS(source=obj, type="window:destroy"))
        self.Atspi.Accessible.clear_cache_single.assert_called_once_with(obj)
        self.assertIn(hash(obj), self.ax.dead)

    def test_deregister_error_does_not_prevent_owned_exit(self):
        self.begin()
        self.assertIs(self.ax.A, self.Atspi)
        self.listener.deregister.side_effect = NativeError("listener already disconnected")
        self.ax.close()
        self.ax.close()
        self.assertEqual(self.listener.deregister.call_count, len(self.ax.events))
        self.Atspi.exit.assert_called_once_with()

    def make_directory(self):
        registry = Registry()
        window = Window(123, "No accessibility", "Fixture", 42, (0, 0, 100, 100), process_start="birth")
        desktop = NS(windows=lambda **_: [window], validate=Mock(side_effect=lambda item: item.window))
        backend = NS(ax=self.ax, desktop=desktop, registry=registry, installed=lambda: [])
        return NativeDirectory(backend, NativeStore(self.ax), "session"), desktop

    def test_unavailable_provider_retains_native_window_identity_and_explicit_gap(self):
        self.connector.side_effect = Failure("selected bus unavailable", "observation_unavailable")
        ctx = self.begin()
        directory, _ = self.make_directory()
        apps = directory.applications(ctx)
        self.assertEqual(len(apps["applications"]), 1)
        self.assertIn("selected bus unavailable", "\n".join(apps["stopReasons"]))
        app_id = apps["applications"][0]["id"]
        first = directory.read(app_id, ctx)
        second = directory.read(app_id, ctx)
        self.assertEqual(first["surfaces"], second["surfaces"])
        row = first["surfaces"][0]
        self.assertTrue(row["nativeWindow"])
        self.assertIsNone(row["node"])
        self.assertFalse(row["accessibility"])
        self.assertEqual(first["provider"]["status"], "unavailable")
        self.assertIn("selected bus unavailable", "\n".join(first["stopReasons"]))
        self.connector.assert_called_once()

    def test_application_and_window_list_outer_failures_preserve_native_windows(self):
        ctx = self.begin()
        directory, _ = self.make_directory()
        for name in ("applications", "windows"):
            with self.subTest(name=name), patch.object(self.ax, name, side_effect=NativeError("list failed")):
                apps = directory.applications(ctx)
                result = directory.read(apps["applications"][0]["id"], ctx)
                self.assertEqual(len(result["surfaces"]), 1)
                self.assertIn("list failed", "\n".join(result["stopReasons"]))

    def test_directory_propagates_global_failures_but_preserves_partial_window_results(self):
        ctx = self.begin()
        directory, desktop = self.make_directory()
        apps = directory.applications(ctx)
        app_id = apps["applications"][0]["id"]
        for name in ("applications", "windows"):
            for code in ("invalid_request", "cancelled", "deadline_exceeded", "stale_target"):
                with self.subTest(name=name, code=code), patch.object(self.ax, name, side_effect=Failure("stop", code)):
                    self.assert_failure(code, lambda: directory.read(app_id, ctx))
        original = directory.read(app_id, ctx)["surfaces"][0]
        desktop.validate.side_effect = Failure("closed native window", "stale_target")
        current = directory.read(app_id, ctx)
        self.assertEqual(current["surfaces"], [])
        self.assertIn("closed native window", "\n".join(current["stopReasons"]))
        target = dict(
            instance=original["instance"], rootWindow=original["window"], surface=original["surface"], node=None
        )
        self.assert_failure("stale_target", lambda: directory.validate(target, ctx))

    def test_directory_checks_cancel_after_successful_enumeration(self):
        ctx = self.begin()
        directory, _ = self.make_directory()

        def cancel(**_):
            ctx.cancelled.set()
            return []

        with patch.object(self.ax, "applications", side_effect=cancel):
            self.assert_failure("cancelled", lambda: directory.applications(ctx))

    def test_unrelated_defunct_ax_app_does_not_remove_live_native_window(self):
        ctx = self.begin()
        directory, _ = self.make_directory()
        dead_app = object()
        with (
            patch.object(self.ax, "applications", return_value=[dead_app]),
            patch.object(self.ax, "live", side_effect=Failure("AX application exited", "stale_target")),
        ):
            # Real windows() observes the same defunct application and marks a
            # gap; that optional observation does not invalidate an X11 window.
            apps = directory.applications(ctx)
            result = directory.read(apps["applications"][0]["id"], ctx)
        self.assertEqual(len(result["surfaces"]), 1)
        self.assertIsNone(result["surfaces"][0]["node"])
        self.assertIn("AX application exited", "\n".join(result["stopReasons"]))

    def test_defunct_ax_root_is_optional_but_input_live_still_fails(self):
        ctx = self.begin()
        directory, desktop = self.make_directory()
        apps = directory.applications(ctx)
        dead_root = object()
        native = desktop.windows()[0]
        native.accessible = dead_root
        with patch.object(self.ax, "live", side_effect=Failure("AX root exited", "stale_target")):
            result = directory.read(apps["applications"][0]["id"], ctx)
            self.assertEqual(len(result["surfaces"]), 1)
            self.assertIsNone(result["surfaces"][0]["node"])
            self.assertIn("AX root exited", "\n".join(result["stopReasons"]))
            self.assert_failure("stale_target", lambda: self.ax.live(dead_root))


if __name__ == "__main__":
    unittest.main()
