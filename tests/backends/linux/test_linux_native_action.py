"""Semantic selection plus real Linux native action/identity/lifetime code."""

import unittest
from copy import deepcopy

from linux_native_provider import NativeError, Node

import backends.linux.test_linux_native as read_tests
import backends.linux.test_linux_native_text as text_tests
from backends.linux.test_linux_native import context
from computer_use import ClickOption
from computer_use.errors import ComputerUseError
from computer_use.native.semantics import action_for_intent, control_capabilities
from computer_use.protocol.execution import decodeExecutionResult
from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.objects.registry import Window


class LinuxNativeActionTests(unittest.TestCase):
    setUp = read_tests.LinuxNativeFactsTests.setUp
    tearDown = read_tests.LinuxNativeFactsTests.tearDown
    begin = read_tests.LinuxNativeFactsTests.begin
    inspect = read_tests.LinuxNativeFactsTests.inspect
    surfaces = read_tests.LinuxNativeFactsTests.surfaces
    target = read_tests.LinuxNativeFactsTests.target
    collect = read_tests.LinuxNativeFactsTests.collect
    send = text_tests.LinuxNativeTextTests.send

    def action_setup(self, kind="click", parameters=None, actions=None):
        if actions is not None:
            self.backend.button.actions = list(actions)
        target = self.target()
        batch = self.collect(target)
        ref = next(n["node"] for n in batch["nodes"] if n["values"].get("name") == "确认")
        self.selected = dict(target, node=ref)
        self.operation = self.driver.perform(
            "begin",
            dict(mode="write", target=self.selected, intent=dict(kind=kind, parameters=parameters or {})),
            context(),
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

    def command(self, facts, name="click"):
        p = next(p for p in facts["primitives"] if p["action"]["name"] == name)
        return dict(kind="native_action", primitiveId=p["id"], target=self.selected, foreground=False)

    def calls(self):
        return [call for call in self.backend.ax.calls if call[0] == "do_action"]

    def test_reordered_action_uses_exact_identity_not_stale_index(self):
        facts = self.action_setup(actions=[("click", "Click"), ("custom", "Pin")])
        self.backend.button.actions.reverse()
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["confirmation"]), ("dispatched", "unavailable"))
        self.assertEqual(self.backend.button.action_history, ["click"])
        self.assertEqual(self.calls()[0][2], (1,))

    def test_secondary_description_keeps_the_exact_action_and_original_intent(self):
        facts = self.action_setup("action", {"action": "Pin"}, [("click", "Click"), ("custom", "Pin")])
        with self.assertRaises(Failure):
            self.send(self.command(facts))
        result = self.send(self.command(facts, "custom"), step=2)
        self.assertEqual(result["dispatch"], "dispatched")
        self.assertEqual(self.backend.button.action_history, ["custom"])

    def test_duplicate_names_preserve_ambiguity_but_allow_exact_unique_description(self):
        facts = self.action_setup(
            "action",
            {"action": "Pin"},
            [("custom", "Pin"), ("custom", "Unpin"), ("click", "Click"), ("click", "Click")],
        )
        self.assertEqual([p["supported"] for p in facts["primitives"]], ["yes", "yes", "no", "no"])
        self.assertTrue(facts["actionsComplete"])
        self.assertIsNone(
            action_for_intent("linux", "button", [p["action"]["name"] for p in facts["primitives"]], True, "click")
        )
        command = dict(
            kind="native_action", primitiveId=facts["primitives"][0]["id"], target=self.selected, foreground=False
        )
        self.backend.button.actions.reverse()
        self.assertEqual(self.send(command)["dispatch"], "dispatched")
        self.assertEqual(self.calls()[0][2], (3,))

    def test_missing_or_ambiguous_action_is_not_dispatched(self):
        for actions in ([], [("click", "Click the button")] * 2):
            facts = self.action_setup()
            self.backend.button.actions = actions
            result = self.send(self.command(facts))
            self.assertEqual(result["dispatch"], "notDispatched")
            self.assertEqual(self.calls(), [])
            self.driver.finish(self.operation)
            self.backend.button.actions = [("click", "Click the button")]

    def test_only_description_based_requests_depend_on_description(self):
        facts = self.action_setup()
        self.backend.button.actions = [("click", "Updated explanation")]
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.driver.finish(self.operation)
        facts = self.action_setup("action", {"action": "Updated explanation"})
        self.backend.button.actions = [("click", "Another explanation")]
        self.assertEqual(self.send(self.command(facts))["dispatch"], "notDispatched")
        self.assertEqual(self.backend.button.action_history, ["click"])

    def test_description_read_failure_does_not_hide_an_unambiguous_named_action(self):
        self.backend.button.errors["get_action_description"] = NativeError("no description")
        facts = self.action_setup()
        self.assertTrue(facts["actionsComplete"])
        self.assertNotIn("description", facts["primitives"][0]["action"])
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.assertEqual(self.backend.button.action_history, ["click"])

    def test_incomplete_catalogue_cannot_claim_name_uniqueness(self):
        facts = self.action_setup(actions=[(f"action{i}", "") for i in range(129)])
        self.assertFalse(facts["actionsComplete"])
        self.assertEqual(facts["primitives"], [])
        self.assertEqual(self.calls(), [])

    def test_every_action_in_a_maximum_reply_has_a_live_contract(self):
        facts = self.action_setup("action", {"action": "action0"}, [(f"action{i}", "") for i in range(128)])
        self.assertEqual(len(facts["primitives"]), 128)
        self.assertEqual(self.send(self.command(facts, "action0"))["dispatch"], "dispatched")
        self.assertEqual(self.backend.button.action_history, ["action0"])

    def test_target_changes_during_catalogue_query_stop_before_dispatch(self):
        facts = self.action_setup()
        foreign = Node("frame")

        def change(obj):
            self.backend.button.parent = foreign

        self.backend.ax.hooks["get_n_actions"] = change
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["errorCode"]), ("notDispatched", "stale_target"))
        self.assertEqual(self.calls(), [])

    def test_modal_disabled_lock_and_role_change_are_rechecked(self):
        for mode in ("modal", "disabled", "locked", "role"):
            facts = self.action_setup()
            if mode == "modal":
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
            elif mode == "disabled":
                self.backend.button.states -= {"ENABLED", "SENSITIVE"}
            elif mode == "locked":
                self.backend.locked = True
            else:
                self.backend.button.role = "entry"
            self.assertEqual(self.send(self.command(facts))["dispatch"], "notDispatched")
            self.assertEqual(self.calls(), [])
            self.driver.finish(self.operation)
            self.backend.desktop.items = self.backend.desktop.items[:1]
            self.backend.button.states |= {"ENABLED", "SENSITIVE"}
            self.backend.locked = False
            self.backend.button.role = "push button"

    def test_index_changes_during_final_guard_are_not_invoked(self):
        facts = self.action_setup(actions=[("click", "Click"), ("custom", "Pin")])
        count = 0

        def change(obj):
            nonlocal count
            if obj is self.backend.button:
                count += 1
                if count == 2:
                    obj.actions.reverse()

        self.backend.ax.hooks["get_state_set"] = change
        self.assertEqual(self.send(self.command(facts))["dispatch"], "notDispatched")
        self.assertEqual(self.calls(), [])

    def test_false_return_after_effect_is_unknown_and_never_replayed(self):
        facts = self.action_setup()
        self.backend.ax.hooks["do_action"] = lambda obj, index: obj.action_history.append(obj.actions[index][0])
        self.backend.button.errors["do_action"] = False
        command = self.command(facts)
        result = self.send(command)
        self.assertEqual(result["dispatch"], "unknown")
        self.driver.finish(self.operation)
        with unittest.mock.patch.object(
            self.backend.ax, "begin", side_effect=AssertionError("A duplicate is not native input")
        ):
            self.assertEqual(self.send(command), result)
        self.assertEqual(self.backend.button.action_history, ["click"])

    def test_native_query_error_before_dispatch_is_not_an_unknown_action_effect(self):
        facts = self.action_setup()
        self.backend.button.errors["get_state_set"] = NativeError("provider unavailable")
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["errorCode"]), ("notDispatched", "input_unavailable"))
        self.assertEqual(self.calls(), [])

    def test_action_may_close_its_window_without_reclassifying_acceptance(self):
        facts = self.action_setup()
        self.backend.ax.hooks["do_action"] = lambda obj, index: setattr(self.backend.window, "dead", True)
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.assertEqual(self.backend.button.action_history, ["click"])

    def test_nonprimary_clicks_and_forged_contracts_do_not_enter_native_action(self):
        target = self.target()
        for parameters in ({"count": True}, {"x": 0, "y": 0}):
            with self.assertRaises(Failure):
                self.driver.perform(
                    "begin",
                    dict(mode="write", target=target, intent=dict(kind="click", parameters=parameters)),
                    context(),
                )
        for parameters in ({"button": "right"}, {"count": 2}):
            facts = self.action_setup(parameters=parameters)
            self.assertEqual(facts["primitives"][0]["supported"], "no")
            with self.assertRaises(Failure):
                self.send(self.command(facts))
            self.driver.finish(self.operation)
        facts = self.action_setup()
        for step, changes in enumerate(
            ({"kind": "native_text_edit"}, {"primitiveId": "invented"}, {"foreground": True}), 1
        ):
            with self.assertRaises(Failure):
                self.send({**self.command(facts), **changes}, step=step)
        self.assertEqual(self.calls(), [])


class LinuxNativeActionSDKTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = read_tests.LinuxNativeSDKTests.asyncSetUp
    asyncTearDown = read_tests.LinuxNativeSDKTests.asyncTearDown

    async def test_public_click_action_and_explicit_read_use_one_native_pipeline(self):
        app = self.computer.app((await self.computer.listApps())[0].id)
        tree = await app.read(view="both")
        self.assertRegex(tree.text, r"(?m)^\t\d+ button 确认,")
        button = await app.element([{"name": "确认"}])
        for action in (button.click, lambda: button.performAction("Click the button")):
            result = await action()
            self.assertEqual(result.status, "dispatched")
            self.assertFalse(hasattr(result, "text"))
        self.assertEqual((await button.readText()).text, "2")
        with self.assertRaises(ComputerUseError):
            await button.click(ClickOption(count=2))
        self.assertEqual((await button.readText()).text, "2")


class LinuxActionSemanticsTests(unittest.TestCase):
    def test_semantics_do_not_turn_submit_activation_or_focus_into_click(self):
        for role, actions, intent, expected in (
            ("push button", ["SetFocus", "Press"], "click", "Press"),
            ("button", ["click"], "click", "click"),
            ("check box", ["Toggle"], "click", "Toggle"),
            ("menu item", ["activate"], "click", "activate"),
            ("entry", ["activate"], "click", None),
            ("table cell", ["activate"], "click", None),
            ("button", ["SetFocus"], "click", None),
            ("button", ["showMenu"], "click", None),
            ("button", ["showMenu"], "openMenu", "showMenu"),
            ("button", ["click", "Click", "Toggle"], "click", None),
        ):
            with self.subTest(role=role, actions=actions, intent=intent):
                self.assertEqual(action_for_intent("linux", role, actions, True, intent), expected)
                self.assertIsNone(action_for_intent("linux", role, actions, False, intent))

    def test_declared_text_and_action_support_require_actual_driver_features(self):
        node = dict(
            values=dict(
                role="entry", editable=True, enabled=False, sensitive=True, interfaces=["Text", "EditableText"]
            ),
            fieldStatus={},
            actions=dict(state="complete", items=[]),
        )
        features = ("nativeTextInput", "nativeTextReplacement", "nativeActionIntents")
        original = deepcopy(node)
        caps = control_capabilities("linux", node, features)
        self.assertEqual((caps["typeText"], caps["replaceText"]), ("available", "available"))
        self.assertEqual(control_capabilities("linux", node)["typeText"], "unknown")
        self.assertEqual(node, original)
        node["values"]["sensitive"] = False
        self.assertTrue(all(v == "unavailable" for v in control_capabilities("linux", node, features).values()))
        node["values"].update(sensitive=True, role="password text")
        self.assertNotEqual(control_capabilities("linux", node, features)["typeText"], "available")


if __name__ == "__main__":
    unittest.main()
