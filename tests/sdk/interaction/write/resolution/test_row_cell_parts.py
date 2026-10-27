"""A row's exact passive cell hit does not authorize its independent editors."""

import unittest

from sdk.interaction.write.resolution.test_receiver_query import ReceiverPort
from sdk.native.query.test_target_query import ROOT, node

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.resolution.control_part import ControlPartQuery
from computer_use.native.contracts import Actuator, Intent


def row_graph():
    return dict(
        root=node("AXRow", children=["cell"]),
        cell={**node("AXCell", children=["label"]), "parents": ["root"]},
        label={**node("AXStaticText", name="Today"), "parents": ["cell"]},
    )


class RowCellPartTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    async def resolve(data, hit="label", **options):
        query = ControlPartQuery(ReceiverPort(data), "op", ROOT, lambda: 1000, intent=Intent("click"), **options)
        return await query.resolve(hit), query

    async def test_row_cell_passive_hit_binds_complete_path(self):
        actual, query = await self.resolve(row_graph())
        self.assertEqual(actual, Actuator("label", "controlPart"))
        self.assertEqual({d.node for d in query.basis}, {"root", "cell", "label"})
        for token in ("cell", "label"):
            self.assertTrue(
                any(
                    d.node == token and d.actions and "AXRole" in d.fields and "AXParent" in d.relations
                    for d in query.basis
                )
            )

    async def test_button_editor_and_nested_item_hits_remain_independent(self):
        for role in ("AXButton", "AXTextField", "AXTextArea", "AXCell", "AXRow", "AXWindow"):
            with self.subTest(role=role):
                data = row_graph()
                data["label"]["values"]["AXRole"] = role
                self.assertIsNone((await self.resolve(data))[0])

    async def test_existing_rename_caption_exception_does_not_expand_through_cell(self):
        data = row_graph()
        data["label"]["values"].update(
            {"AXRole": "AXTextField", "settable.AXValue": True, "settable.AXSelectedText": False}
        )
        data["label"]["actions"]["items"] = [dict(name="AXConfirm", description="confirm")]
        self.assertIsNone((await self.resolve(data))[0])

    async def test_cell_itself_is_not_absorbed_by_row(self):
        self.assertIsNone((await self.resolve(row_graph(), hit="cell"))[0])

    async def test_nonrow_owner_keeps_cell_boundary(self):
        for role in ("AXButton", "AXCell", "AXGroup", "AXTable"):
            data = row_graph()
            data["root"]["values"]["AXRole"] = role
            self.assertIsNone((await self.resolve(data))[0])

    async def test_nested_cell_is_not_a_direct_row_cell(self):
        data = row_graph()
        data["root"]["children"] = ["layout"]
        data["layout"] = node("AXGroup", children=["cell"])
        data["cell"]["parents"] = ["layout"]
        self.assertIsNone((await self.resolve(data))[0])

    async def test_cell_or_hit_unknown_editing_capability_does_not_authorize(self):
        for token in ("cell", "label"):
            for field in ("AXEditable", "settable.AXFocused", "settable.AXValue", "settable.AXSelectedText"):
                for state in ("true", "error"):
                    with self.subTest(token=token, field=field, state=state):
                        data = row_graph()
                        if state == "true":
                            data[token]["values"][field] = True
                        else:
                            data[token]["statuses"][field] = "error"
                        self.assertIsNone((await self.resolve(data))[0])

    async def test_cell_or_hit_independent_actions_and_partial_catalogue_reject(self):
        for token in ("cell", "label"):
            for action in ("AXPress", "AXOpen", "AXConfirm", "Custom"):
                data = row_graph()
                data[token]["actions"]["items"] = [dict(name=action, description=action)]
                self.assertIsNone((await self.resolve(data))[0])
            data = row_graph()
            data[token]["actions"]["state"] = "partial"
            self.assertIsNone((await self.resolve(data))[0])

    async def test_conflicting_cell_or_hit_parent_cannot_escape_row(self):
        for token in ("cell", "label"):
            data = row_graph()
            data[token]["parents"] = ["other-row"]
            with self.assertRaises(ComputerUseError):
                await self.resolve(data)

    async def test_unread_cell_children_and_budget_do_not_create_evidence(self):
        port = ReceiverPort(row_graph())
        port.edges["cell", "AXChildren"] = dict(targets=["label"], state="partial", certainty="confirmed")
        with self.assertRaises(ComputerUseError):
            await ControlPartQuery(port, "op", ROOT, lambda: 1000).resolve("label")
        with self.assertRaises(ComputerUseError):
            await self.resolve(row_graph(), max_nodes=2)


if __name__ == "__main__":
    unittest.main()
