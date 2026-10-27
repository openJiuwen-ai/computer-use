"""Image-bound v5 pointer mechanisms and public SDK, with OS effects substituted.

No RPC/policy/execution-unit mocks: native pixels/window hits/events are seams;
production mapping, contracts, guards, XTEST sequencing and releases run here.
"""

import json
import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fixture_driver import environmentDriver
from linux_native_provider import NativeError, PointerBackend

import backends.linux.test_linux_native as base
import backends.linux.test_linux_native_capture as capture_tests
from computer_use.client.factory import nativeComputer
from computer_use.diagnostics import setDiagnosticHandler
from computer_use.errors import ComputerUseError
from computer_use.native.connection import Connection
from computer_use.native.contracts import HostPolicy
from computer_use.native.transport import ConnectionPort
from computer_use.protocol.captures import captureMetadata
from computer_use.protocol.execution import decodeExecutionResult
from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.objects.registry import Window
from computer_use_linux.runtime.native import NativeExecution


class LinuxNativePointerTests(unittest.TestCase):
    begin = capture_tests.LinuxCaptureTests.begin
    read = capture_tests.LinuxCaptureTests.read
    tearDown = capture_tests.LinuxCaptureTests.tearDown

    def setUp(self):
        self.b = PointerBackend()
        self.b.desktop.assist = False
        self.driver = NativeExecution(self.b, "session")
        self.pid = patch("computer_use_linux.windowing.native_directory.process_start", return_value="generation")
        self.pid.start()
        ctx = base.context()
        self.application = self.driver.directory.applications(ctx)["applications"][0]["id"]
        row = self.driver.directory.read(self.application, ctx)["surfaces"][0]
        self.target = base.scope(instance=row["instance"], rootWindow=row["window"], surface=row["surface"])

    def image(self, *, desktop=False):
        result = self.read(target=base.scope() if desktop else self.target, kind="desktop" if desktop else "window")
        png = result["screenshot" if desktop else "image"]
        return dict(self.driver.captures.entries[captureMetadata(png)["id"]].target)

    def setup_pointer(self, kind="click", parameters=None, *, target=None, inspect=True):
        self.selected = target if target is not None else self.image()
        self.operation = self.driver.perform(
            "begin",
            dict(mode="write", target=self.selected, intent=dict(kind=kind, parameters=parameters or dict(x=10, y=20))),
            base.context(),
        )["operation"]
        return self.inspect_input() if inspect else None

    def inspect_input(self):
        return decodeExecutionResult(
            "inspect",
            self.driver.perform(
                "inspect",
                dict(operation=self.operation, target=self.selected, query=dict(kind="inputContext", within=[])),
                base.context(),
            ),
        )

    def command(self, facts, *, prepare=False, foreground=False):
        p = facts["primitives"][0]
        return dict(
            kind="prepare_receiver" if prepare else p["unit"],
            primitiveId=p["id"],
            target=self.selected,
            foreground=foreground,
        )

    def send(self, command, step=1, ctx=None):
        return decodeExecutionResult(
            "execute",
            self.driver.perform(
                "execute", dict(operation=self.operation, step=step, command=command), ctx or base.context()
            ),
        )

    def test_no_ax_background_image_click_needs_neither_activation_nor_keyboard_focus(self):
        selected = self.image()
        with patch.object(self.b.ax, "call", side_effect=AssertionError("Image input must not query AX")):
            facts = self.setup_pointer(target=selected)
            self.assertEqual((facts["input"]["pointerRoute"], facts["primitives"][0]["readiness"]), ("yes", "yes"))
            result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["cleanup"]), ("dispatched", "complete"))
        self.assertEqual(self.b.desktop.events, [(6, 0, 25, 50), (4, 1, 0, 0), (5, 1, 0, 0)])
        self.assertEqual((self.b.desktop.foreground, self.b.desktop.keyboard_window), (22, 22))
        self.assertEqual(self.b.desktop.activations, [])
        self.assertEqual(result["preparation"], [])

    def test_original_capture_is_bound_to_scope_and_never_rebound_to_new_pixels(self):
        selected = self.image()
        for changed in (
            {"capture": "missing"},
            {"surface": "different"},
            {"instance": "different"},
            {"rootWindow": "different"},
            {"node": "forged"},
            {"session": "other"},
        ):
            with self.subTest(changed=changed), self.assertRaises(Failure):
                self.setup_pointer(target={**selected, **changed})
        self.assertEqual(self.b.leases, 0)
        self.b.desktop.items[0].rect = (30, 20, 640, 480)
        new = self.image()
        self.assertNotEqual(selected["capture"], new["capture"])
        with self.assertRaises(Failure) as error:
            self.setup_pointer(target=selected)
        self.assertEqual(error.exception.code, "stale_target")
        self.assertEqual(self.b.desktop.events, [])

    def test_evicted_capture_cannot_acquire_a_write_lease(self):
        selected = self.image()
        for _ in range(64):
            self.read(dimension=1)
        with self.assertRaises(Failure) as error:
            self.setup_pointer(target=selected)
        self.assertEqual(error.exception.code, "stale_target")
        self.assertEqual(self.b.leases, 0)

    def test_covered_point_requires_explicit_foreground_permission_and_no_input_during_prepare(self):
        self.b.desktop.covered = True
        facts = self.setup_pointer()
        self.assertEqual(facts["primitives"][0]["pending"], ["foreground"])
        refused = self.send(self.command(facts, prepare=True))
        self.assertEqual((refused["dispatch"], refused["retryReason"]), ("notDispatched", "preparationUnavailable"))
        denied = self.send(self.command(facts, prepare=True, foreground=True), 2)
        self.assertEqual((denied["dispatch"], denied["errorCode"]), ("notDispatched", "mode_unsupported"))
        self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(self.b.desktop.activations, [])
        self.b.desktop.assist = True
        prepared = self.send(self.command(facts, prepare=True, foreground=True), 3)
        self.assertEqual((prepared["confirmation"], prepared["preparation"]), ("confirmed", ["foreground"]))
        self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(self.b.desktop.activations, [11])
        result = self.send(self.command(self.inspect_input()), 4)
        self.assertEqual(result["dispatch"], "dispatched")
        self.driver.finish(self.operation)
        self.assertEqual(self.b.desktop.foreground, 11, "No automatic restoration")

    def test_unmanaged_visible_popup_can_be_clicked_but_cannot_be_activated_when_covered(self):
        self.b.desktop.items[0].override_redirect = True
        facts = self.setup_pointer()
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.driver.finish(self.operation)
        self.b.desktop.covered = True
        facts = self.setup_pointer()
        self.assertEqual(facts["primitives"][0]["supported"], "no")
        before = list(self.b.desktop.events)
        with self.assertRaises(Failure):
            self.send(self.command(facts, prepare=True, foreground=True))
        self.assertEqual(self.b.desktop.events, before)
        self.assertEqual(self.b.desktop.activations, [])

    def test_final_guard_detects_geometry_identity_lock_modal_and_hit_drift(self):
        for change in ("geometry", "identity", "lock", "modal", "covered", "unmapped"):
            with self.subTest(change=change):
                facts = self.setup_pointer()
                original = replace(self.b.desktop.items[0])
                if change == "geometry":
                    self.b.desktop.items[0].rect = (2, 3, 640, 480)
                elif change == "identity":
                    self.b.desktop.items[0] = replace(self.b.desktop.items[0], process_start="new")
                elif change == "lock":
                    self.b.locked = True
                elif change == "covered":
                    self.b.desktop.covered = True
                elif change == "unmapped":
                    self.b.desktop.items[0].mapped = False
                else:
                    self.b.desktop.items.append(
                        Window(
                            33,
                            "Modal",
                            self.b.app.name,
                            42,
                            (0, 0, 100, 100),
                            transient=11,
                            modal=True,
                            process_start="generation",
                        )
                    )
                result = self.send(self.command(facts))
                self.assertEqual(result["dispatch"], "notDispatched")
                self.assertEqual(self.b.desktop.events, [])
                self.driver.finish(self.operation)
                self.b.desktop.items = [original]
                self.b.locked = self.b.desktop.covered = False
                self.b.registry.instances[self.target["instance"]].alive = True

    def test_geometry_change_during_authorized_activation_never_reprojects_or_sends(self):
        self.b.desktop.covered = self.b.desktop.assist = True
        facts = self.setup_pointer()
        self.b.desktop.activate_hook = lambda: setattr(self.b.desktop.items[0], "rect", (1, 0, 640, 480))
        result = self.send(self.command(facts, prepare=True, foreground=True))
        self.assertEqual((result["dispatch"], result["errorCode"]), ("notDispatched", "stale_target"))
        self.assertEqual(result["preparation"], ["foreground"])
        self.assertEqual(self.b.desktop.events, [])

    def test_user_held_input_prevents_motion_or_foreground_preparation(self):
        for covered in (False, True):
            with self.subTest(covered=covered):
                self.b.desktop.covered, self.b.desktop.assist = covered, True
                facts = self.setup_pointer()
                self.b.desktop.user_held = True
                result = self.send(self.command(facts, prepare=covered, foreground=covered))
                self.assertEqual(result["dispatch"], "notDispatched")
                self.assertEqual(self.b.desktop.events, [])
                self.assertEqual(self.b.desktop.activations, [])
                self.driver.finish(self.operation)
                self.b.desktop.user_held = False

    def test_preflight_rejects_invalid_input_before_events_or_preparation(self):
        selected = self.image()
        self.b.desktop.covered = self.b.desktop.assist = True
        for kind, params in [
            ("click", dict(x=True, y=2)),
            ("click", dict(x=1, y=float("inf"))),
            ("click", dict(x=10**500, y=2)),
            ("click", dict(x=256, y=2)),
            ("click", dict(x=1, y=2, count=3)),
            ("scroll", dict(x=1, y=2, direction="down", pages=101)),
            ("drag", dict(fromX=1, fromY=2, toX=-1, toY=3)),
        ]:
            with self.subTest(kind=kind, params=str(params)[:80]), self.assertRaises(Failure):
                self.setup_pointer(kind, params, target=selected)
            if self.driver.current:
                self.driver.finish(self.operation)
        self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(self.b.desktop.activations, [])

    def test_right_double_click_and_scroll_use_paired_correct_buttons(self):
        for kind, params, button, count in [
            ("click", dict(x=2, y=3, button="right", count=2), 3, 2),
            *[
                ("scroll", dict(x=2, y=3, direction=d, pages=0.25), b, 2)
                for d, b in [("up", 4), ("down", 5), ("left", 6), ("right", 7)]
            ],
        ]:
            with self.subTest(kind=kind, params=params):
                facts = self.setup_pointer(kind, params)
                result = self.send(self.command(facts))
                self.assertEqual(result["dispatch"], "dispatched")
                self.assertEqual([e[:2] for e in self.b.desktop.events[1:]], [(4, button), (5, button)] * count)
                self.assertEqual(self.b.desktop.buttons, set())
                self.driver.finish(self.operation)
                self.b.desktop.events.clear()

    def test_drag_keeps_implicit_grab_when_window_geometry_or_hit_changes_after_press(self):
        facts = self.setup_pointer("drag", dict(fromX=10, fromY=20, toX=100, toY=80))

        def move_window(kind, *_):
            if kind == 4:
                self.b.desktop.covered = True
                self.b.desktop.items[0].rect = (30, 40, 500, 350)

        self.b.desktop.event_hook = move_window
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["cleanup"]), ("dispatched", "complete"))
        self.assertEqual(self.b.desktop.events[-2:], [(6, 0, 250, 200), (5, 1, 0, 0)])
        self.assertEqual([e[0] for e in self.b.desktop.events].count(4), 1)
        self.assertFalse(self.b.desktop.buttons)

    def test_drag_identity_loss_or_lock_releases_without_remaining_motion(self):
        for change in ("identity", "lock"):
            with self.subTest(change=change):
                facts = self.setup_pointer("drag", dict(fromX=1, fromY=2, toX=10, toY=20))
                original = replace(self.b.desktop.items[0])

                def lose(kind, *_, change=change):
                    if kind != 4:
                        return
                    if change == "identity":
                        self.b.desktop.items[0] = replace(self.b.desktop.items[0], process_start="new")
                    else:
                        self.b.locked = True

                self.b.desktop.event_hook = lose
                result = self.send(self.command(facts))
                self.assertEqual((result["dispatch"], result["cleanup"]), ("unknown", "complete"))
                self.assertEqual([e[0] for e in self.b.desktop.events], [6, 4, 5])
                self.driver.finish(self.operation)
                self.b.desktop.items = [original]
                self.b.registry.instances[self.target["instance"]].alive = True
                self.b.locked = False
                self.b.desktop.events.clear()
                self.b.desktop.event_hook = None

    def test_cancel_after_motion_or_press_stops_and_only_releases_owned_button(self):
        for cancel_at, expected in [(6, [6]), (4, [6, 4, 5])]:
            with self.subTest(cancel_at=cancel_at):
                facts = self.setup_pointer("drag", dict(fromX=1, fromY=2, toX=10, toY=20))
                ctx = base.context()
                self.b.desktop.event_hook = lambda kind, *_, cancel_at=cancel_at, ctx=ctx: (
                    ctx.cancelled.set() if kind == cancel_at else None
                )
                result = self.send(self.command(facts), ctx=ctx)
                self.assertEqual(
                    (result["dispatch"], result["errorCode"], result["cleanup"]), ("unknown", "cancelled", "complete")
                )
                self.assertEqual([e[0] for e in self.b.desktop.events], expected)
                self.assertTrue(self.driver.current.dispatched)
                with self.assertRaises(Failure):
                    self.send(self.command(facts), 2)
                self.driver.finish(self.operation)
                self.b.desktop.events.clear()
                self.b.desktop.event_hook = None

    def test_hit_changed_by_first_motion_prevents_any_press_and_is_never_replayed(self):
        facts = self.setup_pointer()
        self.b.desktop.event_hook = lambda kind, *_: setattr(self.b.desktop, "covered", True) if kind == 6 else None
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["cleanup"]), ("unknown", "complete"))
        self.assertEqual(self.b.desktop.events, [(6, 0, 25, 50)])
        self.assertTrue(self.driver.current.dispatched, "Motion is already an observable requested effect")
        with self.assertRaises(Failure):
            self.send(self.command(facts), 2)
        self.assertEqual(self.b.desktop.activations, [])

    def test_lost_press_reply_is_unknown_and_release_failure_retires_operation(self):
        for failure_at in (4, 5):
            with self.subTest(failure_at=failure_at):
                facts = self.setup_pointer()

                def fail(kind, *_, failure_at=failure_at):
                    if kind == failure_at:
                        raise NativeError("X reply unavailable")

                self.b.desktop.event_hook = fail
                result = self.send(self.command(facts))
                self.assertEqual(result["dispatch"], "unknown")
                self.assertEqual(result["cleanup"], "failed" if failure_at == 5 else "complete")
                self.assertEqual(self.b.desktop.buttons, {1} if failure_at == 5 else set())
                self.assertEqual(self.driver.finish(self.operation)["cleanup"], result["cleanup"])
                self.b.desktop.event_hook = None
                self.b.desktop.release()
                self.b.desktop.events.clear()

    def test_closed_window_does_not_prevent_button_release(self):
        facts = self.setup_pointer()
        self.b.desktop.event_hook = lambda kind, *_: self.b.desktop.items.clear() if kind == 4 else None
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["cleanup"]), ("dispatched", "complete"))
        self.assertEqual([e[0] for e in self.b.desktop.events], [6, 4, 5])

    def test_duplicate_step_after_finish_returns_same_receipt_without_replay(self):
        facts = self.setup_pointer()
        command = self.command(facts)
        result = self.send(command)
        self.driver.finish(self.operation)
        self.assertEqual(self.send(command), result)
        self.assertEqual([e[0] for e in self.b.desktop.events], [6, 4, 5])
        with self.assertRaises(Failure):
            self.send(dict(command, foreground=True))

    def test_pointer_dispatch_cannot_smuggle_activation_target_or_contract(self):
        facts = self.setup_pointer()
        for step, changes in enumerate(
            (dict(foreground=True), dict(primitiveId="missing"), dict(target={**self.selected, "capture": "another"})),
            1,
        ):
            with self.assertRaises(Failure):
                self.send({**self.command(facts), **changes}, step)
        self.assertEqual(self.b.desktop.events, [])

    def test_desktop_requires_exclusive_and_original_canvas(self):
        selected = self.image(desktop=True)
        with self.assertRaises(Failure) as error:
            self.setup_pointer(target=selected)
        self.assertEqual(error.exception.code, "mode_unsupported")
        self.b.mode = self.b.desktop.mode = "exclusive"
        facts = self.setup_pointer(target=selected)
        self.b.desktop.screen_size = (1200, 800)
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["errorCode"]), ("notDispatched", "stale_target"))
        self.assertEqual(self.b.desktop.events, [])
        self.driver.finish(self.operation)
        self.b.desktop.screen_size = (1024, 768)
        facts = self.setup_pointer(target=selected)
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.assertEqual(self.b.desktop.events[0], (6, 0, 40, 80))
        self.assertEqual(self.b.desktop.activations, [])

    def test_desktop_pointer_capability_does_not_supply_keyboard_evidence_or_paste(self):
        self.b.mode = self.b.desktop.mode = "exclusive"
        selected = self.image(desktop=True)
        with self.assertRaises(Failure) as error:
            self.setup_pointer("paste", {"text": "hello"}, target=selected)
        self.assertEqual(error.exception.code, "mode_unsupported")
        self.assertEqual(self.b.leases, 0)
        with self.assertRaises(Failure) as error:
            self.setup_pointer("insert", {"text": "hello"}, target=selected)
        self.assertEqual(error.exception.code, "stale_target")
        self.driver.finish(self.operation)
        self.assertEqual(self.b.leases, 0)
        self.assertEqual(self.b.desktop.events, [])


class LinuxNativePointerSDKTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.connections, self.diagnostics = [], []
        self.temp = TemporaryDirectory()
        self.mode = patch.dict(os.environ, {"COMPUTER_USE_DESKTOP": "shared"})
        self.mode.start()
        setDiagnosticHandler(self.diagnostics.append)

    async def asyncTearDown(self):
        setDiagnosticHandler(None)
        for connection in self.connections:
            connection.close()
        for connection in self.connections:
            await connection.waitClosed()
        self.mode.stop()
        self.temp.cleanup()

    async def connect(self, *, mode="shared", assist=False, covered=False, extra=()):
        os.environ["COMPUTER_USE_DESKTOP"] = mode
        self.journal = Path(self.temp.name) / ("events-" + str(len(self.connections)) + ".jsonl")
        args = [
            "COMPUTER_USE_DESKTOP=" + mode,
            "COMPUTER_USE_FOREGROUND_ASSIST=" + ("1" if assist else "0"),
            sys.executable,
            str(base.ROOT / "tests/fixtures/linux_native_provider.py"),
            "--pointer",
            "--pointer-journal",
            str(self.journal),
        ]
        if covered:
            args.append("--covered")
        args.extend(extra)
        connection = Connection(lambda: None, environmentDriver(args, timeoutMs=5000), protocol_version=6)
        self.connections.append(connection)
        computer = nativeComputer(ConnectionPort(connection, HostPolicy(mode, assist)))
        app = computer.app((await computer.listApps())[0].id)
        state = await app.read(view="both")
        self.assertIsNotNone(state.screenshot)
        self.assertNotIn("Full text", state.text)
        return connection, computer, app

    def commands(self):
        return [
            e["params"]["command"]
            for e in self.diagnostics
            if e["kind"] == "command_start" and e["method"] == "execute"
        ]

    def events(self):
        return [json.loads(line) for line in self.journal.read_text().splitlines()] if self.journal.exists() else []

    def no_automatic_read(self):
        events = [e for e in self.diagnostics if e["kind"] == "command_start"]
        last = max(i for i, e in enumerate(events) if e["method"] == "execute")
        self.assertEqual([e["method"] for e in events[last + 1 :]], ["finish"])

    async def test_public_no_ax_click_scroll_drag_use_original_image_without_auto_read(self):
        connection, _, app = await self.connect()
        for action in (
            lambda: app.click(at=(10, 20)),
            lambda: app.scroll("down", at=(10, 20), pages=0.125),
            lambda: app.drag((10, 20), (100, 80)),
        ):
            result = await action()
            self.assertEqual(result.status, "dispatched")
            self.assertFalse(hasattr(result, "text"))
            self.no_automatic_read()
        self.assertEqual([(c["kind"], c["foreground"]) for c in self.commands()], [("pointer_sequence", False)] * 3)
        self.assertEqual(len({c["target"]["capture"] for c in self.commands()}), 1)
        self.assertTrue(all(c["target"]["node"] is None for c in self.commands()))
        self.assertTrue(all(e["active"] == 22 for e in self.events()))
        self.assertIn("nativePointer", connection.capabilities)

    async def test_public_covered_shared_refuses_without_input_and_keeps_session_usable(self):
        _, _, app = await self.connect(covered=True)
        with self.assertRaises(ComputerUseError) as error:
            await app.click(at=(10, 20))
        self.assertEqual(error.exception.effect, "none")
        self.assertEqual(self.events(), [])
        self.assertEqual(self.commands(), [])
        (self.assertIsNotNone((await app.read(view="both")).screenshot))

    async def test_public_authorized_covered_point_prepares_then_sends_one_gesture(self):
        _, _, app = await self.connect(assist=True, covered=True)
        (self.assertEqual((await app.click(at=(10, 20))).status, "dispatched"))
        self.assertEqual([(c["kind"], c["foreground"]) for c in self.commands()], [("pointer_sequence", True)])
        self.assertEqual([e["kind"] for e in self.events()], [6, 4, 5])
        self.assertTrue(all(e["active"] == 11 for e in self.events()))
        self.no_automatic_read()

    async def test_public_failed_button_cleanup_retires_sdk_without_replay(self):
        _, _, app = await self.connect(extra=("--pointer-release-failed",))
        with self.assertRaises(ComputerUseError) as error:
            await app.click(at=(10, 20))
        self.assertEqual(error.exception.effect, "possible")
        self.assertIn("cleanup", error.exception.detail)
        before = len(self.commands())
        with self.assertRaisesRegex(ComputerUseError, "retired"):
            await app.click(at=(10, 20))
        self.assertEqual(len(self.commands()), before)
        self.assertEqual([e["kind"] for e in self.events()].count(4), 1)

    async def test_public_desktop_shared_refuses_even_when_assistance_is_authorized(self):
        _, computer, _ = await self.connect(assist=True)
        await computer.read(view="both")
        with self.assertRaises(ComputerUseError) as error:
            await computer.click(at=(10, 20))
        self.assertEqual((error.exception.code, error.exception.effect), ("mode_unsupported", "none"))
        self.assertEqual(self.commands(), [])
        self.assertEqual(self.events(), [])

    async def test_public_desktop_exclusive_pointer_and_unobserved_keyboard_refusal(self):
        connection, computer, _ = await self.connect(mode="exclusive")
        state = await computer.read(view="both")
        self.assertIsNotNone(state.screenshot)
        (self.assertEqual((await computer.click(at=(10, 20))).status, "dispatched"))
        self.assertIsNone(self.commands()[0]["target"]["surface"])
        self.assertIn("nativeDesktopInput", connection.capabilities)
        before = self.events()
        for action in (lambda: computer.pressKey("Return"), lambda: computer.typeText("hello")):
            await computer.read(view="both")
            with self.assertRaises(ComputerUseError) as error:
                await action()
            self.assertEqual(error.exception.code, "stale_target")
        self.assertEqual(self.events(), before)


if __name__ == "__main__":
    unittest.main()
