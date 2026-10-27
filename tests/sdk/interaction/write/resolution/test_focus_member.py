"""Focus containment must describe the same control, not just a subtree."""

import unittest

from sdk.interaction.write.resolution.test_virtual_focus import VirtualPort
from sdk.native.query.test_target_query import ROOT, node

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.interaction.write.resolution.focus_member import FocusMemberQuery
from computer_use.native.contracts import FocusMember, Intent


def graph():
    result = dict(
        root=node("AXTextArea", AXEditable=True),
        layout=node(),
        a=node("AXStaticText"),
        independent=node("AXTextField", AXEditable=True),
    )
    for value in result.values():
        value["relations"] = {"AXWindow": ["window"], "AXParent": ["root"]}
    result["a"]["relations"]["AXParent"] = ["layout"]
    result["root"]["children"] = ["layout", "independent"]
    return result


class MemberPort(VirtualPort):
    capabilities = frozenset(("nativeActuators", "nativeFocusMembers", "nativeSelectionBasis"))


class FocusMemberTests(unittest.IsolatedAsyncioTestCase):
    async def resolve(self, value=None, focused="a"):
        self.port = MemberPort(value or graph())
        self.query = FocusMemberQuery(self.port, "operation", ROOT, lambda: 1000)
        return await self.query.resolve(focused)

    async def test_passive_content_keeps_native_parent_and_semantic_dependencies(self):
        self.assertEqual(await self.resolve(), FocusMember("a"))
        self.assertEqual({b.node for b in self.query.basis}, {"root", "layout", "a"})
        self.assertFalse(any(e[0] == "nodes" and e[1].node == "independent" for e in self.port.events))
        self.assertTrue(any("AXParent" in b.relations for b in self.query.basis))
        self.assertTrue(any(b.actions for b in self.query.basis))
        self.assertFalse(any("AXValue" in b.fields or "AXFocused" in b.fields for b in self.query.basis))

    async def test_independent_controls_in_the_path_are_not_members(self):
        for token in ("a", "layout"):
            for role in ("AXTextField", "AXTextArea", "AXComboBox", "AXButton", "AXCell", "AXWindow"):
                value = graph()
                value[token]["values"]["AXRole"] = role
                with self.subTest(token=token, role=role):
                    self.assertIsNone(await self.resolve(value))

    async def test_ambiguous_action_or_editing_declarations_do_not_authorize_membership(self):
        for key, setting in (
            ("AXEditable", True),
            ("settable.AXFocused", True),
            ("settable.AXSelectedText", True),
            ("settable.AXValue", True),
        ):
            value = graph()
            value["a"]["values"][key] = setting
            self.assertIsNone(await self.resolve(value))
            value = graph()
            value["a"]["statuses"][key] = "timeout"
            self.assertIsNone(await self.resolve(value))
        for state, items in (("unavailable", []), ("complete", [dict(name="AXPress", description="Press")])):
            value = graph()
            value["layout"]["actions"] = dict(state=state, items=items)
            self.assertIsNone(await self.resolve(value))

    async def test_collection_items_are_keyboard_scope_but_embedded_editors_are_not(self):
        for owner in ("AXList", "AXOutline", "AXTable"):
            value = graph()
            value["root"]["values"] = dict(AXRole=owner)
            value["a"]["values"] = {"AXRole": "AXCell", "settable.AXFocused": True}
            value["layout"]["values"] = {"AXRole": "AXRow", "settable.AXFocused": True}
            value["layout"]["actions"]["items"] = [dict(name="AXPress", description="Press")]
            self.assertEqual(await self.resolve(value), FocusMember("a"))
            value["a"]["values"]["AXRole"] = "AXTextField"
            self.assertIsNone(await self.resolve(value))

    async def test_no_parent_cycle_or_generic_form_does_not_prove_input_scope(self):
        for parent in ([], ["a"], ["layout", "root"]):
            value = graph()
            value["layout"]["relations"]["AXParent"] = parent
            self.assertIsNone(await self.resolve(value))
        value = graph()
        value["root"]["values"] = dict(AXRole="AXGroup")
        self.assertIsNone(await self.resolve(value))

    async def test_same_focus_needs_no_member_proof(self):
        self.assertIsNone(await self.resolve(focused="root"))
        self.assertEqual(self.port.events, [])

    async def test_engine_binds_passive_member_without_retargeting_the_request(self):
        port = MemberPort(graph())
        self.assertEqual(
            (await Engine(port).execute(Intent("insert", {"text": "owner"}), ROOT))["status"], "dispatched"
        )
        self.assertEqual(len(port.commands), 1)
        self.assertEqual(port.commands[0].target, ROOT)
        self.assertEqual(port.commands[0].receiver, FocusMember("a"))
        value = graph()
        value["a"]["values"] = dict(AXRole="AXTextField", AXEditable=True)
        port = MemberPort(value)
        with self.assertRaises(ComputerUseError):
            await Engine(port).execute(Intent("insert", {"text": "do not misroute"}), ROOT)
        self.assertEqual(port.commands, [])
