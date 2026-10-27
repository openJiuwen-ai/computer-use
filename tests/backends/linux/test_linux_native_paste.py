"""Bound Linux paste through real native units, RPC and the public Python API.

Only native clipboard ownership/consumption and OS effects are substituted.
XTEST sequencing, receiver guards, policy, dispatch and cleanup are production.
"""

import sys
import unittest
from unittest.mock import patch

from fixture_driver import environmentDriver
from linux_native_provider import NativeError, PasteBackend
from repository import ROOT as REPO_ROOT

import backends.linux.test_linux_native as read_tests
import backends.linux.test_linux_native_keyboard as key_tests
from backends.linux.test_linux_native import context
from computer_use.client.factory import nativeComputer
from computer_use.errors import ComputerUseError
from computer_use.native.connection import Connection
from computer_use.native.contracts import HostPolicy
from computer_use.native.transport import ConnectionPort
from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.runtime.native import NativeExecution

ROOT = REPO_ROOT
TEXT = "第一行 😀\nsecond\tcolumn\rthird"


class LinuxNativePasteTests(unittest.TestCase):
    tearDown = read_tests.LinuxNativeFactsTests.tearDown
    begin = read_tests.LinuxNativeFactsTests.begin
    inspect = read_tests.LinuxNativeFactsTests.inspect
    surfaces = read_tests.LinuxNativeFactsTests.surfaces
    target = read_tests.LinuxNativeFactsTests.target
    collect = read_tests.LinuxNativeFactsTests.collect
    ready = key_tests.LinuxNativeKeyboardTests.ready
    inspect_input = key_tests.LinuxNativeKeyboardTests.inspect_input
    command = key_tests.LinuxNativeKeyboardTests.command
    send = key_tests.LinuxNativeKeyboardTests.send
    focus_calls = key_tests.LinuxNativeKeyboardTests.focus_calls

    def setUp(self):
        self.backend = PasteBackend()
        self.backend.desktop.assist = False
        self.driver = NativeExecution(self.backend, "session")
        self.pid = patch("computer_use_linux.windowing.native_directory.process_start", return_value="generation")
        self.pid.start()

    def paste_setup(self, text=TEXT, *, window_scope=False):
        target = self.target()
        batch = self.collect(target)
        ref = next(n["node"] for n in batch["nodes"] if n["values"].get("name") == "Input")
        self.selected = dict(target, node=None if window_scope else ref)
        self.operation = self.driver.perform(
            "begin",
            dict(mode="write", target=self.selected, intent=dict(kind="paste", parameters=dict(text=text))),
            context(),
        )["operation"]
        return self.inspect_input()

    def test_unicode_multiline_paste_preserves_intent_and_replaces_native_selection(self):
        self.ready()
        facts = self.paste_setup()
        self.assertEqual(facts["primitives"][0]["unit"], "clipboard_paste")
        result = self.send(self.command(facts))
        self.assertEqual(
            (result["dispatch"], result["confirmation"], result["cleanup"]), ("dispatched", "unavailable", "complete")
        )
        self.assertEqual(result["preparation"], ["clipboard"])
        self.assertEqual(self.backend.editor.text, "a" + TEXT + "bc")
        self.assertEqual(self.backend.other_editor.text, "untouched")
        self.assertEqual(self.backend.clipboard.calls, [TEXT])
        self.assertEqual(self.backend.paste_counter.text, "1")
        self.assertEqual(self.backend.counter.text, "0", "Newline characters must not be Return key events")
        self.assertEqual(self.backend.desktop.events, [(2, "ctrl"), (2, "v"), (3, "v"), (3, "ctrl")])
        self.assertFalse(
            any(name in ("insert_text", "set_text_contents", "delete_text") for name, *_ in self.backend.ax.calls)
        )

    def test_preparation_does_not_publish_clipboard_or_send_paste(self):
        self.backend.desktop.assist = True
        facts = self.paste_setup()
        failed = self.send(self.command(facts, prepare=True))
        self.assertEqual((failed["dispatch"], failed["retryReason"]), ("notDispatched", "preparationUnavailable"))
        self.assertEqual(self.backend.clipboard_requests, 0)
        self.assertEqual(self.backend.desktop.events, [])
        fresh = self.inspect_input()
        prepared = self.send(self.command(fresh, prepare=True, foreground=True), step=2)
        self.assertEqual(prepared["confirmation"], "confirmed")
        self.assertEqual(self.backend.clipboard_requests, 0)
        result = self.send(self.command(self.inspect_input()), step=3)
        self.assertEqual(result["dispatch"], "dispatched")
        self.assertEqual(self.backend.clipboard.calls, [TEXT])
        self.assertEqual(self.backend.desktop.activations, [11])

    def test_readonly_target_does_not_offer_or_dispatch_paste(self):
        self.ready()
        self.backend.editor.states.discard("EDITABLE")
        facts = self.paste_setup()
        self.assertEqual(facts["primitives"][0]["supported"], "no")
        with self.assertRaises(Failure):
            self.send(self.command(facts))
        self.assertEqual(self.backend.clipboard_requests, 0)
        self.assertEqual(self.backend.desktop.events, [])

    def test_editable_state_lost_after_observation_refuses_before_clipboard(self):
        self.ready()
        facts = self.paste_setup()
        self.backend.editor.states.discard("EDITABLE")
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["errorCode"]), ("notDispatched", "input_unavailable"))
        self.assertEqual(self.backend.clipboard_requests, 0)
        self.assertEqual(self.backend.editor.text, "a😀bc")

    def test_clipboard_publication_callback_cannot_redirect_first_key(self):
        for change in ("readonly", "focus", "parent", "ownership"):
            with self.subTest(change=change):
                self.ready()
                facts = self.paste_setup()

                def mutate(*, change=change):
                    if change == "readonly":
                        self.backend.editor.states.discard("EDITABLE")
                    elif change == "focus":
                        self.backend.editor.states.discard("FOCUSED")
                    elif change == "parent":
                        self.backend.editor.parent = self.backend.other_window
                    else:
                        self.backend.clipboard.owned = False

                self.backend.clipboard.before_send = mutate
                result = self.send(self.command(facts))
                self.assertEqual((result["dispatch"], result["cleanup"]), ("notDispatched", "complete"))
                self.assertEqual(result["preparation"], ["clipboard"])
                self.assertEqual(self.backend.desktop.events, [])
                self.assertEqual(self.backend.paste_counter.text, "0")
                self.assertFalse(self.driver.current.dispatched)
                self.driver.finish(self.operation)
                self.backend.editor.states.add("EDITABLE")
                self.backend.editor.parent = self.backend.window
                self.backend.clipboard.before_send = None

    def test_focus_or_clipboard_changes_after_modifier_release_without_paste_or_replay(self):
        original = self.backend.desktop.event_hook
        for change in ("focus", "clipboard"):
            with self.subTest(change=change):
                self.ready()
                self.backend.desktop.events.clear()
                facts = self.paste_setup()

                def event(kind, detail, *, change=change):
                    original(kind, detail)
                    if (kind, detail) == (2, "ctrl"):
                        if change == "focus":
                            self.backend.desktop.keyboard_window = 22
                        else:
                            self.backend.clipboard.owned = False

                self.backend.desktop.event_hook = event
                result = self.send(self.command(facts))
                self.assertEqual((result["dispatch"], result["cleanup"]), ("unknown", "complete"))
                self.assertIsNone(result["retryReason"])
                self.assertEqual(self.backend.desktop.events, [(2, "ctrl"), (3, "ctrl")])
                self.assertEqual(self.backend.paste_counter.text, "0")
                with self.assertRaises(Failure):
                    self.send(self.command(facts, prepare=True, foreground=True), step=2)
                self.driver.finish(self.operation)
        self.backend.desktop.event_hook = original

    def test_consumer_error_after_paste_never_replays_or_falls_back_to_text(self):
        self.ready()
        facts = self.paste_setup()
        self.backend.clipboard.error = Failure("Transfer receipt lost", "execution_unknown")
        command = self.command(facts)
        result = self.send(command)
        self.assertEqual((result["dispatch"], result["errorCode"]), ("dispatched", "execution_unknown"))
        self.assertEqual(self.backend.paste_counter.text, "1")
        self.assertIsNone(result["retryReason"])
        self.driver.finish(self.operation)
        self.assertEqual(self.send(command), result)
        self.assertEqual(self.backend.clipboard.calls, [TEXT])
        self.assertEqual(self.backend.paste_counter.text, "1")

    def test_clipboard_cleanup_failure_survives_operation_finish(self):
        self.ready()
        facts = self.paste_setup()
        self.backend.clipboard.cleanup = "failed"
        result = self.send(self.command(facts))
        self.assertEqual(
            (result["dispatch"], result["cleanup"], result["errorCode"]), ("dispatched", "failed", "execution_unknown")
        )
        self.assertIsNone(result["retryReason"])
        self.assertEqual(self.driver.finish(self.operation), dict(cleanup="failed"))
        self.assertEqual(self.backend.paste_counter.text, "1")

    def test_key_release_failure_is_not_hidden_by_successful_clipboard_cleanup(self):
        self.ready()
        facts = self.paste_setup()
        original = self.backend.desktop.event_hook

        def fail(kind, detail):
            original(kind, detail)
            if kind == 3:
                raise NativeError("Release reply lost")

        self.backend.desktop.event_hook = fail
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["cleanup"]), ("unknown", "failed"))
        self.assertEqual(self.backend.paste_counter.text, "1")
        self.assertEqual(self.backend.desktop.keys, {"ctrl", "v"})
        self.assertEqual(self.driver.finish(self.operation), dict(cleanup="failed"))
        self.backend.desktop.event_hook = None
        self.backend.desktop.release()

    def test_window_paste_requires_no_accessibility_root(self):
        self.ready()
        facts = self.paste_setup(window_scope=True)
        self.backend.desktop.items[0].accessible = None
        self.backend.ax.windows = lambda *_: []
        result = self.send(self.command(facts))
        self.assertEqual(result["dispatch"], "dispatched")
        self.assertEqual(self.backend.paste_counter.text, "1")
        self.assertEqual(self.focus_calls(), [])

    def test_empty_native_paste_sends_no_input_and_never_publishes_clipboard(self):
        self.ready()
        facts = self.paste_setup(text="")
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["confirmation"]), ("notDispatched", "confirmed"))
        self.assertEqual(result["preparation"], [])
        self.assertEqual(self.backend.clipboard_requests, 0)
        self.assertEqual(self.backend.desktop.events, [])

    def test_paste_text_limit_is_utf8_bytes_and_rejects_before_lease(self):
        target = self.target()
        for text in ("x" * 65537, "😀" * 16385, "has\0nul"):
            with self.subTest(bytes=len(text.encode("utf8"))):
                with self.assertRaises(Failure):
                    self.driver.perform(
                        "begin",
                        dict(mode="write", target=target, intent=dict(kind="paste", parameters=dict(text=text))),
                        context(),
                    )
                self.assertEqual(self.backend.leases, 0)
        for text in ("x" * 65536, "😀" * 16384):
            operation = self.driver.perform(
                "begin",
                dict(mode="write", target=target, intent=dict(kind="paste", parameters=dict(text=text))),
                context(),
            )["operation"]
            self.driver.finish(operation)
        self.assertEqual(self.backend.clipboard_requests, 0)

    def test_unmapped_paste_shortcut_refuses_before_any_native_preparation(self):
        target = self.target()
        self.backend.desktop.keymap_missing.add("v")
        operation = self.driver.perform(
            "begin", dict(mode="write", target=target, intent=dict(kind="paste", parameters=dict(text=TEXT))), context()
        )["operation"]
        with self.assertRaises(Failure) as failed:
            self.driver.perform(
                "inspect",
                dict(operation=operation, target=target, query=dict(kind="inputContext", within=[])),
                context(),
            )
        self.assertEqual(failed.exception.code, "input_unavailable")
        self.assertEqual(self.focus_calls(), [])
        self.assertEqual(self.backend.clipboard_requests, 0)


class LinuxNativePasteSDKTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = key_tests.LinuxNativeKeyboardSDKTests.asyncSetUp
    asyncTearDown = key_tests.LinuxNativeKeyboardSDKTests.asyncTearDown
    commands = key_tests.LinuxNativeKeyboardSDKTests.commands

    async def connect(self, *, assist=False, extra=()):
        args = [
            "COMPUTER_USE_DESKTOP=shared",
            "COMPUTER_USE_FOREGROUND_ASSIST=" + ("1" if assist else "0"),
            sys.executable,
            str(ROOT / "tests/fixtures/linux_native_provider.py"),
            "--paste",
            *extra,
        ]
        connection = Connection(lambda: None, environmentDriver(args, timeoutMs=5000), protocol_version=6)
        self.connections.append(connection)
        computer = nativeComputer(ConnectionPort(connection, HostPolicy("shared", assist)))
        app = computer.app((await computer.listApps())[0].id)
        await app.read(view="both")
        await app.read(view="both")
        return connection, app, (await app.element([{"name": "Input"}]))

    async def test_shared_failure_never_publishes_or_dispatches_paste(self):
        _, app, field = await self.connect()
        with self.assertRaises(ComputerUseError) as error:
            await field.paste(TEXT)
        self.assertEqual((error.exception.code, error.exception.effect), ("input_unavailable", "possible"))
        self.assertEqual([(c["kind"], c["foreground"]) for c in self.commands()], [("prepare_receiver", False)])
        (self.assertEqual((await (await app.element([{"name": "Paste count"}])).readText()).text, "0"))
        self.assertEqual((await field.readText()).text, "a😀bc")

    async def test_public_paste_background_foreground_one_paste_then_app_paste(self):
        _, app, field = await self.connect(assist=True)
        result = await field.paste(TEXT)
        self.assertEqual(result.status, "dispatched")
        self.assertFalse(hasattr(result, "text"), "Input must not append an automatic tree")
        self.assertEqual(
            [(c["kind"], c["foreground"]) for c in self.commands()],
            [("prepare_receiver", False), ("prepare_receiver", True), ("clipboard_paste", False)],
        )
        last_execute = max(
            i for i, e in enumerate(self.diagnostics) if e["kind"] == "command_start" and e["method"] == "execute"
        )
        self.assertFalse(
            any(
                e["kind"] == "command_start" and e["method"] in ("inspect", "capture")
                for e in self.diagnostics[last_execute + 1 :]
            ),
            "No implicit observation after paste",
        )
        self.assertEqual((await field.readText()).text, "a" + TEXT + "bc")
        (self.assertEqual((await (await app.element([{"name": "Paste count"}])).readText()).text, "1"))
        result = await app.paste("尾")
        self.assertEqual(result.status, "dispatched")
        self.assertFalse(hasattr(result, "text"))
        self.assertEqual(self.commands()[-1]["target"]["node"], None)
        self.assertEqual((await field.readText()).text, "a" + TEXT + "bc尾")
        (self.assertEqual((await (await app.element([{"name": "Paste count"}])).readText()).text, "2"))

    async def test_empty_public_paste_needs_no_focus_or_clipboard(self):
        _, app, field = await self.connect()
        result = await field.paste("")
        self.assertEqual(result.status, "verified")
        self.assertFalse(hasattr(result, "text"))
        self.assertEqual(self.commands(), [])
        self.assertEqual((await field.readText()).text, "a😀bc")
        (self.assertEqual((await (await app.element([{"name": "Paste count"}])).readText()).text, "0"))

    async def test_readonly_public_control_refuses_without_preparation(self):
        _, app, field = await self.connect(assist=True, extra=("--readonly",))
        with self.assertRaises(ComputerUseError) as error:
            await field.paste(TEXT)
        self.assertEqual((error.exception.code, error.exception.effect), ("input_unavailable", "none"))
        self.assertEqual(self.commands(), [])
        (self.assertEqual((await (await app.element([{"name": "Paste count"}])).readText()).text, "0"))

    async def test_failed_clipboard_cleanup_retires_sdk_and_blocks_further_input(self):
        _, _, field = await self.connect(assist=True, extra=("--clipboard-cleanup-failed",))
        with self.assertRaises(ComputerUseError) as error:
            await field.paste(TEXT)
        self.assertEqual(error.exception.effect, "possible")
        self.assertIn("cleanup", error.exception.detail)
        before = len(self.commands())
        with self.assertRaisesRegex(ComputerUseError, "retired"):
            await field.paste("must not replay")
        self.assertEqual(len(self.commands()), before)
        self.assertEqual(sum(c["kind"] == "clipboard_paste" for c in self.commands()), 1)

    async def test_oversized_public_paste_never_reaches_prepare(self):
        _, _, field = await self.connect(assist=True)
        with self.assertRaises(ComputerUseError) as error:
            await field.paste("😀" * 16385)
        self.assertEqual(error.exception.effect, "none")
        self.assertEqual(self.commands(), [])


if __name__ == "__main__":
    unittest.main()
