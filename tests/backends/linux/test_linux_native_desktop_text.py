"""Captured desktop literal text, through production v5 and XKB sequences.

Only OS pixels, window identities, keymap and native recipient effects are
substituted. These tests do not claim application or real XKB compatibility.
"""

import json
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fixture_driver import environmentDriver

import backends.linux.test_linux_native as base
import backends.linux.test_linux_native_desktop_key as key_tests
from computer_use.client.factory import nativeComputer
from computer_use.diagnostics import setDiagnosticHandler
from computer_use.errors import ComputerUseError
from computer_use.native.connection import Connection
from computer_use.native.contracts import HostPolicy
from computer_use.native.transport import ConnectionPort
from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.objects.registry import Window


class DesktopTextTests(unittest.TestCase):
    setUp, tearDown = key_tests.DesktopKeyTests.setUp, key_tests.DesktopKeyTests.tearDown
    begin, read = key_tests.DesktopKeyTests.begin, key_tests.DesktopKeyTests.read
    image, setup_pointer = key_tests.DesktopKeyTests.image, key_tests.DesktopKeyTests.setup_pointer
    inspect_input, command, send = (
        key_tests.DesktopKeyTests.inspect_input,
        key_tests.DesktopKeyTests.command,
        key_tests.DesktopKeyTests.send,
    )

    def setup_text(self, text="aA!", target=None):
        return self.setup_pointer("insert", dict(text=text), target=target or self.image(desktop=True))

    def event_pairs(self):
        return [(e[0], e[1]) for e in self.b.desktop.events]

    def keyboard_invalidated(self):
        self.assertIsNone(self.driver.captures.entries[self.selected["capture"]].keyboard)

    def test_literal_text_uses_observed_keyboard_window_without_any_ax(self):
        with patch.object(self.b.ax, "begin", side_effect=AssertionError("No AX")):
            with patch.object(self.b.ax, "call", side_effect=AssertionError("No AX")):
                facts = self.setup_text()
                result = self.send(self.command(facts))
        self.assertEqual(
            (result["dispatch"], result["confirmation"], result["cleanup"]), ("dispatched", "unavailable", "complete")
        )
        self.assertEqual(result["preparation"], [])
        self.assertEqual(self.b.desktop.texts, {11: "first:", 22: "second:aA!"})
        self.assertEqual(self.b.desktop.activations, [])
        self.assertFalse(self.b.desktop.keys)

    def test_click_then_type_reuses_capture_and_actual_hit_keyboard_destination(self):
        target = self.image(desktop=True)
        facts = self.setup_pointer(target=target)
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.driver.finish(self.operation)
        facts = self.setup_text("aB", target)
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.assertEqual(self.b.desktop.texts, {11: "first:aB", 22: "second:"})
        self.assertEqual(self.b.desktop.activations, [])

    def test_full_payload_mapping_refuses_without_sending_ascii_prefix(self):
        with self.assertRaises(Failure) as error:
            self.setup_text("abc😀")
        self.assertEqual(error.exception.code, "input_unavailable")
        self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(self.b.desktop.activations, [])

    def test_mapping_is_rechecked_after_inspect_before_first_event(self):
        facts = self.setup_text("ab")
        self.b.desktop.keymap_missing.add("b")
        result = self.send(self.command(facts))
        self.assertEqual(result["dispatch"], "notDispatched")
        self.assertEqual(self.b.desktop.events, [])
        self.keyboard_invalidated()

    def test_whole_preflight_callback_cannot_redirect_target(self):
        facts = self.setup_text("ab")
        snapshot = self.b.desktop.text_keyboard.snapshot

        def changed():
            result = snapshot()
            self.b.desktop.foreground = self.b.desktop.keyboard_window = 11
            return result

        self.b.desktop.text_keyboard.snapshot = changed
        result = self.send(self.command(facts))
        self.assertEqual(result["dispatch"], "notDispatched")
        self.assertEqual(self.b.desktop.events, [])
        self.keyboard_invalidated()

    def test_shared_replacement_and_unexposed_paste_are_rejected(self):
        target = self.image(desktop=True)
        for kind in ("replace", "paste"):
            with self.subTest(kind=kind), self.assertRaises(Failure):
                self.setup_pointer(kind, dict(text="abc"), target=target)
        self.b.mode = "shared"
        with self.assertRaises(Failure) as error:
            self.setup_text(target=target)
        self.assertEqual(error.exception.code, "mode_unsupported")
        self.assertEqual(self.b.desktop.events, [])

    def test_plain_window_capture_is_not_a_desktop_text_destination(self):
        # Build a well-formed captured-window binding; desktop text must not be
        # accepted merely because a native keyboard window can be identified.
        self.setup_text()
        self.driver.finish(self.operation)
        target = {**self.selected, "instance": "instance", "rootWindow": "window", "surface": "surface"}
        with self.assertRaises(Failure) as error:
            self.setup_text(target=target)
        self.assertEqual(error.exception.code, "mode_unsupported")
        self.assertEqual(self.b.desktop.events, [])

    def test_multiline_is_not_converted_to_keyboard_submit(self):
        target = self.image(desktop=True)
        for text in ("abc\n", "abc\t", "abc\0", "abc\u2028"):
            with self.subTest(text=repr(text)), self.assertRaises(Failure):
                self.setup_text(text, target)
        self.assertEqual(self.b.desktop.events, [])

    def test_empty_native_insert_confirms_without_events_or_keyboard_handoff(self):
        facts = self.setup_text("")
        with patch.object(self.driver.captures, "input_finished", side_effect=AssertionError("No input handoff")):
            result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["confirmation"]), ("notDispatched", "confirmed"))
        self.assertEqual(self.b.desktop.events, [])

    def test_mode_lock_held_window_and_process_changes_refuse_before_text(self):
        for cause in ("shared", "no-input", "lock", "held", "focus", "generation", "closed"):
            with self.subTest(cause=cause):
                self.tearDown()
                self.setUp()
                facts = self.setup_text()
                if cause == "shared":
                    self.b.mode = "shared"
                elif cause == "no-input":
                    self.b.desktop.can_input = False
                elif cause == "lock":
                    self.b.locked = True
                elif cause == "held":
                    self.b.desktop.user_held = True
                elif cause == "focus":
                    self.b.desktop.keyboard_window = 11
                elif cause == "generation":
                    self.b.desktop.items[-1].process_start = "replaced"
                else:
                    self.b.registry.retire(22)
                result = self.send(self.command(facts))
                self.assertEqual(result["dispatch"], "notDispatched")
                self.assertEqual(self.b.desktop.events, [])
                self.keyboard_invalidated()

    def test_shift_callback_focus_loss_releases_without_character_or_activation(self):
        facts = self.setup_text("ABC")

        def move(kind, detail, *_):
            if (kind, detail) == (2, "shift"):
                self.b.desktop.foreground = self.b.desktop.keyboard_window = 11

        self.b.desktop.event_hook = move
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["cleanup"]), ("unknown", "complete"))
        self.assertEqual(self.event_pairs(), [(2, "shift"), (3, "shift")])
        self.assertEqual(self.b.desktop.texts, {11: "first:", 22: "second:"})
        self.assertEqual(self.b.desktop.activations, [])
        self.keyboard_invalidated()

    def test_first_character_focus_loss_stops_remaining_text_and_cannot_replay(self):
        facts = self.setup_text("abc")

        def move(kind, detail, *_):
            if (kind, detail) == (2, "a"):
                self.b.desktop.foreground = self.b.desktop.keyboard_window = 11

        self.b.desktop.event_hook = move
        command = self.command(facts)
        result = self.send(command)
        self.assertEqual((result["dispatch"], result["cleanup"]), ("unknown", "complete"))
        self.assertEqual(self.b.desktop.texts, {11: "first:", 22: "second:a"})
        self.assertEqual(self.event_pairs(), [(2, "a"), (3, "a")])
        self.assertEqual(self.send(command), result)
        with self.assertRaises(Failure):
            self.send(command, step=2)
        self.driver.finish(self.operation)
        self.assertEqual(self.send(command), result)
        self.assertEqual(self.event_pairs(), [(2, "a"), (3, "a")])
        self.keyboard_invalidated()

    def test_map_change_after_modifier_sends_no_character(self):
        facts = self.setup_text("ABC")

        def remap(kind, detail, *_):
            if (kind, detail) == (2, "shift"):
                self.b.desktop.text_keyboard.generation += 1

        self.b.desktop.event_hook = remap
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["cleanup"]), ("unknown", "complete"))
        self.assertEqual(self.event_pairs(), [(2, "shift"), (3, "shift")])
        self.keyboard_invalidated()

    def test_cancel_and_deadline_after_shift_release_independently(self):
        for cause in ("cancel", "deadline"):
            with self.subTest(cause=cause):
                self.tearDown()
                self.setUp()
                facts = self.setup_text("ABC")
                ctx = base.context()

                def stop(kind, detail, *_, cause=cause, ctx=ctx):
                    if (kind, detail) == (2, "shift"):
                        if cause == "cancel":
                            ctx.cancelled.set()
                        else:
                            ctx.deadline = time.monotonic() - 1

                self.b.desktop.event_hook = stop
                result = self.send(self.command(facts), ctx=ctx)
                self.assertEqual((result["dispatch"], result["cleanup"]), ("unknown", "complete"))
                self.assertEqual(result["errorCode"], "cancelled" if cause == "cancel" else "deadline_exceeded")
                self.assertEqual(self.event_pairs(), [(2, "shift"), (3, "shift")])
                self.assertFalse(self.b.desktop.keys)
                self.keyboard_invalidated()

    def test_failed_release_invalidates_capture_and_preserves_failed_cleanup(self):
        facts = self.setup_text("a")

        def fail(kind, *_):
            if kind == 3:
                raise RuntimeError("Release not confirmed")

        self.b.desktop.event_hook = fail
        result = self.send(self.command(facts))
        self.assertEqual(
            (result["dispatch"], result["cleanup"], result["errorCode"]), ("unknown", "failed", "execution_unknown")
        )
        self.assertTrue(self.b.desktop.keys)
        self.assertEqual(self.driver.finish(self.operation)["cleanup"], "failed")
        self.keyboard_invalidated()
        self.b.desktop.event_hook = None
        self.b.desktop.release()

    def test_foreground_preparation_and_changed_capture_cannot_be_smuggled(self):
        facts = self.setup_text()
        for number, changes in enumerate(
            (
                dict(foreground=True),
                dict(kind="prepare_receiver"),
                dict(target={**self.selected, "capture": "different"}),
            ),
            1,
        ):
            with self.subTest(changes=changes), self.assertRaises(Failure):
                self.send({**self.command(facts), **changes}, step=number)
        self.assertEqual(self.b.desktop.events, [])

    def test_last_character_opening_new_window_cannot_authorize_unobserved_receiver(self):
        facts = self.setup_text("a")

        def opened(kind, detail, *_):
            if (kind, detail) == (2, "a"):
                self.b.desktop.items.append(
                    Window(33, "New", "Fixture", 42, (0, 0, 200, 200), process_start="generation")
                )
                self.b.desktop.foreground = self.b.desktop.keyboard_window = 33

        self.b.desktop.event_hook = opened
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.keyboard_invalidated()
        self.driver.finish(self.operation)
        with self.assertRaises(Failure):
            self.setup_text("b", self.selected)
        self.assertEqual(self.event_pairs(), [(2, "a"), (3, "a")])


class DesktopTextSDKTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_read_click_type_key_keep_capture_without_automatic_observation(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {"COMPUTER_USE_DESKTOP": "exclusive"}):
            journal = Path(tmp) / "text.jsonl"
            diagnostics = []
            setDiagnosticHandler(diagnostics.append)
            connection = Connection(
                lambda: None,
                environmentDriver(
                    [
                        "COMPUTER_USE_DESKTOP=exclusive",
                        sys.executable,
                        str(base.ROOT / "tests/fixtures/linux_desktop_key_provider.py"),
                        "--desktop-text-journal",
                        str(journal),
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
                    lambda: computer.typeText("aA!?"),
                    lambda: computer.typeText("b"),
                    lambda: computer.pressKey("Return"),
                ):
                    self.assertEqual((await action()).status, "dispatched")
                commands = [
                    e["params"]["command"]
                    for e in diagnostics
                    if e["kind"] == "command_start" and e["method"] == "execute"
                ]
                self.assertEqual(
                    [c["kind"] for c in commands], ["pointer_sequence", "key_sequence", "key_sequence", "key_sequence"]
                )
                self.assertEqual(len({c["target"]["capture"] for c in commands}), 1)
                self.assertFalse(
                    any(
                        e.get("method") == "capture"
                        or e.get("params", {}).get("query", {}).get("kind") in ("nodes", "surfaces", "applications")
                        for e in diagnostics
                    )
                )
                rows = [json.loads(line) for line in journal.read_text().splitlines()]
                self.assertEqual(rows[-1]["texts"], {"11": "first:aA!?b", "22": "second:"})
            finally:
                connection.close()
                await connection.waitClosed()
                setDiagnosticHandler(None)

    async def test_public_failed_key_cleanup_retires_desktop_session(self):
        with patch.dict(os.environ, {"COMPUTER_USE_DESKTOP": "exclusive"}):
            diagnostics = []
            setDiagnosticHandler(diagnostics.append)
            connection = Connection(
                lambda: None,
                environmentDriver(
                    [
                        "COMPUTER_USE_DESKTOP=exclusive",
                        sys.executable,
                        str(base.ROOT / "tests/fixtures/linux_desktop_key_provider.py"),
                        "--desktop-key-release-failed",
                    ],
                    timeoutMs=5000,
                ),
                protocol_version=6,
            )
            try:
                computer = nativeComputer(ConnectionPort(connection, HostPolicy("exclusive", False)))
                await computer.read(view="both")
                diagnostics.clear()
                with self.assertRaises(ComputerUseError) as error:
                    await computer.typeText("a")
                self.assertEqual(error.exception.effect, "possible")
                self.assertIn("cleanup", error.exception.detail)
                before = len([e for e in diagnostics if e["kind"] == "command_start" and e["method"] == "execute"])
                # The public desktop clears its capture before consulting the
                # retired Engine. A new read must also refuse the old session.
                with self.assertRaisesRegex(ComputerUseError, "observation is missing"):
                    await computer.typeText("b")
                with self.assertRaisesRegex(RuntimeError, "retired"):
                    await computer.read(view="both")
                after = len([e for e in diagnostics if e["kind"] == "command_start" and e["method"] == "execute"])
                self.assertEqual((before, after), (1, 1))
            finally:
                connection.close()
                await connection.waitClosed()
                setDiagnosticHandler(None)


if __name__ == "__main__":
    unittest.main()
