"""Path matching discovers declared contents without inventing containment."""

import unittest
from copy import deepcopy
from dataclasses import replace

from linux_native_provider import NativeError, Node
from observation_fixture import facts_fixture
from sdk.native.query.test_target_query import ROOT, GraphPort, node

import backends.linux.test_linux_native_tables as tables
from backends.linux.test_linux_native_element_pointer_parts import DriverConnection
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.native.contracts import HostPolicy, Intent
from computer_use.native.query.path import TargetQuery
from computer_use.native.semantics import followed_relations
from computer_use.native.transport import ConnectionPort, decode_binding


class ContentPort(GraphPort):
    capabilities = ("nativeFollowRelations",)

    async def query_nodes(self, operation, target, fields, max_nodes, max_depth, timeout_ms, *, follow_relations=()):
        batch = await super().query_nodes(operation, target, fields, max_nodes, max_depth, timeout_ms)
        for property_name in follow_relations:
            edge = self.edges.get(
                (target.node, property_name), dict(targets=[], state="unsupported", certainty="unknown")
            )
            batch["relations"].append(dict(source=target.node, property=property_name, **deepcopy(edge)))
            batch["requestedRelations"].append(property_name)
        batch["unread"] = sorted({child for edge in batch["relations"] for child in edge["targets"]} - {target.node})
        return facts_fixture(batch)


class ContentQueryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.port = ContentPort(
            dict(
                root=node("AXWindow", children=["table"]),
                table=node("AXTable", "Items"),
                item=node("AXStaticText", "Target"),
                other=node("AXStaticText", "Other"),
            )
        )
        self.port.edges["table", "AXRows"] = dict(targets=["item"], state="complete", certainty="confirmed")

    async def resolve(self, path, **budgets):
        query = TargetQuery(self.port, "op", ROOT, lambda: 1000, **budgets)
        target = await query.resolve(path)
        return target, query.within

    async def test_explicit_content_anchor_finds_real_object_without_children(self):
        target, within = await self.resolve(["Items", "Target"])
        self.assertEqual(target, replace(ROOT, node="item"))
        self.assertEqual(within, ("root", "table"))
        self.assertEqual(self.port.graph["table"]["children"], [])

    async def test_shared_objects_across_children_visible_and_full_rows_are_one_candidate(self):
        self.port.graph["table"]["children"] = ["item"]
        self.port.edges["table", "AXVisibleRows"] = dict(
            targets=["item", "item"], state="complete", certainty="confirmed"
        )
        self.assertEqual((await self.resolve(["Target"]))[0].node, "item")
        self.port.graph["other"]["values"]["AXTitle"] = "Target"
        self.port.edges["table", "AXRows"]["targets"].append("other")
        with self.assertRaises(ComputerUseError) as error:
            await self.resolve(["Target"])
        self.assertEqual(error.exception.code, "ambiguous_target")

    async def test_visible_subset_does_not_hide_a_partial_full_relation(self):
        self.port.edges["table", "AXVisibleRows"] = dict(targets=["item"], state="complete", certainty="confirmed")
        for status in ("error", "timeout", "partial"):
            self.port.edges["table", "AXRows"] = dict(targets=[], state=status, certainty="unknown")
            with self.subTest(status=status), self.assertRaises(ComputerUseError) as error:
                await self.resolve(["Target"])
            self.assertEqual(error.exception.code, "observation_incomplete")

    async def test_content_pages_are_followed_with_the_observed_cursor(self):
        self.port.edges["table", "AXRows"] = dict(
            targets=["other"], state="partial", certainty="confirmed", offset=0, total=2, cursor="rows:1"
        )
        self.port.pages["rows:1"] = dict(
            source="table",
            property="AXRows",
            targets=["item"],
            state="partial",
            certainty="confirmed",
            offset=1,
            total=2,
        )
        self.assertEqual((await self.resolve(["Items", "Target"]))[0].node, "item")
        self.assertIn(("relation", "rows:1"), self.port.events)
        self.port.pages["rows:1"]["total"] = 3
        with self.assertRaises(ComputerUseError) as error:
            await self.resolve(["Target"])
        self.assertEqual(error.exception.code, "observation_incomplete")

    async def test_content_cycles_and_query_budget_remain_incomplete(self):
        self.port.edges["table", "AXRows"]["targets"] = ["root"]
        with self.assertRaises(ComputerUseError) as error:
            await self.resolve(["Target"])
        self.assertEqual(error.exception.code, "observation_incomplete")
        self.port.edges["table", "AXRows"]["targets"] = ["item", "other"]
        with self.assertRaises(ComputerUseError) as error:
            await self.resolve(["Target"], max_nodes=2)
        self.assertEqual(error.exception.code, "observation_incomplete")

    def test_content_profile_excludes_selection_headers_and_ownership(self):
        self.assertEqual(followed_relations("linux", ("nativeFollowRelations",), content_only=True), ())
        self.assertEqual(followed_relations("linux", ("nativeTableContent",), content_only=True), ())
        self.assertEqual(
            followed_relations("linux", ("nativeFollowRelations", "nativeTableContent"), content_only=True),
            ("Table.cells",),
        )
        self.assertEqual(followed_relations("windows", ("nativeFollowRelations",), content_only=True), ())
        self.assertFalse(
            set(followed_relations("macos", self.port.capabilities, content_only=True))
            & {"AXParent", "AXWindow", "AXSelectedCells", "AXRowHeaderUIElements", "AXTitleUIElement"}
        )

    async def test_older_driver_is_not_given_an_unnegotiated_content_profile(self):
        self.port.capabilities = ()
        with self.assertRaises(ComputerUseError) as error:
            await self.resolve(["Target"])
        self.assertEqual(error.exception.code, "element_not_found")


class LinuxContentQueryTests(unittest.IsolatedAsyncioTestCase):
    """Production Python and native pipeline; only native OS calls are replaced."""

    def setUp(self):
        self.case = tables.LinuxNativeTableTests()
        self.case.setUp()
        self.case.table.children = []
        self.connection = DriverConnection(self.case.driver)
        self.port = ConnectionPort(self.connection, HostPolicy())
        self.root = decode_binding(self.case.target())

    def tearDown(self):
        self.case.tearDown()

    async def resolve(self, path):
        async with self.port.calls.open(self.root, 10000) as lease:
            query = TargetQuery(self.port, lease.token, self.root, lease.remaining)
            return await query.resolve(path)

    async def test_merged_native_table_slots_find_one_original_node(self):
        self.case.table.cells[1] = self.case.table.cells[0]
        target = await self.resolve(["Table", "R1C1"])
        self.assertIs(self.case.driver.store.get(target.node, target.surface).obj, self.case.cells[0])
        self.assertFalse(any(method == "execute" for method, _ in self.connection.requests))

    async def test_find_and_action_share_one_operation_and_original_scope(self):
        cell = self.case.cells[-1]
        cell.interfaces.append("Action")
        cell.actions = [("activate", "Activate this cell")]
        self.connection.requests.clear()
        result = await Engine(self.port).execute(
            Intent("action", dict(action="activate")), self.root, path=["Table", "R3C2"]
        )
        self.assertEqual(result["status"], "dispatched")
        self.assertEqual(cell.action_history, ["activate"])
        self.assertFalse(any(n.action_history for n in self.case.cells[:-1]))
        self.assertEqual(sum(method == "begin" for method, _ in self.connection.requests), 1)
        self.assertEqual(sum(method == "execute" for method, _ in self.connection.requests), 1)

    async def test_content_membership_does_not_override_a_different_input_parent(self):
        cell = self.case.cells[-1]
        cell.interfaces.append("Action")
        cell.actions = [("activate", "Activate this cell")]
        cell.parent = Node("frame", "Another document", parent=self.case.backend.app)
        with self.assertRaises(ComputerUseError):
            await Engine(self.port).execute(
                Intent("action", dict(action="activate")), self.root, path=["Table", "R3C2"]
            )
        self.assertFalse(cell.action_history)
        self.assertFalse(any(method == "execute" for method, _ in self.connection.requests))

    async def test_unread_table_slot_cannot_prove_unique_match(self):
        self.case.table.cells[2] = None
        with self.assertRaises(ComputerUseError) as error:
            await self.resolve(["R1C1"])
        self.assertEqual(error.exception.code, "observation_incomplete")

    async def test_unknown_interface_query_does_not_hide_possible_contents(self):
        self.case.table.errors["get_interfaces"] = NativeError("provider unavailable")
        with self.assertRaises(ComputerUseError) as error:
            await self.resolve(["R1C1"])
        self.assertEqual(error.exception.code, "observation_incomplete")


if __name__ == "__main__":
    unittest.main()
