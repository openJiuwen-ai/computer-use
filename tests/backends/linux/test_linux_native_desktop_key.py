"""Captured exclusive desktop keyboard mechanisms, with native OS seams only."""

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fixture_driver import environmentDriver
from linux_desktop_key_provider import Backend

import backends.linux.test_linux_native as base
import backends.linux.test_linux_native_pointer as pointer_tests
from computer_use.client.factory import nativeComputer
from computer_use.diagnostics import setDiagnosticHandler
from computer_use.native.connection import Connection
from computer_use.native.contracts import HostPolicy
from computer_use.native.transport import ConnectionPort
from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.objects.registry import Window
from computer_use_linux.runtime.native import NativeExecution


class DesktopKeyTests(unittest.TestCase):
    begin = pointer_tests.LinuxNativePointerTests.begin
    read = pointer_tests.LinuxNativePointerTests.read
    tearDown = pointer_tests.LinuxNativePointerTests.tearDown
    image = pointer_tests.LinuxNativePointerTests.image
    setup_pointer = pointer_tests.LinuxNativePointerTests.setup_pointer
    inspect_input = pointer_tests.LinuxNativePointerTests.inspect_input
    command = pointer_tests.LinuxNativePointerTests.command
    send = pointer_tests.LinuxNativePointerTests.send

    def setUp(self):
        self.b = Backend()
        self.driver = NativeExecution(self.b, "session")
        self.pid = patch("computer_use_linux.windowing.native_directory.process_start", return_value="generation")
        self.pid.start()

    def setup_key(self, key="Return", target=None):
        return self.setup_pointer("key", dict(key=key), target=target or self.image(desktop=True))

    def test_keyboard_uses_capture_without_ax_or_activation(self):
        with patch.object(self.b.ax, "begin", side_effect=AssertionError("no AX")):
            target = self.image(desktop=True)
            facts = self.setup_key(target=target)
            self.assertEqual(
                (facts["primitives"][0]["unit"], facts["primitives"][0]["readiness"]), ("key_sequence", "yes")
            )
            result = self.send(self.command(facts))
        self.assertEqual(result["dispatch"], "dispatched")
        self.assertEqual(result["target"], target)
        self.assertEqual([(e[0], e[1]) for e in self.b.desktop.events], [(2, "Return"), (3, "Return")])
        self.assertEqual(self.b.desktop.activations, [])
        self.assertEqual(self.b.desktop.foreground, 22)

    def test_unstable_capture_keeps_pixels_but_cannot_send_keys(self):
        self.b.desktop.after_capture = lambda: setattr(self.b.desktop, "foreground", 11)
        target = self.image(desktop=True)
        self.assertIn(target["capture"], self.driver.captures.entries)
        with self.assertRaises(Failure) as error:
            self.setup_key(target=target)
        self.assertEqual(error.exception.code, "stale_target")
        self.assertEqual(self.b.desktop.events, [])

    def test_old_capture_rejects_external_window_focus_generation_and_lifetime_changes(self):
        for cause in ("window", "focus", "process", "closed", "metadata"):
            with self.subTest(cause=cause):
                self.tearDown()
                self.setUp()
                facts = self.setup_key()
                if cause == "window":
                    self.b.desktop.foreground = self.b.desktop.keyboard_window = 11
                elif cause == "focus":
                    self.b.desktop.keyboard_window = 11
                elif cause == "process":
                    self.b.desktop.items[-1].process_start = "new"
                elif cause == "closed":
                    self.b.registry.retire(22)
                else:
                    self.b.desktop.items[-1].pid = 0
                result = self.send(self.command(facts))
                self.assertEqual(result["dispatch"], "notDispatched")
                self.assertEqual(self.b.desktop.events, [])

    def test_click_then_key_can_use_same_capture_without_implicit_read(self):
        target = self.image(desktop=True)
        facts = self.setup_pointer(target=target)
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.driver.finish(self.operation)
        facts = self.setup_key(target=target)
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.assertEqual(self.b.desktop.foreground, 11)
        self.assertEqual(self.b.desktop.activations, [])

    def test_unconfirmed_pointer_handoff_cannot_keep_previous_window_as_keyboard_recipient(self):
        target = self.image(desktop=True)
        self.b.desktop._event = lambda kind, detail=0, x=0, y=0: self.b.desktop.events.append((kind, detail, x, y))
        facts = self.setup_pointer(target=target)
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.driver.finish(self.operation)
        with self.assertRaises(Failure):
            self.setup_key(target=target)
        self.assertFalse(any(e[0] == 2 for e in self.b.desktop.events))

    def test_new_window_after_own_key_needs_new_observation(self):
        facts = self.setup_key()

        def open_window(kind, *args):
            if kind == 2:
                self.b.desktop.items.append(
                    Window(33, "New", "Fixture", 42, (0, 0, 100, 100), process_start="generation")
                )
                self.b.desktop.foreground = self.b.desktop.keyboard_window = 33

        self.b.desktop.event_hook = open_window
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.assertIsNone(self.driver.captures.entries[self.selected["capture"]].keyboard)
        self.assertFalse(self.b.desktop.keys)

    def test_foreground_preparation_contract_or_within_cannot_be_smuggled(self):
        facts = self.setup_key()
        cmd = self.command(facts)
        for step, changed in enumerate(
            (dict(foreground=True), dict(kind="prepare_receiver"), dict(target={**self.selected, "capture": "fake"})), 1
        ):
            with self.subTest(changed=changed), self.assertRaises(Failure) as error:
                self.send({**cmd, **changed}, step=step)
            self.assertEqual(error.exception.code, "stale_target")
        self.assertEqual(self.b.desktop.events, [])
        with self.assertRaises(Failure):
            self.driver.perform(
                "inspect",
                dict(
                    operation=self.operation,
                    target=self.selected,
                    query=dict(kind="inputContext", within=["some-node"]),
                ),
                base.context(),
            )

    def test_partial_chord_focus_change_releases_modifiers_and_never_replays(self):
        facts = self.setup_key("ctrl+Return")

        def move(kind, *args):
            if kind == 2:
                self.b.desktop.foreground = self.b.desktop.keyboard_window = 11

        self.b.desktop.event_hook = move
        command = self.command(facts)
        first = self.send(command)
        self.assertEqual(first["dispatch"], "unknown")
        self.assertEqual(first["cleanup"], "complete")
        self.assertEqual([(e[0], e[1]) for e in self.b.desktop.events], [(2, "ctrl"), (3, "ctrl")])
        self.assertFalse(self.b.desktop.keys)
        self.assertEqual(self.send(command), first)
        with self.assertRaises(Failure):
            self.send(command, step=2)
        self.driver.finish(self.operation)
        self.assertEqual(self.send(command), first)
        self.assertIsNone(self.driver.captures.entries[self.selected["capture"]].keyboard)

    def test_cancellation_after_modifier_releases_without_replaying(self):
        facts = self.setup_key("ctrl+Return")
        ctx = base.context()
        self.b.desktop.event_hook = lambda kind, *args: ctx.stopped.set() if kind == 2 else None
        result = self.send(self.command(facts), ctx=ctx)
        self.assertEqual((result["dispatch"], result["errorCode"]), ("unknown", "cancelled"))
        self.assertFalse(self.b.desktop.keys)
        self.assertFalse(any(e[1] == "Return" for e in self.b.desktop.events))

    def test_no_input_without_mode_capability_unlocked_idle_state_and_whole_chord_mapping(self):
        for cause in ("mode", "capability", "locked", "held", "mapping"):
            with self.subTest(cause=cause):
                self.tearDown()
                self.setUp()
                facts = self.setup_key("ctrl+Return")
                if cause == "mode":
                    self.b.mode = "shared"
                elif cause == "capability":
                    self.b.desktop.can_input = False
                elif cause == "locked":
                    self.b.locked = True
                elif cause == "held":
                    self.b.desktop.user_held = True
                else:
                    self.b.desktop.keymap_missing.add("Return")
                result = self.send(self.command(facts))
                self.assertEqual(result["dispatch"], "notDispatched")
                self.assertEqual(self.b.desktop.events, [])

    def test_failed_release_reports_cleanup_failure_and_invalidates_keyboard(self):
        facts = self.setup_key()

        def fail(kind, *args):
            if kind == 3:
                raise RuntimeError("release failed")

        self.b.desktop.event_hook = fail
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["cleanup"]), ("unknown", "failed"))
        self.assertTrue(self.b.desktop.keys)
        self.assertIsNone(self.driver.captures.entries[self.selected["capture"]].keyboard)
        self.b.desktop.event_hook = None
        self.b.desktop.release()


class DesktopKeySDKTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_read_click_chord_and_key_share_one_binding_and_no_automatic_read(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {"COMPUTER_USE_DESKTOP": "exclusive"}):
            events = Path(tmp) / "events.jsonl"
            diagnostics = []
            setDiagnosticHandler(diagnostics.append)
            connection = Connection(
                lambda: None,
                environmentDriver(
                    [
                        "COMPUTER_USE_DESKTOP=exclusive",
                        sys.executable,
                        str(base.ROOT / "tests/fixtures/linux_desktop_key_provider.py"),
                        "--pointer-journal",
                        str(events),
                    ],
                    timeoutMs=5000,
                ),
                protocol_version=6,
            )
            try:
                computer = nativeComputer(ConnectionPort(connection, HostPolicy("exclusive", False)))
                await computer.read(view="both")
                diagnostics.clear()
                for action in (
                    lambda: computer.click(at=(10, 20)),
                    lambda: computer.pressKey("ctrl+a"),
                    lambda: computer.pressKey("Return"),
                ):
                    self.assertEqual((await action()).status, "dispatched")
                commands = [
                    e["params"]["command"]
                    for e in diagnostics
                    if e["kind"] == "command_start" and e["method"] == "execute"
                ]
                self.assertEqual([c["kind"] for c in commands], ["pointer_sequence", "key_sequence", "key_sequence"])
                self.assertEqual(len({c["target"]["capture"] for c in commands}), 1)
                self.assertFalse(
                    any(
                        e.get("method") == "capture"
                        or e.get("params", {}).get("query", {}).get("kind") in ("nodes", "surfaces", "applications")
                        for e in diagnostics
                    )
                )
                rows = [json.loads(s) for s in events.read_text().splitlines()]
                self.assertEqual([r["active"] for r in rows if r["kind"] == 2], [11, 11, 11])
            finally:
                connection.close()
                await connection.waitClosed()
                setDiagnosticHandler(None)
