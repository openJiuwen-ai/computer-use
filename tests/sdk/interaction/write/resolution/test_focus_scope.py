"""A coarse native focus report can confirm only its addressed, focused editor."""

import unittest
from dataclasses import replace

from sdk.interaction.write.resolution.test_virtual_focus import VirtualPort
from sdk.native.query.test_target_query import ROOT, node

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.interaction.write.resolution.focus_scope import FocusScopeQuery
from computer_use.native.contracts import FocusScope, InputContext, Intent, Knowledge as K


def graph():
    values = dict(
        root=node("AXTextArea", AXEditable=True),
        layout=node(),
        scope=node(),
        window=node("AXWindow"),
        peer=node("AXTextField", AXEditable=True),
    )
    for value in values.values():
        value["relations"] = {"AXWindow": ["window"], "AXParent": ["window"]}
    values["root"]["relations"]["AXParent"] = ["layout"]
    values["layout"]["relations"]["AXParent"] = ["scope"]
    return values


class ScopePort(VirtualPort):
    capabilities = frozenset(("nativeActuators", "nativeFocusScopes", "nativeSelectionBasis"))

    async def inspect(self, *args, **kwargs):
        facts = await super().inspect(*args, **kwargs)
        return replace(facts, native_role="AXTextArea", input=InputContext(focused_node="scope"))


class FocusScopeTests(unittest.IsolatedAsyncioTestCase):
    async def resolve(self, value=None, focused="scope"):
        self.port = ScopePort(value or graph())
        self.query = FocusScopeQuery(self.port, "operation", ROOT, lambda: 1000)
        return await self.query.resolve(focused)

    async def test_exact_editor_parent_path_is_retained_without_dynamic_focus_guards(self):
        self.assertEqual(await self.resolve(), FocusScope("scope"))
        self.assertEqual({d.node for d in self.query.basis}, {"root", "layout", "scope"})
        self.assertTrue(any("AXParent" in d.relations for d in self.query.basis))
        self.assertTrue(all("AXFocused" not in d.fields and "AXValue" not in d.fields for d in self.query.basis))
        self.assertFalse(any(e[0] == "nodes" and e[1].node == "peer" for e in self.port.events))

    async def test_direct_or_missing_focus_does_not_introduce_a_new_dependency(self):
        for focused in (None, "root"):
            self.assertIsNone(await self.resolve(focused=focused))
            self.assertEqual(self.port.events, [])

    async def test_cannot_cross_independent_controls_editable_containers_or_windows(self):
        for role in ("AXTextArea", "AXTextField", "AXComboBox", "AXButton", "AXCell", "AXWindow", "AXUnknown"):
            value = graph()
            value["layout"]["values"]["AXRole"] = role
            with self.subTest(role=role):
                self.assertIsNone(await self.resolve(value))
        for key in ("AXEditable", "settable.AXValue", "settable.AXSelectedText"):
            value = graph()
            value["layout"]["values"][key] = True
            self.assertIsNone(await self.resolve(value))
            value = graph()
            value["layout"]["statuses"][key] = "timeout"
            self.assertIsNone(await self.resolve(value))

    async def test_focusable_layout_is_not_itself_an_editor(self):
        value = graph()
        value["layout"]["values"]["settable.AXFocused"] = True
        self.assertEqual(await self.resolve(value), FocusScope("scope"))

    async def test_scope_requires_one_window_and_an_acyclic_bounded_parent_path(self):
        for target in ("root", "layout", "scope"):
            value = graph()
            value[target]["relations"]["AXWindow"] = ["outside"]
            self.assertIsNone(await self.resolve(value))
        for parents in ([], ["layout"], ["scope", "peer"]):
            value = graph()
            value["layout"]["relations"]["AXParent"] = parents
            self.assertIsNone(await self.resolve(value))
        self.assertIsNone(await self.resolve(focused="peer"))
        value = graph()
        value["root"]["values"]["AXEditable"] = False
        self.assertIsNone(await self.resolve(value))

    async def test_engine_uses_the_same_target_without_foreground_or_extra_dispatch(self):
        port = ScopePort(graph())
        result = await Engine(port).execute(Intent("insert", {"text": "one input"}), ROOT)
        self.assertEqual(result["status"], "dispatched")
        self.assertEqual(len(port.commands), 1)
        self.assertEqual(port.commands[0].target, ROOT)
        self.assertEqual(port.commands[0].receiver, FocusScope("scope"))
        self.assertFalse(port.commands[0].foreground)

    async def test_existing_ready_route_is_unchanged_and_capability_is_required(self):
        class Ready(ScopePort):
            async def inspect(self, *args, **kwargs):
                facts = await super().inspect(*args, **kwargs)
                return replace(facts, primitives=(replace(facts.primitives[0], readiness=K.YES, pending=()),))

        port = Ready(graph())
        await Engine(port).execute(Intent("insert", {"text": "normal"}), ROOT)
        self.assertIsNone(port.commands[0].receiver)
        self.assertFalse(any(event[0] == "nodes" and event[1].node == "scope" for event in port.events))
        unsupported = ScopePort(graph())
        unsupported.capabilities -= {"nativeFocusScopes"}
        with self.assertRaises(ComputerUseError):
            await Engine(unsupported).execute(Intent("insert", {"text": "unavailable"}), ROOT)
        self.assertEqual(unsupported.commands, [])
