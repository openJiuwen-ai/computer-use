"""Native EditableText mechanism checks using the production Linux pipeline."""

import time
import unittest

from linux_native_provider import NativeError, Node

import backends.linux.test_linux_native as read_tests
from backends.linux.test_linux_native import context
from computer_use.protocol.execution import decodeExecutionResult
from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.control.native_text_codec import TextCodec
from computer_use_linux.objects.registry import Window


class LinuxNativeTextTests(unittest.TestCase):
    # Share only the provider/setup helpers; do not rerun inherited read tests.
    setUp = read_tests.LinuxNativeFactsTests.setUp
    tearDown = read_tests.LinuxNativeFactsTests.tearDown
    begin = read_tests.LinuxNativeFactsTests.begin
    inspect = read_tests.LinuxNativeFactsTests.inspect
    surfaces = read_tests.LinuxNativeFactsTests.surfaces
    target = read_tests.LinuxNativeFactsTests.target
    collect = read_tests.LinuxNativeFactsTests.collect

    def text_setup(self, kind="replace", text="hello", window_scope=False):
        target = self.target()
        batch = self.collect(target)
        ref = next(n["node"] for n in batch["nodes"] if n["values"].get("name") == "Input")
        self.target_text = dict(target, node=ref)
        reply = self.driver.perform(
            "begin",
            dict(
                mode="write",
                target=dict(target, node=None) if window_scope else self.target_text,
                intent=dict(kind=kind, parameters=dict(text=text)),
            ),
            context(),
        )
        self.operation = reply["operation"]
        self.assertEqual(self.backend.leases, 1)
        return self.inspect_input()

    def inspect_input(self, target=None, within=()):
        return decodeExecutionResult(
            "inspect",
            self.driver.perform(
                "inspect",
                dict(
                    operation=self.operation,
                    target=target or self.target_text,
                    query=dict(kind="inputContext", within=list(within)),
                ),
                context(),
            ),
        )

    def command(self, facts):
        return dict(
            kind="native_text_edit", primitiveId=facts["primitives"][0]["id"], target=self.target_text, foreground=False
        )

    def send(self, command, step=1):
        return decodeExecutionResult(
            "execute",
            self.driver.perform("execute", dict(operation=self.operation, step=step, command=command), context()),
        )

    def writes(self):
        return [c for c in self.backend.ax.calls if c[0] in ("set_text_contents", "insert_text", "delete_text")]

    def test_replace_unicode_empty_and_no_global_focus_or_value_equality(self):
        facts = self.text_setup(text="全量 😀")
        self.backend.editor.text = "application changed the display value"
        reply = self.send(self.command(facts))
        self.assertEqual(reply["dispatch"], "dispatched")
        self.assertEqual(reply["confirmation"], "unavailable")
        self.assertEqual(self.backend.editor.text, "全量 😀")
        self.assertEqual(len(self.writes()), 1)
        self.assertNotIn("FOCUSED", self.backend.editor.states)
        self.driver.finish(self.operation)
        self.assertEqual(self.backend.leases, 0)
        facts = self.text_setup(text="")
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.assertEqual(self.backend.editor.text, "")

    def test_insert_replaces_selected_range_and_uses_utf8_byte_length(self):
        facts = self.text_setup("insert", "中😀")
        reply = self.send(self.command(facts))
        self.assertEqual(reply["dispatch"], "dispatched")
        self.assertEqual(self.backend.editor.text, "a中😀bc")
        self.assertEqual([w[0] for w in self.writes()], ["delete_text", "insert_text"])

    def test_insert_preserves_selected_offset_when_delete_moves_caret_to_end(self):
        self.backend.editor.text = "left|right"
        self.backend.editor.selections = [(4, 5)]
        original = self.backend.ax.A.EditableText.delete_text

        def delete_and_reset_caret(obj, start, end):
            accepted = original(obj, start, end)
            obj.caret = len(obj.text)
            return accepted

        self.backend.ax.A.EditableText.delete_text = delete_and_reset_caret
        facts = self.text_setup("insert", "Z")
        reply = self.send(self.command(facts))
        self.assertEqual((reply["dispatch"], self.backend.editor.text), ("dispatched", "leftZright"))
        self.assertEqual([c[0] for c in self.writes()], ["delete_text", "insert_text"])
        self.assertEqual(self.writes()[1][2][0], 4)
        self.assertEqual(self.backend.long_text.text, "中😀" * 3000)

    def test_new_selection_after_delete_is_partial_and_cannot_replay(self):
        self.backend.editor.text = "left|right"
        self.backend.editor.selections = [(4, 5)]
        original = self.backend.ax.A.EditableText.delete_text

        def delete_and_select_elsewhere(obj, start, end):
            accepted = original(obj, start, end)
            obj.selections = [(0, 1)]
            return accepted

        self.backend.ax.A.EditableText.delete_text = delete_and_select_elsewhere
        facts = self.text_setup("insert", "Z")
        command = self.command(facts)
        reply = self.send(command)
        self.assertEqual((reply["dispatch"], self.backend.editor.text), ("partial", "leftright"))
        self.assertIsNone(reply["retryReason"])
        self.assertEqual([c[0] for c in self.writes()], ["delete_text"])
        self.assertEqual(self.send(command), reply)
        with self.assertRaises(Failure):
            self.send(command, step=2)
        self.assertEqual(len(self.writes()), 1)

    def test_original_insertion_offset_disappearing_after_delete_stops_partial(self):
        self.backend.editor.text = "left|right"
        self.backend.editor.selections = [(4, 5)]
        original = self.backend.ax.A.EditableText.delete_text

        def delete_and_shorten(obj, start, end):
            accepted = original(obj, start, end)
            obj.text = "x"
            obj.caret = 1
            return accepted

        self.backend.ax.A.EditableText.delete_text = delete_and_shorten
        facts = self.text_setup("insert", "Z")
        reply = self.send(self.command(facts))
        self.assertEqual((reply["dispatch"], self.backend.editor.text), ("partial", "x"))
        self.assertIn("original insertion offset", reply["detail"])
        self.assertEqual([c[0] for c in self.writes()], ["delete_text"])

    def test_provider_units_are_native_arguments_not_a_change_of_intent(self):
        text = "中😀"
        self.assertEqual(TextCodec("gtk", "3.24").insertion_length(text), 7)
        self.assertEqual(TextCodec("Qt", "6.10.2", True).insertion_length(text), 3)
        with self.assertRaises(Failure):
            TextCodec("Qt", "99.0", True).insertion_length(text)
        facts = self.text_setup("insert", text)
        self.backend.app.toolkit = "Qt"
        self.backend.app.toolkit_version = "6.10.2"
        reply = self.send(self.command(facts))
        self.assertEqual(reply["dispatch"], "notDispatched")
        self.assertEqual(self.writes(), [], "A changed provider contract must not cause partial deletion")

    def test_partial_deletion_stops_if_callback_moves_object(self):
        facts = self.text_setup("insert", "new")

        def delete(obj, start, end):
            self.backend.ax.delete(obj, start, end)
            obj.parent = self.backend.app
            return True

        self.backend.ax.A.EditableText.delete_text = delete
        reply = self.send(self.command(facts))
        self.assertEqual(reply["dispatch"], "partial")
        self.assertEqual(reply["errorCode"], "stale_target")
        self.assertEqual(self.backend.editor.text, "abc")
        self.assertIsNone(reply["retryReason"])
        with self.assertRaises(Failure):
            self.send(self.command(facts), step=2)

    def test_unknown_dispatch_never_replayed_even_after_finish(self):
        facts = self.text_setup()
        self.backend.editor.errors["set_text_contents"] = NativeError("provider disconnected after accepting input")
        command = self.command(facts)
        reply = self.send(command)
        self.assertEqual(reply["dispatch"], "unknown")
        self.assertEqual(len(self.writes()), 1)
        self.driver.finish(self.operation)
        self.assertEqual(self.send(command), reply)
        self.assertEqual(len(self.writes()), 1)
        altered = dict(command, foreground=True)
        with self.assertRaises(Failure):
            self.send(altered)

    def test_path_selection_is_fixed_and_guarded_by_native_ancestors(self):
        facts = self.text_setup(window_scope=True)
        other = self.driver.store.keep(self.target_text["surface"], self.backend.button)
        with self.assertRaises(Failure):
            self.inspect_input(dict(self.target_text, node=other))
        self.backend.editor.parent = self.backend.app
        reply = self.send(self.command(facts))
        self.assertEqual(reply["dispatch"], "notDispatched")
        self.assertEqual(reply["errorCode"], "stale_target")
        self.assertEqual(self.writes(), [])

    def test_read_cannot_input_or_rebind_intent(self):
        facts = self.text_setup()
        command = self.command(facts)
        with self.assertRaises(Failure):
            self.send(dict(command, text="smuggled"))
        self.assertEqual(self.writes(), [])
        self.driver.finish(self.operation)
        self.operation = self.begin(self.target_text)
        with self.assertRaises(Failure):
            self.inspect_input()
        with self.assertRaises(Failure):
            self.send(command)

    def test_lock_disabled_readonly_and_modal_stop_before_mutation(self):
        for mode in ("locked", "disabled", "readonly", "modal"):
            with self.subTest(mode=mode):
                facts = self.text_setup()
                if mode == "locked":
                    self.backend.locked = True
                elif mode == "disabled":
                    self.backend.editor.states -= {"ENABLED", "SENSITIVE"}
                elif mode == "readonly":
                    self.backend.editor.states.discard("EDITABLE")
                else:
                    self.backend.desktop.items.append(
                        Window(
                            12,
                            "modal",
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
                reply = self.send(self.command(facts))
                self.assertEqual(reply["dispatch"], "notDispatched")
                self.assertEqual(self.writes(), [])
                self.backend.locked = False
                self.backend.editor.states |= {"ENABLED", "SENSITIVE", "EDITABLE"}
                self.backend.desktop.items = self.backend.desktop.items[:1]
                self.driver.finish(self.operation)

    def test_new_selection_after_delete_is_partial_not_another_replacement(self):
        facts = self.text_setup("insert", "new")

        def delete(obj, start, end):
            self.backend.ax.delete(obj, start, end)
            obj.selections = [(0, 1)]
            return True

        self.backend.ax.A.EditableText.delete_text = delete
        reply = self.send(self.command(facts))
        self.assertEqual(reply["dispatch"], "partial")
        self.assertEqual(self.backend.editor.text, "abc")

    def test_unsupported_paste_invalid_controls_and_unknown_contract_are_not_sent(self):
        target = self.target()
        for kind, text in (("insert", "line\nfeed"), ("replace", "\0")):
            with self.assertRaises(Failure):
                self.driver.perform(
                    "begin",
                    dict(mode="write", target=target, intent=dict(kind=kind, parameters=dict(text=text))),
                    context(),
                )
        self.assertEqual(self.backend.leases, 0)
        operation = self.driver.perform(
            "begin", dict(mode="write", target=target, intent=dict(kind="paste", parameters=dict(text="ok"))), context()
        )["operation"]
        with self.assertRaises(Failure) as unsupported:
            self.driver.perform(
                "inspect",
                dict(operation=operation, target=target, query=dict(kind="inputContext", within=[])),
                context(),
            )
        self.assertEqual(unsupported.exception.code, "mode_unsupported")
        self.driver.finish(operation)
        facts = self.text_setup()
        with self.assertRaises(Failure):
            self.send(dict(self.command(facts), primitiveId="invented"))
        self.assertEqual(self.writes(), [])

    def test_expiration_releases_whole_operation_desktop_lease(self):
        self.text_setup()
        self.driver.current.deadline = time.monotonic() - 1
        self.driver.expire()
        self.assertIsNone(self.driver.current)
        self.assertEqual(self.backend.leases, 0)
        self.assertEqual(self.driver.finish(self.operation), dict(cleanup="complete"))


class LinuxNativeTextSDKTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = read_tests.LinuxNativeSDKTests.asyncSetUp
    asyncTearDown = read_tests.LinuxNativeSDKTests.asyncTearDown

    async def test_public_path_replace_insert_and_explicit_observe(self):
        app = self.computer.app((await self.computer.listApps())[0].id)
        await app.read(view="both")
        field = await app.element([dict(name="Input")])
        result = await field.typeText("替换 😀", replace=True)
        self.assertFalse(hasattr(result, "text"), "Actions do not acquire an automatic tree")
        self.assertEqual((await field.readText()).text, "替换 😀")
        await field.typeText(" plus")
        self.assertEqual((await field.readText()).text, "替换 😀 plus")


if __name__ == "__main__":
    unittest.main()
