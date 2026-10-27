"""AT-SPI owner declarations, real editor declarations and input stay distinct.

These use the shared Engine/ReceiverQuery with raw provider facts. Real native
provider contracts and GTK/Qt GUI effects have separate acceptance fixtures.
"""

import unittest
from copy import deepcopy

from observation_fixture import facts_fixture
from sdk.native.query.test_target_query import ROOT, GraphPort

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.interaction.write.resolution.receiver import ReceiverQuery, ReceiverResolution
from computer_use.native.contracts import Actuator, Channel, Facts, InputContext, Intent, Knowledge as K, Primitive
from computer_use.native.semantics import (
    UNKNOWN,
    control_capabilities,
    control_values,
    direct_editor,
    editing_evidence,
    field_value,
)

FEATURES = frozenset(
    (
        "nativeActuators",
        "nativeSelectionBasis",
        "nativeTextInput",
        "nativeTextReplacement",
        "nativeKeyboardIntents",
        "nativeElementPointer",
    )
)


def node(role="panel", *, children=(), parent=None, interfaces=("Component",), **values):
    return dict(
        values={
            **dict(
                role=role,
                enabled=True,
                sensitive=False,
                editable=False,
                readOnly=False,
                focusable=False,
                interfaces=list(interfaces),
            ),
            **values,
        },
        children=list(children),
        labels=[],
        parent=parent,
        statuses={},
        actions=dict(state="complete", items=[]),
    )


def editor(**values):
    return node(
        "entry", interfaces=("Component", "Text", "EditableText"), **{**dict(editable=True, focusable=True), **values}
    )


class LinuxReceiverPort(GraphPort):
    platform = "linux"
    capabilities = FEATURES

    def __init__(self, graph):
        super().__init__(graph)
        self.selections = []
        self.commands = []
        self.intent = None

    async def begin(self, intent, target, timeout_ms):
        self.intent = intent
        return await super().begin(intent, target, timeout_ms)

    async def query_nodes(self, operation, target, fields, max_nodes, max_depth, timeout_ms):
        value = facts_fixture(await super().query_nodes(operation, target, fields, max_nodes, max_depth, timeout_ms))
        names = dict(AXChildren="children", AXTitleUIElement="labelled-by")
        for relation in value["relations"]:
            relation["property"] = names[relation["property"]]
        value["requestedRelations"] = list(names.values())
        parent = self.graph[target.node]["parent"]
        if parent is not None:
            value["requestedRelations"].append("parent")
            value["relations"].append(
                dict(source=target.node, property="parent", targets=[parent], state="complete", certainty="confirmed")
            )
            value["unread"] = sorted(set(value["unread"]) | {parent})
        return facts_fixture(value)

    async def inspect(self, operation, target, timeout_ms, *, within=(), actuator=None, basis=()):
        self.selections.append((actuator, basis))
        selected = actuator.node if actuator else target.node
        declared = self.graph[selected]["values"]
        interfaces = declared["interfaces"]
        primitives = []
        if declared["editable"] and not declared["readOnly"]:
            if "EditableText" in interfaces:
                primitives.append(Primitive("object", "native_text_edit", Channel.OBJECT, K.YES, K.YES))
            elif declared["role"] == "spin button" and "Component" in interfaces and self.intent.kind == "replace":
                primitives.append(Primitive("keys", "key_sequence", Channel.KEYBOARD, K.YES, K.YES))
            elif declared["role"] == "entry" and self.intent.kind != "replace":
                primitives.append(Primitive("keys", "key_sequence", Channel.KEYBOARD, K.YES, K.YES))
        return Facts(
            target,
            "now",
            "composite",
            K.YES,
            K.YES,
            K.YES,
            (),
            tuple(primitives),
            InputContext(editor=selected if actuator else None, editor_owner=target.node if actuator else None),
            platform="linux",
            native_role=self.graph[target.node]["values"]["role"],
            actuator=actuator,
        )

    async def execute(self, operation, step, command, timeout_ms):
        self.commands.append(command)
        return await super().execute(operation, step, command, timeout_ms)


class LinuxReceiverQueryTests(unittest.IsolatedAsyncioTestCase):
    def query(self, graph):
        self.port = LinuxReceiverPort(graph)
        return ReceiverQuery(self.port, "op", ROOT, lambda: 1000)

    @staticmethod
    def composite(*, declared=False, focusable=False):
        return dict(
            root=node("combo box", editable=declared, focusable=focusable, children=["layout"]),
            layout=node(children=["field"], parent="root"),
            field=editor(parent="layout"),
        )

    async def test_owner_without_editable_or_with_editable_but_no_text_resolves_child(self):
        for declared, focusable in ((False, False), (True, True)):
            for require_editable in (False, True):
                with self.subTest(declared=declared, require_editable=require_editable):
                    query = self.query(self.composite(declared=declared, focusable=focusable))
                    self.assertEqual(
                        await query.resolve(require_editable=require_editable), ReceiverResolution(Actuator("field"))
                    )
                    self.assertEqual(query.root, ROOT)
                    used = {d.node: d for d in query.basis}
                    self.assertIn("interfaces", used["root"].fields)
                    self.assertIn("interfaces", used["field"].fields)
                    self.assertIn("readOnly", used["root"].fields)
                    self.assertIn("children", used["root"].relations)
                    self.assertIn("parent", used["field"].relations)

    async def test_sensitive_only_owner_and_editor_match_the_native_enabled_contract(self):
        graph = self.composite()
        for token in ("root", "field"):
            graph[token]["values"].update(enabled=False, sensitive=True)
        query = self.query(graph)
        self.assertEqual(await query.resolve(require_editable=True), ReceiverResolution(Actuator("field")))
        for token in ("root", "field"):
            used = next(d for d in query.basis if d.node == token)
            self.assertIn("enabled", used.fields)
            self.assertIn("sensitive", used.fields)
        self.assertNotIn("sensitive", next(d for d in query.basis if d.node == "layout").fields)
        graph["root"]["values"]["readOnly"] = True
        with self.assertRaises(ComputerUseError):
            await self.query(graph).resolve(require_editable=True)

    async def test_no_readable_text_still_accepts_an_explicit_focusable_text_control(self):
        for interfaces in (("Component",), ("Component", "Text")):
            query = self.query(
                dict(root=node("entry", editable=True, focusable=True, interfaces=interfaces, children=[]))
            )
            self.assertEqual(await query.resolve(require_editable=True), ReceiverResolution())
            self.assertEqual([e[1].node for e in self.port.events], ["root"])

    async def test_addressed_editable_object_does_not_require_focus_writability(self):
        query = self.query(dict(root=editor(focusable=False)))
        self.assertEqual(await query.resolve(require_editable=True), ReceiverResolution())
        self.assertEqual([e[1].node for e in self.port.events], ["root"])

    async def test_editable_spin_without_text_only_supports_full_replacement(self):
        graph = dict(
            root=node("spin button", interfaces=("Component", "Value"), editable=True, focusable=True, children=[])
        )
        port = LinuxReceiverPort(graph)
        result = await Engine(port).execute(Intent("replace", {"text": "84"}), ROOT)
        self.assertEqual(result["status"], "dispatched")
        self.assertEqual(port.commands[0].primitive.unit, "key_sequence")
        self.assertIsNone(port.commands[0].actuator)
        port = LinuxReceiverPort(graph)
        with self.assertRaises(ComputerUseError):
            await Engine(port).execute(Intent("insert", {"text": "84"}), ROOT)
        self.assertEqual(port.commands, [])

    async def test_spin_keyboard_editing_requires_editable_focusable_component(self):
        graph = dict(root=node("spin button", interfaces=("Component",), editable=True, focusable=True, children=[]))
        self.assertEqual(await self.query(graph).resolve(require_editable=True), ReceiverResolution())
        for changes in (
            dict(role="slider"),
            dict(editable=False),
            dict(readOnly=True),
            dict(focusable=False),
            dict(interfaces=[]),
        ):
            altered = deepcopy(graph)
            altered["root"]["values"].update(changes)
            with self.assertRaises(ComputerUseError):
                await self.query(altered).resolve(require_editable=True)

    async def test_independent_owner_contract_does_not_require_child_selection(self):
        graph = self.composite(declared=True, focusable=True)
        graph["root"]["values"]["interfaces"].extend(["Text", "EditableText"])
        # When contextual input needs an editor, discovery still identifies
        # the child. An already bound EditableText object contract needs none
        # of those child facts and keeps the explicitly requested object.
        self.assertEqual(await self.query(graph).resolve(require_editable=True), ReceiverResolution(Actuator("field")))
        port = LinuxReceiverPort(graph)
        await Engine(port).execute(Intent("replace", {"text": "one replacement"}), ROOT)
        self.assertEqual(len(port.commands), 1)
        self.assertIsNone(port.commands[0].actuator)
        self.assertEqual(port.commands[0].target, ROOT)
        self.assertFalse(any(event[0] == "nodes" for event in port.events))

    async def test_readonly_owner_overrides_editable_children(self):
        for values in (dict(readOnly=True), dict(enabled=False)):
            graph = self.composite(declared=True)
            graph["root"]["values"].update(values)
            query = self.query(graph)
            with self.assertRaises(ComputerUseError) as caught:
                await query.resolve(require_editable=True)
            self.assertEqual((caught.exception.code, caught.exception.effect), ("input_unavailable", "none"))
            self.assertEqual([e[1].node for e in self.port.events], ["root"])

    async def test_readonly_editor_is_not_selected_despite_editable_state_and_interface(self):
        graph = self.composite()
        graph["field"]["values"]["readOnly"] = True
        with self.assertRaises(ComputerUseError) as caught:
            await self.query(graph).resolve(require_editable=True)
        self.assertEqual(caught.exception.code, "input_unavailable")

    async def test_multiple_editors_are_ambiguous_even_if_one_is_readonly(self):
        for readonly in (False, True):
            graph = self.composite()
            graph["layout"]["children"].append("second")
            graph["second"] = editor(readOnly=readonly, parent="layout")
            with self.assertRaises(ComputerUseError) as caught:
                await self.query(graph).resolve(require_editable=True)
            self.assertEqual((caught.exception.code, caught.exception.effect), ("ambiguous_target", "none"))

    async def test_ordinary_form_cannot_borrow_its_only_text_field(self):
        graph = self.composite()
        graph["root"]["values"]["role"] = "panel"
        with self.assertRaises(ComputerUseError) as caught:
            await self.query(graph).resolve(require_editable=True)
        self.assertEqual(caught.exception.code, "input_unavailable")
        self.assertEqual([e[1].node for e in self.port.events], ["root"])

    async def test_layout_with_actions_or_readonly_state_cannot_be_crossed(self):
        for kind in ("action", "readonly"):
            graph = self.composite()
            if kind == "action":
                graph["layout"]["actions"]["items"] = [dict(name="activate", description="activate")]
            else:
                graph["layout"]["values"]["readOnly"] = True
            with self.assertRaises(ComputerUseError) as caught:
                await self.query(graph).resolve(require_editable=True)
            self.assertEqual(caught.exception.code, "input_unavailable")
            self.assertNotIn("field", [e[1].node for e in self.port.events])

    async def test_missing_or_failed_interfaces_do_not_authorize_a_receiver(self):
        for status in ("error", "unsupported"):
            graph = self.composite()
            graph["field"]["statuses"]["interfaces"] = status
            with self.assertRaises(ComputerUseError) as caught:
                await self.query(graph).resolve(require_editable=True)
            self.assertEqual(caught.exception.code, "observation_incomplete")

    async def test_focused_editor_must_belong_to_same_owner_and_complete_search(self):
        graph = self.composite()
        query = self.query(graph)
        self.assertEqual(
            await query.resolve(require_editable=True, focused="field"), ReceiverResolution(Actuator("field"))
        )
        self.assertIn("parent", next(d for d in query.basis if d.node == "field").relations)
        graph["field"]["parent"] = "other"
        graph["other"] = editor()
        with self.assertRaises(ComputerUseError) as caught:
            await self.query(graph).resolve(require_editable=True, focused="field")
        self.assertEqual(caught.exception.code, "observation_incomplete")

    async def test_engine_uses_selected_child_but_retains_requested_owner_for_insert_and_replace(self):
        for kind in ("insert", "replace"):
            for declared, focusable in ((False, False), (True, True)):
                port = LinuxReceiverPort(self.composite(declared=declared, focusable=focusable))
                result = await Engine(port).execute(Intent(kind, {"text": "中文😀"}), ROOT)
                self.assertEqual(result["status"], "dispatched")
                self.assertEqual(len(port.commands), 1)
                command = port.commands[0]
                self.assertEqual((command.target, command.actuator), (ROOT, Actuator("field")))
                self.assertEqual(command.primitive.unit, "native_text_edit")
                self.assertFalse(command.foreground)
                self.assertTrue(port.selections[-1][1])
                self.assertEqual(port.events[-1], ("finish", "op"))


class LinuxEditingSemanticsTests(unittest.TestCase):
    @staticmethod
    def facts(**values):
        return dict(
            values=values, fieldStatus={}, requestedFields=list(values), actions=dict(state="complete", items=[])
        )

    def test_local_editable_false_is_not_a_readonly_owner_hint(self):
        owner = self.facts(
            role="combo box", editable=False, readOnly=False, enabled=True, interfaces=["Component"], focusable=False
        )
        before = deepcopy(owner)
        caps = control_capabilities("linux", owner, FEATURES)
        self.assertEqual(caps["typeText"], "unknown")
        self.assertEqual(caps["replaceText"], "unknown")
        self.assertEqual(owner, before)

    def test_editable_spin_without_text_advertises_replacement_without_insert(self):
        source = self.facts(
            role="spin button",
            editable=True,
            readOnly=False,
            enabled=True,
            interfaces=["Component", "Value"],
            focusable=True,
        )
        before = deepcopy(source)
        caps = control_capabilities("linux", source, FEATURES)
        self.assertEqual(caps["replaceText"], "available")
        self.assertEqual(caps["typeText"], "unavailable")
        self.assertEqual(source, before)
        self.assertNotEqual(
            control_capabilities("linux", source, FEATURES - {"nativeKeyboardIntents"})["replaceText"], "available"
        )
        self.assertNotEqual(
            control_capabilities("linux", source, FEATURES - {"nativeElementPointer"})["replaceText"], "available"
        )

    def test_readonly_never_advertises_writes_and_does_not_remove_focus(self):
        source = self.facts(
            role="entry",
            editable=True,
            readOnly=True,
            enabled=True,
            interfaces=["Component", "Text", "EditableText"],
            focusable=True,
        )
        caps = control_capabilities("linux", source, FEATURES)
        self.assertEqual(
            (caps["typeText"], caps["replaceText"], caps["focus"]), ("unavailable", "unavailable", "available")
        )

    def test_known_noneditable_objects_do_not_advertise_unknown_replacement(self):
        for role in ("push button", "menu bar", "menu item", "label", "panel", "separator"):
            with self.subTest(role=role):
                source = self.facts(
                    role=role,
                    editable=False,
                    readOnly=False,
                    enabled=True,
                    interfaces=["Component", "Text", "EditableText"],
                    focusable=True,
                )
                before = deepcopy(source)
                caps = control_capabilities("linux", source, FEATURES)
                self.assertEqual((caps["typeText"], caps["replaceText"]), ("unavailable", "unavailable"))
                self.assertEqual(source, before)

    def test_noneditable_text_field_can_still_own_an_internal_editor(self):
        source = self.facts(
            role="entry", editable=False, readOnly=False, enabled=True, interfaces=["Component"], focusable=True
        )
        caps = control_capabilities("linux", source, FEATURES)
        self.assertEqual((caps["typeText"], caps["replaceText"]), ("unknown", "unknown"))

    def test_missing_editable_fact_keeps_replacement_unknown(self):
        source = self.facts(role="entry", enabled=True, interfaces=["Component", "Text", "EditableText"])
        caps = control_capabilities("linux", source, FEATURES)
        self.assertEqual((caps["typeText"], caps["replaceText"]), ("unknown", "unknown"))

    def test_keyboard_text_hint_does_not_claim_full_replacement_without_native_range(self):
        source = self.facts(
            role="entry", editable=True, readOnly=False, enabled=True, interfaces=["Component"], focusable=True
        )
        values = control_values("linux", source)
        self.assertIs(editing_evidence("textField", values, platform="linux"), True)
        self.assertIs(direct_editor("textField", values, platform="linux"), True)
        caps = control_capabilities("linux", source, FEATURES)
        self.assertEqual((caps["typeText"], caps["replaceText"]), ("available", "unknown"))
        without_keyboard = control_capabilities("linux", source, FEATURES - {"nativeKeyboardIntents"})
        self.assertEqual(without_keyboard["typeText"], "unknown")

    def test_combo_editable_state_alone_is_not_direct_text_receiving_evidence(self):
        source = self.facts(
            role="combo box", editable=True, readOnly=False, enabled=True, interfaces=["Component"], focusable=True
        )
        self.assertIs(editing_evidence("comboBox", control_values("linux", source), platform="linux"), False)
        self.assertEqual(control_capabilities("linux", source, FEATURES)["typeText"], "unknown")
        source["values"]["interfaces"].append("Text")
        self.assertIs(editing_evidence("comboBox", control_values("linux", source), platform="linux"), True)

    def test_interface_lists_and_readonly_are_strict_typed_facts(self):
        for value in ("EditableText", ["EditableText", 1], [False], None, ["x"] * 65):
            with self.subTest(value=value):
                self.assertIs(field_value("linux", self.facts(interfaces=value), "interfaces"), UNKNOWN)
        self.assertEqual(field_value("linux", self.facts(interfaces=["Text"]), "interfaces"), ("Text",))
        self.assertIs(field_value("linux", self.facts(readOnly="false"), "readOnly"), UNKNOWN)
        self.assertIs(field_value("linux", self.facts(readOnly=False), "readOnly"), False)


if __name__ == "__main__":
    unittest.main()
