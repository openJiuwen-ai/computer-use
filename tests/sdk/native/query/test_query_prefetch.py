"""Batching native relation pages must preserve query semantics and evidence."""

import unittest
from copy import deepcopy

from linux_native_provider import Node
from observation_fixture import facts_fixture

import backends.linux.test_linux_native_tables as tables
from backends.linux.test_linux_native_element_pointer_parts import DriverConnection
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.native.contracts import HostPolicy, Intent
from computer_use.native.query.path import TargetQuery
from computer_use.native.transport import ConnectionPort, decode_binding


class QueryPrefetchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.case = tables.LinuxNativeTableTests()
        self.case.setUp()
        table = self.case.table
        self.cells = [Node("table cell", f"Item {i}", interfaces=["TableCell"], parent=table) for i in range(300)]
        for i, cell in enumerate(self.cells):
            cell.table, cell.span = table, (i, 0, 1, 1)
            cell.row_headers = cell.column_headers = []
        table.n_rows, table.n_columns = 300, 1
        table.cells, table.children, table.selected_children = self.cells, list(self.cells), []
        self.connection = DriverConnection(self.case.driver)
        self.port = ConnectionPort(self.connection, HostPolicy())
        self.root = decode_binding(self.case.target())

    def tearDown(self):
        self.case.tearDown()

    async def resolve(self, path=None, **options):
        async with self.port.calls.open(self.root, 10000) as lease:
            query = TargetQuery(self.port, lease.token, self.root, lease.remaining, **options)
            return await query.resolve(path or ["Table", "Item 299"]), query

    def requests(self):
        return [
            p["query"]
            for m, p in self.connection.requests
            if m == "inspect" and p["query"]["kind"] in ("nodes", "relation")
        ]

    def unexpanded_table(self, change=None):
        original = self.port.query_nodes
        table = self.case.driver.store.keep(self.root.surface, self.case.table)

        async def sparse(*args, **kwargs):
            first_page = kwargs.get("relation_page", {}).get("cursor", "not a page") is None
            if first_page and change == "count":
                self.case.table.children.pop()
            batch = await original(*args, **kwargs)
            if "relation_page" not in kwargs or (first_page and change == "no-progress"):
                for edge in batch["relations"]:
                    if edge["source"] == table and edge["property"] == "children":
                        edge.update(targets=[], offset=0, state="partial")
                        edge.pop("cursor", None)
                        if change == "unknown-count":
                            edge.pop("total", None)
            return facts_fixture(batch)

        self.port.query_nodes = sparse

    async def test_unexpanded_edge_starts_a_bounded_page_without_a_retained_cursor(self):
        self.unexpanded_table()
        target, query = await self.resolve()
        self.assertIs(self.case.driver.store.get(target.node, target.surface).obj, self.cells[-1])
        starts = [q for q in self.requests() if q.get("relationPage", {}).get("cursor", "not a page") is None]
        self.assertEqual(len(starts), 1)
        self.assertLessEqual(starts[0]["maxNodes"], query.max_nodes)
        self.assertFalse(any(m == "execute" for m, p in self.connection.requests))

    async def test_unexpanded_edge_count_change_still_refuses_selection(self):
        self.unexpanded_table("count")
        with self.assertRaises(ComputerUseError) as caught:
            await self.resolve()
        self.assertEqual(caught.exception.code, "observation_incomplete")
        self.assertFalse(any(m == "execute" for m, p in self.connection.requests))

    async def test_unexpanded_edge_needs_a_known_count_and_must_make_progress(self):
        for change in ("unknown-count", "no-progress"):
            original = self.port.query_nodes
            self.unexpanded_table(change)
            with self.subTest(change=change), self.assertRaises(ComputerUseError) as caught:
                await self.resolve()
            self.assertEqual(caught.exception.code, "observation_incomplete")
            self.port.query_nodes = original
        self.assertFalse(any(m == "execute" for m, p in self.connection.requests))

    async def test_child_and_content_pages_reuse_facts_instead_of_loading_each_leaf(self):
        target, query = await self.resolve()
        batched = self.requests()
        self.assertIs(self.case.driver.store.get(target.node, target.surface).obj, self.cells[-1])
        pages = [q for q in batched if "relationPage" in q]
        self.assertTrue(pages)
        self.assertFalse(
            any(q["relationPage"]["property"] == "Table.cells" for q in pages),
            "Overlapping content must reuse the already-collected child objects.",
        )
        self.assertTrue(all(q["maxDepth"] == 1 and q["maxNodes"] == q["relationPage"]["limit"] + 1 for q in pages))
        self.assertLessEqual(len(batched), 8)
        self.assertLessEqual(len(query.nodes), query.max_nodes)
        self.connection.capabilities = [c for c in self.connection.capabilities if c != "nativeRelationPages"]
        self.connection.requests.clear()
        older, _ = await self.resolve()
        self.assertEqual(older, target)
        self.assertGreater(len(self.requests()), 150)
        self.assertFalse(any("relationPage" in q for q in self.requests()))

    async def test_non_children_content_and_one_operation_action_keep_original_objects(self):
        self.case.table.children = []
        self.cells[-1].interfaces.append("Action")
        self.cells[-1].actions = [("activate", "Activate this item")]
        result = await Engine(self.port).execute(
            Intent("action", dict(action="activate")), self.root, path=["Table", "Item 299"]
        )
        self.assertEqual(result["status"], "dispatched")
        self.assertEqual(self.cells[-1].action_history, ["activate"])
        self.assertFalse(any(n.action_history for n in self.cells[:-1]))
        self.assertTrue(any(q.get("relationPage", {}).get("property") == "Table.cells" for q in self.requests()))
        self.assertEqual(sum(m == "begin" for m, p in self.connection.requests), 1)
        self.assertEqual(sum(m == "execute" for m, p in self.connection.requests), 1)

    async def test_duplicate_name_in_later_page_is_still_ambiguous(self):
        self.cells[-1].name = self.cells[1].name
        with self.assertRaises(ComputerUseError) as caught:
            await self.resolve(["Table", "Item 1"])
        self.assertEqual(caught.exception.code, "ambiguous_target")
        self.assertFalse(any(m == "execute" for m, p in self.connection.requests))

    async def test_shared_node_budget_does_not_grow_with_each_page(self):
        with self.assertRaises(ComputerUseError) as caught:
            await self.resolve(max_nodes=160)
        self.assertEqual(caught.exception.code, "observation_incomplete")
        self.assertTrue(all(q.get("maxNodes", 0) <= 160 for q in self.requests()))

    async def test_reordered_native_prefix_cannot_be_rebound_by_batched_read(self):
        original = self.connection.request

        async def mutate(method, params, options):
            if params.get("query", {}).get("relationPage"):
                self.case.table.children[0], self.case.table.children[1] = (
                    self.case.table.children[1],
                    self.case.table.children[0],
                )
            return await original(method, params, options)

        self.connection.request = mutate
        with self.assertRaises(ComputerUseError) as caught:
            await self.resolve()
        self.assertEqual(caught.exception.code, "snapshot_expired")
        self.assertFalse(any(m == "execute" for m, p in self.connection.requests))

    async def test_page_facts_keep_their_observation_without_overwriting_old_fields(self):
        async with self.port.calls.open(self.root, 10000) as lease:
            query = TargetQuery(self.port, lease.token, self.root, lease.remaining)
            await query._node(self.root.node)
            table_ref = self.case.driver.store.keep(self.root.surface, self.case.table)
            observed = query.node_sources[table_ref]
            original = deepcopy(query.nodes[table_ref])
            self.case.table.name = "Renamed after the initial sample"
            await query._relation(table_ref, "children")
            self.assertEqual(query.node_sources[table_ref], observed)
            self.assertEqual(query.nodes[table_ref], original)
            target_ref = self.case.driver.store.keep(self.root.surface, self.cells[-1])
            self.assertNotEqual(query.node_sources[target_ref], observed)
            await query._field(target_ref, "name")
            self.assertTrue(
                any(b.node == target_ref and b.observed == query.node_sources[target_ref] for b in query.basis)
            )
            self.assertGreaterEqual(len(query.edge_sources[table_ref, "children"]), 2)

    async def test_membership_change_between_identity_and_facts_does_not_select_another_object(self):
        original = self.connection.request

        async def mutate(method, params, options):
            if params.get("query", {}).get("relationPage"):
                self.case.table.children[200] = Node("table cell", "New child", parent=self.case.table)
            return await original(method, params, options)

        self.connection.request = mutate
        with self.assertRaises(ComputerUseError) as caught:
            await self.resolve()
        self.assertEqual(caught.exception.code, "observation_incomplete")
        self.assertFalse(any(m == "execute" for m, p in self.connection.requests))

    async def test_wrong_page_scope_and_range_are_rejected_before_matching(self):
        original = self.port.query_nodes
        for corruption in ("scope", "offset"):

            async def mutate(*args, corruption=corruption, **kwargs):
                result = await original(*args, **kwargs)
                if kwargs.get("relation_page"):
                    if corruption == "scope":
                        result["surface"] = "foreign-window"
                    else:
                        page = kwargs["relation_page"]
                        edge = next(
                            r
                            for r in result["relations"]
                            if r["source"] == args[1].node and r["property"] == page["property"]
                        )
                        edge["offset"] -= 1
                return facts_fixture(result)

            self.port.query_nodes = mutate
            with self.subTest(corruption=corruption), self.assertRaises((ValueError, ComputerUseError)):
                await self.resolve()
        self.assertFalse(any(n.action_history for n in self.cells))


if __name__ == "__main__":
    unittest.main()
