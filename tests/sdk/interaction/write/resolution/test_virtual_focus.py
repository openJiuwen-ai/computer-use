"""A popup active item is input context only with an explicit editor relation."""

import unittest
from copy import deepcopy
from dataclasses import replace

from observation_fixture import facts_fixture, relation_fixture
from sdk.native.query.test_target_query import ROOT, GraphPort, node

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.interaction.write.resolution.virtual_focus import VirtualFocusQuery
from computer_use.native.contracts import (
    Channel,
    Dispatch,
    Facts,
    InputContext,
    Intent,
    Knowledge as K,
    Primitive,
    StepResult,
    VirtualReceiver,
)


def popup_graph():
    graph = dict(
        root=node("AXComboBox", AXHasPopup=True, AXEditable=True),
        popup=node("AXList"),
        a=node("AXRow", AXFocused=True),
        b=node("AXRow", AXFocused=True),
    )
    for value in graph.values():
        value["relations"] = {"AXWindow": ["window"], "AXParent": ["window"]}
    graph["root"]["relations"].update(AXOwns=["popup"], AXLinkedUIElements=[])
    graph["popup"]["relations"]["AXSelectedChildren"] = ["a"]
    for name in ("a", "b"):
        graph[name]["relations"]["AXParent"] = ["popup"]
    return graph


class VirtualPort(GraphPort):
    capabilities = frozenset(("nativeActuators", "nativeVirtualFocus", "nativeSelectionBasis"))

    def __init__(self, graph=None):
        super().__init__(graph or popup_graph())
        self.commands, self.inspections = [], []
        self.effect = Dispatch.DISPATCHED

    async def query_nodes(self, operation, target, fields, max_nodes, max_depth, timeout_ms):
        result = await super().query_nodes(operation, target, fields, max_nodes, max_depth, timeout_ms)
        for key in ("AXWindow", "AXParent"):
            values = self.graph[target.node]["relations"].get(key, [])
            result["requestedRelations"].append(key)
            result["relations"].append(
                dict(source=target.node, property=key, targets=values, state="complete", certainty="confirmed")
            )
            result["unread"] = sorted(set(result["unread"] + values) - {target.node})
        return facts_fixture(result)

    async def query_relation(self, operation, target, property, cursor, limit, timeout_ms):
        self.events.append(("relation", target, property))
        value = self.graph[target.node]["relations"].get(property, [])
        edge = (
            dict(targets=value, state="complete", certainty="confirmed") if isinstance(value, list) else deepcopy(value)
        )
        return relation_fixture(
            dict(
                kind="relation",
                session=target.session,
                surface=target.surface,
                platform=self.platform,
                observed=dict(id=target.node + "-" + property, startedAt=2, completedAt=3),
                relation=dict(source=target.node, property=property, **edge),
            )
        )

    async def inspect(self, operation, target, timeout_ms, *, within=(), actuator=None, receiver=None, basis=()):
        self.inspections.append((target, receiver, basis))
        primitive = Primitive(
            "keys",
            "key_sequence",
            Channel.KEYBOARD,
            K.YES,
            K.YES if receiver else K.NO,
            pending=() if receiver else ("receiver",),
            background_preparation=K.NO,
        )
        return Facts(
            target,
            "current",
            "editor",
            K.YES,
            K.YES,
            K.YES,
            (),
            (primitive,),
            InputContext(focused_node="a"),
            platform=self.platform,
            native_role="AXComboBox",
            receiver=receiver,
        )

    async def execute(self, operation, step, command, timeout_ms):
        self.commands.append(command)
        return StepResult(
            command.target,
            self.effect,
            "input",
            error_code="execution_unknown" if self.effect in (Dispatch.UNKNOWN, Dispatch.PARTIAL) else None,
        )


class VirtualFocusTests(unittest.IsolatedAsyncioTestCase):
    async def resolve(self, graph=None, focused="a"):
        self.port = VirtualPort(graph)
        self.query = VirtualFocusQuery(self.port, "operation", ROOT, lambda: 1000)
        return await self.query.resolve(focused)

    async def test_explicit_owned_and_linked_popup_preserve_only_structural_dependencies(self):
        for via_value, property_name in (("owns", "AXOwns"), ("linked", "AXLinkedUIElements")):
            graph = popup_graph()
            graph["root"]["relations"]["AXOwns"] = []
            graph["root"]["relations"][property_name] = ["popup"]
            with self.subTest(via=via_value):
                self.assertEqual(await self.resolve(graph), VirtualReceiver("popup", via_value))
                basis = self.query.basis
                self.assertEqual({d.node for d in basis}, {"root", "popup"})
                self.assertTrue(any("AXHasPopup" in d.fields for d in basis))
                self.assertTrue(any(property_name in d.relations for d in basis))
                self.assertFalse(any("AXFocused" in d.fields or "AXSelectedChildren" in d.relations for d in basis))

    async def test_active_item_can_change_without_changing_structural_contract(self):
        graph = popup_graph()
        first = await self.resolve(graph)
        basis = self.query.basis
        graph["popup"]["relations"]["AXSelectedChildren"] = ["b"]
        self.assertEqual(await self.resolve(graph, focused="b"), first)
        self.assertEqual(self.query.basis, basis)

    async def test_textarea_completion_uses_explicit_owner_and_selected_text_through_layout(self):
        graph = popup_graph()
        graph["root"]["values"]["AXRole"] = "AXTextArea"
        graph["a"]["values"]["AXRole"] = "AXStaticText"
        graph["layout"] = node("AXGroup")
        graph["layout"]["relations"] = {"AXParent": ["popup"], "AXWindow": ["window"]}
        graph["a"]["relations"]["AXParent"] = ["layout"]
        self.assertEqual(await self.resolve(graph), VirtualReceiver("popup", "owns"))
        # A nearby selected completion list is insufficient without the original
        # textarea's explicit ownership. A different text editor is never an item.
        graph["root"]["relations"]["AXOwns"] = []
        self.assertIsNone(await self.resolve(graph))
        graph["root"]["relations"]["AXOwns"] = ["popup"]
        graph["a"]["values"]["AXRole"] = "AXTextArea"
        self.assertIsNone(await self.resolve(graph))

    async def test_direct_focus_does_not_probe_popup_and_aliases_do_not_create_ambiguity(self):
        self.assertIsNone(await self.resolve(focused="root"))
        self.assertEqual(self.port.events, [])
        graph = popup_graph()
        graph["root"]["relations"]["AXLinkedUIElements"] = ["popup"]
        self.assertEqual(await self.resolve(graph), VirtualReceiver("popup", "owns"))

    async def test_selection_and_ancestry_without_explicit_relation_never_authorize_input(self):
        graph = popup_graph()
        graph["root"]["relations"]["AXOwns"] = []
        self.assertIsNone(await self.resolve(graph))

    async def test_contradictory_or_incomplete_evidence_is_not_a_receiver(self):
        for scenario in (
            "noPopup",
            "readonly",
            "wrongWindow",
            "multiple",
            "notSelected",
            "notFocused",
            "editorItem",
            "missingRole",
            "wrongParent",
            "cycle",
            "wrongPopupRole",
        ):
            graph = popup_graph()
            if scenario == "noPopup":
                graph["root"]["values"]["AXHasPopup"] = False
            elif scenario == "readonly":
                graph["root"]["values"]["AXEditable"] = False
            elif scenario == "wrongWindow":
                graph["popup"]["relations"]["AXWindow"] = ["another-window"]
            elif scenario == "multiple":
                graph["popup"]["relations"]["AXSelectedChildren"] = ["a", "b"]
            elif scenario == "notSelected":
                graph["popup"]["relations"]["AXSelectedChildren"] = ["b"]
            elif scenario == "notFocused":
                graph["a"]["values"]["AXFocused"] = False
            elif scenario == "editorItem":
                graph["a"]["values"]["AXRole"] = "AXTextField"
            elif scenario == "missingRole":
                graph["a"]["statuses"]["AXRole"] = "timeout"
            elif scenario == "wrongParent":
                graph["a"]["relations"]["AXParent"] = []
            elif scenario == "cycle":
                graph["a"]["relations"]["AXParent"] = ["a"]
            elif scenario == "wrongPopupRole":
                graph["popup"]["values"]["AXRole"] = "AXGroup"
            with self.subTest(scenario=scenario):
                self.assertIsNone(await self.resolve(graph))

    async def test_unknown_relation_fails_closed_without_claiming_absence(self):
        graph = popup_graph()
        graph["root"]["relations"]["AXOwns"] = dict(targets=[], state="timeout", certainty="unknown")
        with self.assertRaises(ComputerUseError) as caught:
            await self.resolve(graph)
        self.assertEqual((caught.exception.code, caught.exception.effect), ("observation_incomplete", "none"))

    async def test_two_nested_popups_claiming_the_same_item_are_ambiguous(self):
        graph = popup_graph()
        graph["outer"] = deepcopy(graph["popup"])
        graph["popup"]["relations"]["AXParent"] = ["outer"]
        graph["root"]["relations"]["AXOwns"] = ["popup", "outer"]
        with self.assertRaises(ComputerUseError) as caught:
            await self.resolve(graph)
        self.assertEqual(caught.exception.code, "ambiguous_target")

    async def test_engine_keeps_requested_target_and_passes_selected_receiver_once(self):
        port = VirtualPort()
        result = await Engine(port).execute(Intent("insert", {"text": "exactly once"}), ROOT)
        self.assertEqual(result["status"], "dispatched")
        self.assertEqual(len(port.commands), 1)
        command = port.commands[0]
        self.assertEqual((command.target, command.receiver), (ROOT, VirtualReceiver("popup", "owns")))
        self.assertFalse(command.foreground)
        self.assertTrue(port.inspections[-1][2])
        self.assertEqual(port.events[-1], ("finish", "op"))

    async def test_unknown_or_partial_dispatch_is_never_replayed(self):
        for effect in (Dispatch.PARTIAL, Dispatch.UNKNOWN):
            port = VirtualPort()
            port.effect = effect
            with self.subTest(effect=effect), self.assertRaises(ComputerUseError):
                await Engine(port).execute(Intent("insert", {"text": "once"}), ROOT)
            self.assertEqual(len(port.commands), 1)

    async def test_ready_input_does_not_acquire_unneeded_popup_dependencies(self):
        class ReadyPort(VirtualPort):
            async def inspect(self, *args, **kwargs):
                facts = await super().inspect(*args, **kwargs)
                return replace(facts, primitives=(replace(facts.primitives[0], readiness=K.YES, pending=()),))

        graph = popup_graph()
        graph["root"]["relations"]["AXOwns"] = dict(targets=[], state="timeout", certainty="unknown")
        port = ReadyPort(graph)
        self.assertEqual(
            (await Engine(port).execute(Intent("insert", {"text": "normal"}), ROOT))["status"], "dispatched"
        )
        self.assertFalse(any(event[0] == "relation" for event in port.events))
        self.assertIsNone(port.commands[0].receiver)

    async def test_old_driver_does_not_receive_new_relationship(self):
        port = VirtualPort()
        port.capabilities -= {"nativeVirtualFocus"}
        with self.assertRaises(ComputerUseError):
            await Engine(port).execute(Intent("insert", {"text": "do not send"}), ROOT)
        self.assertEqual(port.commands, [])
        self.assertTrue(all(receiver is None for _, receiver, _ in port.inspections))
