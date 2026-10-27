"""UIA facts, Python display hints and the declared object routes agree.

These are mechanism tests using real UIA field/method names. They do not claim
that the Windows UIA provider or input path ran on this host.
"""

import unittest
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

from observation_fixture import facts_fixture, from_records, normalize_fixture as normalizeFacts
from sdk.interaction.read.facts.test_native_facts import batch, node as fact_node
from sdk.native.query.test_target_query import ROOT, GraphPort

from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.reader import NativeReader
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.write.engine import Engine
from computer_use.interaction.write.resolution.receiver import ReceiverQuery, ReceiverResolution
from computer_use.native.contracts import (
    Channel,
    Dispatch,
    Facts,
    InputContext,
    Intent,
    Knowledge as K,
    Primitive,
    StepResult,
)
from computer_use.native.semantics import (
    FIELDS,
    UNKNOWN,
    action_for_intent,
    control_capabilities,
    control_values,
    editing_evidence,
    roleName,
)

FEATURES = frozenset(
    (
        "nativeOperations",
        "nativeBoundContracts",
        "nativeUnitReadinessV2",
        "nativeActionIntents",
        "nativeTextReplacement",
        "nativeWindowScope",
    )
)
VALUE = "ValuePatternIdentifiers.Pattern"


def source(role="Edit", *, actions=(), state="complete", **values):
    result = fact_node(
        "root",
        **{
            "ControlType": "ControlType." + role,
            "IsEnabled": True,
            "IsKeyboardFocusable": False,
            "SupportedPatterns": [],
            **values,
        },
    )
    result["actions"] = dict(state=state, items=[dict(name=a, description=a) for a in actions])
    return result


def windows_record(node):
    raw = batch([node])
    raw["platform"] = "windows"
    return raw


def snapshot(node, features=FEATURES):
    raw = windows_record(node)
    return normalizeFacts(raw, DisplayReferences("session"), driver_features=features)


class WindowsObjectPort(GraphPort):
    platform = "windows"
    capabilities = FEATURES

    def __init__(self, raw):
        super().__init__(
            {"root": dict(values=raw["values"], actions=raw["actions"], statuses={}, children=[], labels=[])}
        )
        self.connection = SimpleNamespace(platform=self.platform, capabilities=self.capabilities)
        self.intent = None
        self.commands = []
        self.requested_fields = []

    async def begin(self, intent, target, timeout_ms):
        self.intent = intent
        return await super().begin(intent, target, timeout_ms)

    async def begin_read(self, target, timeout_ms):
        return await self.begin(None, target, timeout_ms)

    async def query_nodes(self, operation, target, fields, max_nodes, max_depth, timeout_ms, **options):
        self.requested_fields.append(fields)
        result = await super().query_nodes(operation, target, fields, max_nodes, max_depth, timeout_ms)

        names = dict(AXChildren="RawViewChildren", AXTitleUIElement="LabeledBy")
        result["requestedRelations"] = list(names.values())
        for relation in result["relations"]:
            relation["property"] = names[relation["property"]]
        return facts_fixture(result)

    async def inspect(self, operation, target, timeout_ms, **scope):
        self.events.append(("inspect", target))
        raw = self.graph[target.node]
        primitives = [
            Primitive(a["name"], "native_action", Channel.OBJECT, K.YES, K.YES, action=a["name"])
            for a in raw["actions"]["items"]
        ]
        if (
            self.intent.kind == "replace"
            and VALUE in raw["values"].get("SupportedPatterns", [])
            and raw["values"].get("Value.IsReadOnly") is False
        ):
            primitives.append(Primitive("set-value", "native_text_edit", Channel.OBJECT, K.YES, K.YES))
        return Facts(
            target,
            "now",
            "UIA object",
            K.YES,
            K.YES,
            K.YES,
            (),
            tuple(primitives),
            InputContext(),
            platform=self.platform,
            native_role=raw["values"]["ControlType"],
            actions_complete=raw["actions"]["state"] == "complete",
        )

    async def execute(self, operation, step, command, timeout_ms):
        self.commands.append(command)
        return await super().execute(operation, step, command, timeout_ms)


class WindowsTextPort(WindowsObjectPort):
    capabilities = FEATURES | {"nativeTextInput", "nativeKeyboardIntents"}
    fail_object = False

    async def inspect(self, operation, target, timeout_ms, **scope):
        facts = await super().inspect(operation, target, timeout_ms, **scope)
        key = Primitive("text-keys", "key_sequence", Channel.KEYBOARD, K.YES, K.YES)
        return replace(facts, primitives=facts.primitives + (key,))

    async def execute(self, operation, step, command, timeout_ms):
        if self.fail_object and command.primitive.unit == "native_text_edit":
            self.commands.append(command)
            return StepResult(
                command.target,
                Dispatch.NOT_DISPATCHED,
                "Object route unavailable",
                error_code="input_unavailable",
                retry_reason="routeUnavailable",
            )
        return await super().execute(operation, step, command, timeout_ms)


class WindowsControlSemanticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_range_readonly_fact_supports_keyboard_editing_but_not_a_native_setter(self):
        for role in ("Document", "Edit"):
            raw = source(
                role,
                **{
                    "SupportedPatterns": ["TextPatternIdentifiers.Pattern"],
                    "Text.IsReadOnly": False,
                    "IsKeyboardFocusable": True,
                },
            )
            caps = snapshot(raw, WindowsTextPort.capabilities).roots[0]["capabilities"]
            self.assertEqual(caps["typeText"], "available")
            self.assertEqual(caps["replaceText"], "unknown")  # No selectable range declaration yet.
            port = WindowsTextPort(raw)
            await Engine(port).execute(Intent("insert", dict(text="hello")), ROOT)
            self.assertEqual([c.primitive.unit for c in port.commands], ["key_sequence"])
            self.assertTrue(any("Text.IsReadOnly" in fields for fields in port.requested_fields))
            view = snapshot(raw)
            self.assertEqual(view.native, from_records(windows_record(raw)))

    def test_keyboard_replacement_hint_needs_a_writable_editor_and_selectable_range(self):
        for selection in ("Single", "Multiple", "None", None, True, "unrecognized"):
            raw = source(
                "Document",
                **{
                    "SupportedPatterns": ["TextPatternIdentifiers.Pattern"],
                    "Text.IsReadOnly": False,
                    "Text.SupportedTextSelection": selection,
                    "IsKeyboardFocusable": True,
                },
            )
            caps = snapshot(raw, WindowsTextPort.capabilities).roots[0]["capabilities"]
            self.assertEqual(caps["replaceText"], "available" if selection in ("Single", "Multiple") else "unknown")
        raw["values"]["Text.IsReadOnly"] = True
        caps = snapshot(raw, WindowsTextPort.capabilities).roots[0]["capabilities"]
        self.assertEqual(caps["typeText"], "unavailable")
        self.assertEqual(caps["replaceText"], "unknown")

    async def test_ready_keyboard_without_actuator_feature_still_needs_editing_evidence(self):
        for role, value in [("Button", False), ("Document", True), ("Document", "mixed"), ("Document", None)]:
            raw = source(
                role,
                **{
                    "SupportedPatterns": ["TextPatternIdentifiers.Pattern"],
                    "Text.IsReadOnly": value,
                    "IsKeyboardFocusable": True,
                },
            )
            port = WindowsTextPort(raw)
            with self.assertRaises(ComputerUseError):
                await Engine(port).execute(Intent("insert", dict(text="wrong")), ROOT)
            self.assertFalse(port.commands)

    async def test_failed_object_route_does_not_authorize_keyboard_replacement(self):
        for role, succeeds in [("Custom", False), ("Edit", True)]:
            raw = source(role, **{"SupportedPatterns": [VALUE], "Value.IsReadOnly": False, "IsKeyboardFocusable": True})
            port = WindowsTextPort(raw)
            port.fail_object = True
            if succeeds:
                await Engine(port).execute(Intent("replace", dict(text="new")), ROOT)
            else:
                with self.assertRaises(ComputerUseError):
                    await Engine(port).execute(Intent("replace", dict(text="new")), ROOT)
            self.assertEqual(
                [c.primitive.unit for c in port.commands],
                ["native_text_edit", "key_sequence"] if succeeds else ["native_text_edit"],
            )

    def test_focus_hint_requires_both_driver_unit_and_observed_focusable(self):
        raw = source(**{"IsKeyboardFocusable": True})
        self.assertEqual(control_capabilities("windows", raw, FEATURES)["focus"], "unknown")
        features = FEATURES | {"nativeKeyboardIntents"}
        caps = control_capabilities("windows", raw, features)
        self.assertEqual(caps["focus"], "available")
        self.assertEqual(caps["typeText"], "unknown")
        raw["values"]["IsEnabled"] = False
        self.assertEqual(control_capabilities("windows", raw, features)["focus"], "unavailable")

    def test_roles_share_control_meanings_without_granting_actions(self):
        for native, semantic in dict(
            TabItem="tab",
            Tab="tabGroup",
            ListItem="row",
            Hyperlink="link",
            SplitButton="menuButton",
            Spinner="incrementor",
            ProgressBar="progressIndicator",
        ).items():
            with self.subTest(role=native):
                self.assertEqual(roleName("windows", "ControlType." + native), semantic)
                self.assertEqual(set(snapshot(source(native)).roots[0]["capabilities"].values()), {"unknown"})

    async def test_declared_primary_actions_have_same_read_and_execution_mapping(self):
        cases = [
            ("Button", ["Invoke.Invoke"], "Invoke.Invoke"),
            ("CheckBox", ["Toggle.Toggle"], "Toggle.Toggle"),
            ("CheckBox", ["Invoke.Invoke", "Toggle.Toggle"], "Toggle.Toggle"),
            ("ComboBox", ["Invoke.Invoke", "ExpandCollapse.Expand"], "ExpandCollapse.Expand"),
            ("ListItem", ["Invoke.Invoke", "SelectionItem.Select"], "SelectionItem.Select"),
            ("TabItem", ["SelectionItem.Select"], "SelectionItem.Select"),
            ("TreeItem", ["SelectionItem.Select", "ExpandCollapse.Expand"], "SelectionItem.Select"),
            ("SplitButton", ["Invoke.Invoke", "ExpandCollapse.Expand"], "Invoke.Invoke"),
            ("MenuItem", ["ExpandCollapse.Expand"], "ExpandCollapse.Expand"),
            ("Custom", ["Invoke.Invoke"], "Invoke.Invoke"),
        ]
        for role, actions, expected in cases:
            with self.subTest(role=role):
                raw = source(role, actions=actions)
                self.assertEqual(snapshot(raw).roots[0]["capabilities"]["click"], "available")
                port = WindowsObjectPort(raw)
                await Engine(port).execute(Intent("click"), ROOT)
                self.assertEqual([c.primitive.action for c in port.commands], [expected])
                self.assertEqual(port.commands[0].target, ROOT)
                self.assertFalse(any(e[0] == "nodes" for e in port.events))

    def test_absent_partial_or_duplicate_actions_do_not_guess_click(self):
        for actions, state in [
            ([], "complete"),
            (["Invoke.Invoke"], "partial"),
            (["Invoke.Invoke", "Invoke.Invoke"], "complete"),
            (["Window.Close"], "complete"),
            (["ScrollItem.ScrollIntoView"], "complete"),
            (["SelectionItem.AddToSelection"], "complete"),
        ]:
            with self.subTest(actions=actions, state=state):
                self.assertIsNone(
                    action_for_intent("windows", "ControlType.Button", actions, state == "complete", "click")
                )

    async def test_collection_item_without_select_does_not_invoke_open_command(self):
        for role in ("ListItem", "TreeItem", "DataItem"):
            raw = source(role, actions=["Invoke.Invoke"])
            self.assertIsNone(action_for_intent("windows", "ControlType." + role, ["Invoke.Invoke"], True, "click"))
            port = WindowsObjectPort(raw)
            with self.assertRaises(ComputerUseError):
                await Engine(port).execute(Intent("click"), ROOT)
            self.assertFalse(port.commands)
            await Engine(port).execute(Intent("action", dict(action="Invoke.Invoke")), ROOT)
            self.assertEqual(port.commands[-1].primitive.action, "Invoke.Invoke")

    async def test_open_menu_expands_menu_control_without_invoking_primary_action(self):
        port = WindowsObjectPort(source("SplitButton", actions=["Invoke.Invoke", "ExpandCollapse.Expand"]))
        await Engine(port).execute(Intent("openMenu"), ROOT)
        self.assertEqual(port.commands[0].primitive.action, "ExpandCollapse.Expand")
        self.assertIsNone(
            action_for_intent("windows", "ControlType.TreeItem", ["ExpandCollapse.Expand"], True, "openMenu")
        )

    async def test_explicit_secondary_action_remains_callable(self):
        port = WindowsObjectPort(source("Window", actions=["Window.Close"]))
        await Engine(port).execute(Intent("action", dict(action="Window.Close")), ROOT)
        self.assertEqual(port.commands[0].primitive.action, "Window.Close")
        self.assertEqual(
            snapshot(source("Window", actions=["Window.Close"])).roots[0]["capabilities"]["click"], "unknown"
        )

    async def test_value_pattern_is_replace_only_and_needs_no_focus_or_editor_search(self):
        for role in ("Edit", "Custom", "DataItem"):
            for protected in (False, True):
                with self.subTest(role=role, protected=protected):
                    raw = source(
                        role, **{"SupportedPatterns": [VALUE], "Value.IsReadOnly": False, "IsPassword": protected}
                    )
                    caps = snapshot(raw).roots[0]["capabilities"]
                    self.assertEqual(
                        caps, dict(click="unknown", typeText="unknown", replaceText="available", focus="unknown")
                    )
                    port = WindowsObjectPort(raw)
                    await Engine(port).execute(Intent("replace", dict(text="new value")), ROOT)
                    self.assertEqual([c.primitive.unit for c in port.commands], ["native_text_edit"])
                    self.assertFalse(any(e[0] == "nodes" for e in port.events))
                    for kind in ("insert", "paste"):
                        port = WindowsObjectPort(raw)
                        with self.assertRaises(ComputerUseError):
                            await Engine(port).execute(Intent(kind, dict(text="new value")), ROOT)
                        self.assertFalse(port.commands)

    def test_readonly_missing_pattern_and_readonly_driver_do_not_advertise_writes(self):
        raw = source(**{"SupportedPatterns": [VALUE], "Value.IsReadOnly": False})
        self.assertEqual(set(snapshot(raw, ()).roots[0]["capabilities"].values()), {"unknown"})
        self.assertEqual(
            snapshot(source("Button", actions=["Invoke.Invoke"]), ()).roots[0]["capabilities"]["click"], "unknown"
        )
        for patch in (
            {"SupportedPatterns": []},
            {"SupportedPatterns": ["TextPatternIdentifiers.Pattern"]},
            {"SupportedPatterns": ["RangeValuePatternIdentifiers.Pattern"]},
            {"Value.IsReadOnly": None},
            {"Value.IsReadOnly": 0},
        ):
            item = deepcopy(raw)
            item["values"].update(patch)
            self.assertEqual(snapshot(item).roots[0]["capabilities"]["replaceText"], "unknown")
        raw["values"]["Value.IsReadOnly"] = True
        self.assertEqual(snapshot(raw).roots[0]["capabilities"]["replaceText"], "unavailable")
        raw["values"]["IsEnabled"] = False
        self.assertEqual(set(snapshot(raw).roots[0]["capabilities"].values()), {"unavailable"})

    def test_unread_pattern_or_readonly_facts_stay_unknown(self):
        for key in ("SupportedPatterns", "Value.IsReadOnly"):
            for state in ("timeout", "error", "unsupported"):
                raw = source(**{"SupportedPatterns": [VALUE], "Value.IsReadOnly": False})
                raw["fieldStatus"][key] = dict(state=state)
                raw["values"].pop(key)
                self.assertEqual(control_capabilities("windows", raw, FEATURES)["replaceText"], "unknown")

    async def test_receiver_reads_actual_pattern_and_does_not_promote_text_or_range(self):
        for patterns, expected in (
            ([VALUE], True),
            (["TextPatternIdentifiers.Pattern"], UNKNOWN),
            (["RangeValuePatternIdentifiers.Pattern"], UNKNOWN),
        ):
            raw = source(**{"SupportedPatterns": patterns, "Value.IsReadOnly": False})
            self.assertIs(editing_evidence("textField", control_values("windows", raw), platform="windows"), expected)
        port = WindowsObjectPort(source(**{"SupportedPatterns": [VALUE], "Value.IsReadOnly": False}))
        query = ReceiverQuery(port, "op", ROOT, lambda: 1000)
        self.assertEqual(await query.resolve(require_editable=True), ReceiverResolution())
        self.assertIn("SupportedPatterns", port.requested_fields[0])
        self.assertIn("Value.IsReadOnly", next(d for d in query.basis if d.node == "root").fields)
        self.assertEqual(sum(e[0] == "nodes" for e in port.events), 1)

    async def test_default_read_requests_native_state_text_range_and_pattern_facts(self):
        port = WindowsObjectPort(source())
        await NativeReader(port).read(ROOT)
        fields = set(port.requested_fields[0])
        self.assertTrue(
            {
                "Text.DocumentRange",
                "Toggle.ToggleState",
                "ExpandCollapse.ExpandCollapseState",
                "SupportedPatterns",
                "RangeValue.Value",
                "RangeValue.IsReadOnly",
            }
            <= fields
        )
        self.assertNotEqual(FIELDS["windows"]["RangeValue.IsReadOnly"], FIELDS["windows"]["Value.IsReadOnly"])

    def test_toggle_and_disclosure_states_render_without_losing_raw_enums(self):
        for state, value in (("On", "on"), ("Off", "off"), ("Indeterminate", "mixed")):
            raw = source("CheckBox", **{"Toggle.ToggleState": state})
            view = snapshot(raw)
            self.assertEqual(view.roots[0]["toggleState"], value)
            self.assertIn("(" + value, view.render()["text"])
            self.assertEqual(view.native, from_records(windows_record(raw)))
        for state in ("Expanded", "Collapsed", "PartiallyExpanded", "LeafNode", "ProviderSpecific"):
            view = snapshot(source("TreeItem", **{"ExpandCollapse.ExpandCollapseState": state}))
            if state in ("Expanded", "Collapsed"):
                self.assertIs(view.roots[0]["expanded"], state == "Expanded")
            else:
                self.assertNotIn("expanded", view.roots[0])
                self.assertIn(
                    {"PartiallyExpanded": "partial", "LeafNode": "leaf"}.get(state, state), view.render()["text"]
                )

    def test_multiple_value_interfaces_are_order_independent_and_lossless(self):
        values = {
            "Value.Value": "editable string",
            "Text.DocumentRange": "full readable text",
            "RangeValue.Value": 42,
            "Toggle.ToggleState": "Off",
            "RangeValue.Minimum": 0,
            "RangeValue.Maximum": 100,
            "RangeValue.IsReadOnly": False,
        }
        for ordered in (values, dict(reversed(list(values.items())))):
            raw = source("Slider", **ordered)
            view = snapshot(raw)
            self.assertEqual(view.roots[0]["value"], "editable string")
            rendered = view.render()["text"]
            for retained in ("full readable text", "42", "off", "numeric_value.minimum", "numeric_value.maximum"):
                self.assertIn(retained, rendered)
            self.assertEqual(view.native, from_records(windows_record(raw)))
            self.assertEqual(view.roots[0]["capabilities"]["replaceText"], "unknown")

    def test_toggle_remains_visible_alongside_value_in_compact_output(self):
        for state, displayed in (("On", "on"), ("Off", "off"), ("Indeterminate", "mixed")):
            for text in ("", "provider text"):
                values = {"Name": "Setting", "Toggle.ToggleState": state, "Value.Value": text}
                for ordered in (values, dict(reversed(list(values.items())))):
                    raw = source("CheckBox", **ordered)
                    view = snapshot(raw)
                    result = view.render(compact=True)
                    self.assertIn("(" + displayed, result["text"])
                    self.assertEqual(view.roots[0]["value"], text)
                    self.assertEqual(view.roots[0]["toggleState"], displayed)
                    self.assertEqual(view.value_fields, {"0": "control_value.value"})
                    self.assertEqual(view.native, from_records(windows_record(raw)))

    def test_range_and_document_values_work_without_value_pattern(self):
        raw = source(
            "Slider",
            **{
                "RangeValue.Value": 0,
                "RangeValue.Minimum": 0,
                "RangeValue.Maximum": 10,
                "RangeValue.IsReadOnly": False,
            },
        )
        view = snapshot(raw)
        self.assertEqual(view.roots[0]["value"], "0")
        self.assertTrue(view.roots[0]["valueIsNumber"])
        self.assertEqual(view.roots[0]["capabilities"]["replaceText"], "unknown")
        document = snapshot(source("Document", **{"Text.DocumentRange": "Actual document text"}))
        self.assertEqual(document.roots[0]["value"], "Actual document text")
        self.assertIn("Actual document text", document.render()["text"])
        self.assertEqual(document.roots[0]["capabilities"]["typeText"], "unknown")

    def test_malformed_native_state_remains_visible_without_becoming_a_boolean(self):
        for field in ("Toggle.ToggleState", "ExpandCollapse.ExpandCollapseState"):
            for malformed in (True, 0, None, ["Expanded"], {"value": "On"}):
                with self.subTest(field=field, value=malformed):
                    raw = source("CheckBox", **{field: malformed})
                    view = snapshot(raw)
                    self.assertNotIn("value", view.roots[0])
                    self.assertNotIn("expanded", view.roots[0])
                    self.assertIn(
                        ("states.checked" if field == "Toggle.ToggleState" else "states.expanded")
                        if malformed is None
                        else field,
                        view.render()["text"],
                    )
                    self.assertEqual(view.native, from_records(windows_record(raw)))


if __name__ == "__main__":
    unittest.main()
