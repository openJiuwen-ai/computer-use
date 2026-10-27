"""Linux key/focus mechanisms using the real driver and public SDK pipeline.

Only native AT-SPI objects and X server effects are substituted. Keyboard event
sequencing, final guards, paired cleanup, RPC and Python policy are production
code; real desktop acceptance lives in tools/acceptance/v5-linux-key.py.
"""

import sys
import unittest
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import patch

from fixture_driver import environmentDriver
from linux_native_provider import KeyboardBackend, NativeError, Node
from repository import ROOT as REPO_ROOT

import backends.linux.test_linux_native as read_tests
from backends.linux.test_linux_native import context
from computer_use.client.factory import nativeComputer
from computer_use.diagnostics import setDiagnosticHandler
from computer_use.errors import ComputerUseError
from computer_use.native.connection import Connection
from computer_use.native.contracts import HostPolicy
from computer_use.native.transport import ConnectionPort
from computer_use.protocol.execution import decodeExecutionResult
from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.objects.registry import Window
from computer_use_linux.runtime.native import NativeExecution
from computer_use_linux.system.x11 import X11

ROOT = REPO_ROOT


class LinuxNativeKeyboardTests(unittest.TestCase):
    tearDown = read_tests.LinuxNativeFactsTests.tearDown
    begin = read_tests.LinuxNativeFactsTests.begin
    inspect = read_tests.LinuxNativeFactsTests.inspect
    surfaces = read_tests.LinuxNativeFactsTests.surfaces
    target = read_tests.LinuxNativeFactsTests.target
    collect = read_tests.LinuxNativeFactsTests.collect

    def setUp(self):
        self.backend = KeyboardBackend()
        self.backend.desktop.assist = False
        self.driver = NativeExecution(self.backend, "session")
        self.pid = patch("computer_use_linux.windowing.native_directory.process_start", return_value="generation")
        self.pid.start()

    def ready(self):
        self.backend.desktop.foreground = self.backend.desktop.keyboard_window = 11
        self.backend.editor.states.add("FOCUSED")
        self.backend.other_editor.states.discard("FOCUSED")

    def key_setup(self, key="Return", *, kind="key", window_scope=False):
        target = self.target()
        batch = self.collect(target)
        ref = next(n["node"] for n in batch["nodes"] if n["values"].get("name") == "Input")
        self.selected = dict(target, node=None if window_scope else ref)
        parameters = {} if kind == "focus" else dict(key=key)
        self.operation = self.driver.perform(
            "begin", dict(mode="write", target=self.selected, intent=dict(kind=kind, parameters=parameters)), context()
        )["operation"]
        return self.inspect_input()

    def inspect_input(self):
        return decodeExecutionResult(
            "inspect",
            self.driver.perform(
                "inspect",
                dict(operation=self.operation, target=self.selected, query=dict(kind="inputContext", within=[])),
                context(),
            ),
        )

    def command(self, facts, *, prepare=False, foreground=False):
        primitive = facts["primitives"][0]
        return dict(
            kind="prepare_receiver" if prepare else primitive["unit"],
            primitiveId=primitive["id"],
            target=self.selected,
            foreground=foreground,
        )

    def send(self, command, step=1, ctx=None):
        return decodeExecutionResult(
            "execute",
            self.driver.perform(
                "execute", dict(operation=self.operation, step=step, command=command), ctx or context()
            ),
        )

    def focus_calls(self):
        return [call for call in self.backend.ax.calls if call[0] == "grab_focus"]

    def test_focused_accessibility_node_does_not_prove_actual_keyboard_window(self):
        self.backend.editor.states.add("FOCUSED")
        facts = self.key_setup()
        self.assertEqual(facts["input"]["focusedNode"], self.selected["node"])
        self.assertEqual(facts["input"]["windowRoute"], "no")
        self.assertEqual(facts["primitives"][0]["readiness"], "no")
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["errorCode"]), ("notDispatched", "input_unavailable"))
        self.assertEqual(self.backend.desktop.events, [])
        self.assertFalse(self.driver.current.dispatched)

    def test_background_preparation_only_uses_addressed_focus_and_never_sends_key(self):
        facts = self.key_setup()
        ctx = context()
        result = self.send(self.command(facts, prepare=True), ctx=ctx)
        self.assertEqual(result["dispatch"], "notDispatched")
        self.assertEqual(result["retryReason"], "preparationUnavailable")
        self.assertEqual(result["preparation"], ["nativeFocus"])
        self.assertEqual(ctx.effect, "possible", "Preparation effects are distinct from the requested key")
        self.assertEqual(len(self.focus_calls()), 1)
        self.assertEqual(self.backend.desktop.activations, [])
        self.assertEqual(self.backend.desktop.events, [])
        self.assertEqual(self.backend.desktop.keyboard_window, 22)
        self.assertFalse(self.driver.current.dispatched)
        self.assertEqual(self.inspect_input()["primitives"][0]["readiness"], "no")

    def test_native_focus_may_self_activate_without_driver_foreground_fallback(self):
        original = self.backend.ax.hooks["grab_focus"]

        def focus(obj):
            original(obj)
            self.backend.desktop.foreground = self.backend.desktop.keyboard_window = 11

        self.backend.ax.hooks["grab_focus"] = focus
        facts = self.key_setup(kind="focus")
        result = self.send(self.command(facts, prepare=True))
        self.assertEqual((result["dispatch"], result["confirmation"]), ("notDispatched", "confirmed"))
        self.assertEqual(result["preparation"], ["nativeFocus"])
        self.assertEqual(self.backend.desktop.activations, [])
        self.assertEqual(self.backend.desktop.events, [])

    def test_foreground_permission_and_prior_background_attempt_are_both_required(self):
        self.backend.desktop.assist = True
        facts = self.key_setup()
        rejected = self.send(self.command(facts, prepare=True, foreground=True))
        self.assertEqual((rejected["dispatch"], rejected["errorCode"]), ("notDispatched", "invalid_request"))
        self.assertEqual(self.backend.desktop.activations, [])
        failed = self.send(self.command(facts, prepare=True), step=2)
        self.assertEqual(failed["retryReason"], "preparationUnavailable")
        fresh = self.inspect_input()
        self.assertNotEqual(facts["primitives"][0]["id"], fresh["primitives"][0]["id"])
        prepared = self.send(self.command(fresh, prepare=True, foreground=True), step=3)
        self.assertEqual(prepared["confirmation"], "confirmed")
        self.assertEqual(self.backend.desktop.activations, [11])
        self.assertEqual(self.backend.desktop.events, [])
        result = self.send(self.command(self.inspect_input()), step=4)
        self.assertEqual(result["dispatch"], "dispatched")
        self.assertEqual(self.backend.counter.text, "1")
        self.driver.finish(self.operation)
        self.assertEqual(self.backend.desktop.foreground, 11, "Closing the operation must not restore B")

    def test_host_policy_matrix_guards_foreground_preparation(self):
        for mode, assist, permitted in (("shared", False, False), ("shared", True, True), ("exclusive", False, True)):
            with self.subTest(mode=mode, assist=assist):
                self.backend.mode, self.backend.desktop.assist = mode, assist
                self.backend.desktop.foreground = self.backend.desktop.keyboard_window = 22
                facts = self.key_setup(window_scope=True)
                result = self.send(self.command(facts, prepare=True, foreground=True))
                self.assertEqual(result["confirmation"] == "confirmed", permitted)
                if not permitted:
                    self.assertEqual(result["errorCode"], "mode_unsupported")
                self.assertEqual(self.backend.desktop.foreground, 11 if permitted else 22)
                self.assertEqual(self.backend.desktop.events, [])
                self.driver.finish(self.operation)

    # Regression: a parent query can run a callback after the normal focus
    # sample. Continuous dispatch samples the pinned recipient again after
    # the scope guard; this does not make provider queries/XTEST atomic.
    def test_focus_changes_in_final_parent_query_stop_key_dispatch(self):
        self.ready()
        facts = self.key_setup()
        calls = 0

        def change(obj):
            nonlocal calls
            if obj is self.backend.editor:
                calls += 1
                # Two parent checks per guard; initial preflight plus final
                # guard immediately before the first requested key event.
                if calls == 4:
                    obj.states.discard("FOCUSED")
                    self.backend.button.states.add("FOCUSED")

        self.backend.ax.hooks["get_parent"] = change
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["errorCode"]), ("notDispatched", "input_unavailable"))
        self.assertEqual(self.backend.desktop.events, [])
        self.assertEqual(self.backend.counter.text, "0")

    def test_key_dispatch_cannot_smuggle_foreground_or_another_target(self):
        self.ready()
        facts = self.key_setup()
        for step, changes in enumerate(
            (dict(foreground=True), dict(primitiveId="forged"), dict(target=dict(self.selected, node=None))), 1
        ):
            with self.assertRaises(Failure):
                self.send({**self.command(facts), **changes}, step=step)
        self.assertEqual(self.backend.desktop.events, [])

    def test_layout_preflight_cannot_move_focus_or_partially_press_chord(self):
        facts = self.key_setup("ctrl+Return")
        self.backend.desktop.keymap_missing.add("Return")
        result = self.send(self.command(facts, prepare=True))
        self.assertEqual(result["dispatch"], "notDispatched")
        self.assertEqual(result["preparation"], [])
        self.assertEqual(self.focus_calls(), [])
        self.assertEqual(self.backend.desktop.events, [])

    def test_cancel_before_first_key_is_not_dispatched(self):
        self.ready()
        facts = self.key_setup("ctrl+Return")
        ctx = context()
        ctx.cancelled.set()
        result = self.send(self.command(facts), ctx=ctx)
        self.assertEqual((result["dispatch"], result["errorCode"]), ("notDispatched", "cancelled"))
        self.assertFalse(self.driver.current.dispatched)
        self.assertEqual(self.backend.desktop.events, [])

    def test_cancel_after_modifier_releases_owned_input_without_replay(self):
        self.ready()
        facts = self.key_setup("ctrl+Return")
        ctx = context()
        self.backend.desktop.event_hook = lambda kind, detail: ctx.cancelled.set() if kind == 2 else None
        result = self.send(self.command(facts), ctx=ctx)
        self.assertEqual(
            (result["dispatch"], result["errorCode"], result["cleanup"]), ("unknown", "cancelled", "complete")
        )
        self.assertEqual(self.backend.desktop.events, [(2, "ctrl"), (3, "ctrl")])
        self.assertFalse(self.backend.desktop.keys)
        self.assertEqual(self.backend.counter.text, "0")
        with self.assertRaises(Failure):
            self.send(self.command(facts), step=2)

    def test_focus_drift_after_modifier_only_releases_never_sends_remaining_key(self):
        self.ready()
        facts = self.key_setup("ctrl+Return")

        def move(kind, detail):
            if kind == 2:
                self.backend.desktop.keyboard_window = 22

        self.backend.desktop.event_hook = move
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["cleanup"]), ("unknown", "complete"))
        self.assertEqual(self.backend.desktop.events, [(2, "ctrl"), (3, "ctrl")])
        self.assertEqual(self.backend.counter.text, "0")
        self.assertEqual(self.backend.desktop.activations, [])

    def test_press_reply_lost_is_unknown_but_cleanup_still_releases_key(self):
        self.ready()
        facts = self.key_setup()

        def fail(kind, detail):
            if kind == 2:
                raise NativeError("X server reply was lost after accepting press")

        self.backend.desktop.event_hook = fail
        result = self.send(self.command(facts))
        self.assertEqual(
            (result["dispatch"], result["errorCode"], result["cleanup"]), ("unknown", "execution_unknown", "complete")
        )
        self.assertEqual(self.backend.desktop.events, [(2, "Return"), (3, "Return")])
        self.assertFalse(self.backend.desktop.keys)

    def test_release_failure_retains_ownership_and_failed_finish(self):
        self.ready()
        facts = self.key_setup()

        def fail(kind, detail):
            if kind == 3:
                raise NativeError("X server release unavailable")

        self.backend.desktop.event_hook = fail
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["cleanup"]), ("unknown", "failed"))
        self.assertEqual(self.backend.desktop.keys, {"Return"})
        self.assertIsNone(result["retryReason"])
        self.assertEqual(self.driver.finish(self.operation), dict(cleanup="failed"))
        self.backend.desktop.event_hook = None
        self.backend.desktop.release()
        self.assertFalse(self.backend.desktop.keys)

    def test_return_may_close_window_and_release_does_not_recheck_its_lifetime(self):
        self.ready()
        facts = self.key_setup()

        def close(kind, detail):
            if kind == 2:
                self.backend.desktop.items = self.backend.desktop.items[1:]
                self.backend.window.dead = True

        self.backend.desktop.event_hook = close
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["cleanup"]), ("dispatched", "complete"))
        self.assertEqual(self.backend.desktop.events, [(2, "Return"), (3, "Return")])

    def test_app_key_does_not_require_an_accessibility_root(self):
        self.ready()
        facts = self.key_setup(window_scope=True)
        self.backend.desktop.items[0].accessible = None
        self.backend.ax.windows = lambda *_: []
        result = self.send(self.command(facts))
        self.assertEqual(result["dispatch"], "dispatched")
        self.assertEqual(self.backend.counter.text, "1")
        self.assertEqual(self.focus_calls(), [])

    def test_actual_route_needs_active_window_and_input_focus_agreement(self):
        for active, receiver in ((11, 22), (22, 11)):
            with self.subTest(active=active, receiver=receiver):
                self.ready()
                facts = self.key_setup()
                self.backend.desktop.foreground, self.backend.desktop.keyboard_window = active, receiver
                result = self.send(self.command(facts))
                self.assertEqual(result["dispatch"], "notDispatched")
                self.assertEqual(self.backend.desktop.events, [])
                self.driver.finish(self.operation)

    def test_scope_modal_disabled_locked_and_role_changes_stop_before_key(self):
        for mode in ("parent", "modal", "disabled", "locked", "role"):
            with self.subTest(mode=mode):
                self.ready()
                facts = self.key_setup()
                if mode == "parent":
                    self.backend.editor.parent = self.backend.other_window
                elif mode == "modal":
                    self.backend.desktop.items.append(
                        Window(
                            33,
                            "Modal",
                            "child",
                            43,
                            (0, 0, 100, 100),
                            modal=True,
                            mapped=True,
                            transient=11,
                            process_start="other",
                            accessible=Node("dialog"),
                        )
                    )
                elif mode == "disabled":
                    self.backend.editor.states -= {"ENABLED", "SENSITIVE"}
                elif mode == "locked":
                    self.backend.locked = True
                else:
                    self.backend.editor.role = "push button"
                result = self.send(self.command(facts))
                self.assertEqual(result["dispatch"], "notDispatched")
                self.assertEqual(self.backend.desktop.events, [])
                self.driver.finish(self.operation)
                self.backend.editor.parent = self.backend.window
                self.backend.desktop.items = self.backend.desktop.items[:2]
                self.backend.editor.states |= {"ENABLED", "SENSITIVE"}
                self.backend.locked, self.backend.editor.role = False, "entry"

    def test_focus_changes_in_role_callback_refresh_the_actual_same_window_recipient(self):
        self.ready()
        facts = self.key_setup()

        def change(obj):
            if obj is self.backend.editor:
                obj.states.discard("FOCUSED")
                self.backend.button.states.add("FOCUSED")

        self.backend.ax.hooks["get_role_name"] = change
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["errorCode"]), ("notDispatched", "input_unavailable"))
        self.assertEqual(self.backend.desktop.foreground, 11)
        self.assertEqual(self.backend.desktop.keyboard_window, 11)
        self.assertEqual(self.backend.desktop.events, [])

    def test_user_held_input_prevents_both_native_focus_and_foreground_activation(self):
        self.backend.desktop.assist = True
        for foreground in (False, True):
            with self.subTest(foreground=foreground):
                facts = self.key_setup(window_scope=foreground)
                with patch.object(
                    self.backend.desktop,
                    "require_idle_input",
                    side_effect=Failure("User is holding a key", "input_unavailable"),
                ):
                    result = self.send(self.command(facts, prepare=True, foreground=foreground))
                self.assertEqual((result["dispatch"], result["cleanup"]), ("notDispatched", "complete"))
                self.assertEqual(result["preparation"], [])
                self.assertEqual(self.backend.desktop.activations, [])
                self.assertEqual(self.focus_calls(), [])
                self.driver.finish(self.operation)

    def test_background_receipt_does_not_depend_on_json_binding_key_order(self):
        self.backend.desktop.assist = True
        facts = self.key_setup()
        result = self.send(self.command(facts, prepare=True))
        self.assertEqual(result["retryReason"], "preparationUnavailable")
        self.selected = dict(reversed(list(self.selected.items())))
        result = self.send(self.command(self.inspect_input(), prepare=True, foreground=True), step=2)
        self.assertEqual(result["confirmation"], "confirmed")
        self.assertEqual(self.backend.desktop.activations, [11])

    def test_async_xtest_rejection_is_reported_and_release_ownership_is_retained(self):
        self.ready()
        facts = self.key_setup()
        desktop = self.backend.desktop
        requests, pending = [], []

        def fake_input(**request):
            requests.append(request)
            if request["event_type"] == desktop.X.KeyRelease:
                pending.append(request)

        def sync():
            while pending:
                request = pending.pop(0)
                request["onerror"](NativeError("asynchronous BadAccess"), request)

        xtest = NS(extname="XTEST", FakeInput=fake_input)
        ext = ModuleType("Xlib.ext")
        ext.xtest = xtest
        desktop.d = NS(display=NS(get_extension_major=lambda name: 132), sync=sync)
        desktop.root = NS(id=1)
        desktop.X = NS(KeyPress=2, KeyRelease=3, ButtonRelease=5, CurrentTime=0)
        desktop._event = lambda *args, **kw: X11._event(desktop, *args, **kw)
        with patch.dict(sys.modules, {"Xlib": ModuleType("Xlib"), "Xlib.ext": ext}):
            result = self.send(self.command(facts))
        self.assertEqual(
            (result["dispatch"], result["errorCode"], result["cleanup"]), ("unknown", "execution_unknown", "failed")
        )
        self.assertEqual([r["event_type"] for r in requests], [2, 3, 3])
        self.assertEqual(desktop.keys, {"Return"})
        self.assertIn("synthetic input", result["detail"])
        self.assertEqual(self.driver.finish(self.operation), dict(cleanup="failed"))
        desktop._event = lambda *_: None
        desktop.release()
        self.assertFalse(desktop.keys)

    def test_repeated_step_even_after_finish_returns_receipt_without_native_replay(self):
        self.ready()
        facts = self.key_setup()
        command = self.command(facts)
        result = self.send(command)
        self.driver.finish(self.operation)
        self.assertEqual(self.send(command), result)
        self.assertEqual(self.backend.counter.text, "1")
        self.assertEqual(self.backend.desktop.events, [(2, "Return"), (3, "Return")])
        with self.assertRaises(Failure):
            self.send(dict(command, foreground=True))

    def test_read_only_scope_and_invalid_keys_cannot_acquire_keyboard_contract(self):
        target = self.target()
        for kind, parameters in (
            ("key", {"key": "ctrl+ctrl+Return"}),
            ("key", {"key": "Return", "text": "x"}),
            ("key", {"key": "CTRL+p"}),
            ("focus", {"foreground": True}),
        ):
            with self.assertRaises(Failure):
                self.driver.perform(
                    "begin", dict(mode="write", target=target, intent=dict(kind=kind, parameters=parameters)), context()
                )
        self.assertEqual(self.backend.leases, 0)
        self.selected, self.operation = target, self.begin(target)
        with self.assertRaises(Failure):
            self.inspect_input()
        self.assertEqual(self.backend.desktop.events, [])


class LinuxNativeKeyboardSDKTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.connections, self.diagnostics = [], []
        setDiagnosticHandler(self.diagnostics.append)

    async def asyncTearDown(self):
        setDiagnosticHandler(None)
        for connection in self.connections:
            connection.close()
        for connection in self.connections:
            await connection.waitClosed()

    async def connect(self, *, assist=False, self_activate=False):
        args = [
            "COMPUTER_USE_DESKTOP=shared",
            "COMPUTER_USE_FOREGROUND_ASSIST=" + ("1" if assist else "0"),
            sys.executable,
            str(ROOT / "tests/fixtures/linux_native_provider.py"),
            "--keyboard",
        ]
        if self_activate:
            args.append("--self-activate")
        connection = Connection(lambda: None, environmentDriver(args, timeoutMs=5000), protocol_version=6)
        self.connections.append(connection)
        computer = nativeComputer(ConnectionPort(connection, HostPolicy("shared", assist)))
        app = computer.app((await computer.listApps())[0].id)
        await app.read(view="both")
        await app.read(view="both")
        return connection, app, (await app.element([{"name": "Input"}]))

    def commands(self):
        return [
            e["params"]["command"]
            for e in self.diagnostics
            if e["kind"] == "command_start" and e["method"] == "execute"
        ]

    async def test_shared_refusal_does_not_submit_and_session_still_reads(self):
        connection, app, field = await self.connect()
        with self.assertRaises(ComputerUseError) as raised:
            await field.pressKey("Return")
        self.assertEqual(raised.exception.code, "input_unavailable")
        (self.assertEqual((await (await app.element([{"name": "Submissions"}])).readText()).text, "0"))
        commands = self.commands()
        self.assertEqual([(c["kind"], c["foreground"]) for c in commands], [("prepare_receiver", False)])
        self.assertIn("nativeKeyboardIntents", connection.capabilities)
        (self.assertIn("Input", (await app.read(view="both")).text))

    async def test_authorized_sdk_background_then_foreground_then_one_key(self):
        _, app, field = await self.connect(assist=True)
        result = await field.pressKey("Return")
        self.assertEqual(result.status, "dispatched")
        self.assertFalse(hasattr(result, "text"))
        self.assertEqual(
            [(c["kind"], c["foreground"]) for c in self.commands()],
            [("prepare_receiver", False), ("prepare_receiver", True), ("key_sequence", False)],
        )
        (self.assertEqual((await (await app.element([{"name": "Submissions"}])).readText()).text, "1"))
        before = len(self.commands())
        result = await field.focus()
        self.assertEqual(result.status, "verified")
        self.assertFalse(hasattr(result, "text"))
        self.assertLessEqual(len(self.commands()) - before, 1)
        result = await app.pressKey("Return")
        self.assertEqual(result.status, "dispatched")
        (self.assertEqual((await (await app.element([{"name": "Submissions"}])).readText()).text, "2"))
        self.assertEqual(
            sum(c["foreground"] for c in self.commands()), 1, "No restoration or extra foreground fallback"
        )

    async def test_native_self_activation_satisfies_shared_without_explicit_foreground(self):
        _, app, field = await self.connect(self_activate=True)
        result = await field.focus()
        self.assertEqual(result.status, "verified")
        result = await field.pressKey("Return")
        self.assertEqual(result.status, "dispatched")
        (self.assertEqual((await (await app.element([{"name": "Submissions"}])).readText()).text, "1"))
        self.assertEqual(
            [(c["kind"], c["foreground"]) for c in self.commands()],
            [("prepare_receiver", False), ("key_sequence", False)],
        )


if __name__ == "__main__":
    unittest.main()
