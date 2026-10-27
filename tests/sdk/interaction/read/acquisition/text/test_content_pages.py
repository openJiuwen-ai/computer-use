"""Public content continuations over the production Linux driver and codecs."""

import re
import unittest
from dataclasses import replace

from linux_native_provider import Node

import backends.linux.test_linux_native_tables as tables
from backends.linux.test_linux_native_element_pointer_parts import DriverConnection
from computer_use.client.factory import nativeComputer
from computer_use.errors import ComputerUseError
from computer_use.interaction.query.instances.scope import adopt
from computer_use.interaction.read.pages import ContentPages
from computer_use.interaction.read.views.publication import publish, tree_text
from computer_use.native.contracts import HostPolicy
from computer_use.native.scopes import RunningApplication, WorkingWindow
from computer_use.native.transport import ConnectionPort, decode_binding
from computer_use.protocol.execution import decodeInspectQuery
from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.runtime.native import query as native_query


def continuation(text, label="cells"):
    match = re.search(
        r"(\d+) " + label + r", continuation: node = await app\.element\(\1\); "
        r'await node\.read\(cursor="(p\d+)"\)',
        text,
    )
    if match is None:
        raise AssertionError("No public continuation for " + label + "\n" + text[-3000:])
    return match.group(1), match.group(2)


class ContentPageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.case = tables.LinuxNativeTableTests()
        self.case.setUp()
        self.case.table.children = []
        self.case.table.n_rows = 150
        self.case.cells = [Node("label", f"Value {i}", parent=self.case.table) for i in range(300)]
        self.case.table.cells = list(self.case.cells)
        self.case.table.selected_children = []
        self.connection = DriverConnection(self.case.driver)
        self.connection.session = "session"
        self.port = ConnectionPort(self.connection, HostPolicy())
        self.app = nativeComputer(self.port).app("fixture")
        self.controller = self.app._controller
        self.state = self.controller.state(self.app)
        self.state.running = RunningApplication("fixture", "fixture", "session")
        self.target = decode_binding(self.case.node_target(self.case.table))
        directory = dict(
            session="session",
            application="fixture",
            observed=dict(id="scope", startedAt=1, completedAt=1),
            stopReasons=[],
            surfaces=[
                dict(
                    window=self.target.root_window,
                    instance=self.target.instance,
                    surface=self.target.surface,
                    node=self.target.node,
                    title="Fixture",
                    kind="window",
                    parent=None,
                    parentState="none",
                    modal=False,
                    nativeWindow=True,
                    accessibility=True,
                )
            ],
        )
        adopt(
            self.controller, self.app, directory, WorkingWindow("fixture", "fixture", replace(self.target, node=None))
        )

    def tearDown(self):
        self.case.tearDown()

    async def first(self):
        self.tree = await self.controller.reader.reader.read(self.target)
        self.controller._remember(self.state, self.target, self.tree)
        result = publish(
            self.controller,
            self.app,
            tree_text(self.controller, self.target, self.tree),
            trees=((self.target, self.tree),),
        )
        return result

    async def test_public_pages_include_non_children_contents_and_keep_native_identity(self):
        first = await self.first()
        ref, cursor = continuation(first.text)
        self.assertIn("Value 127", first.text)
        self.assertNotIn("Value 128", first.text)
        element = await self.app.element(int(ref))
        count = len(self.connection.requests)
        second = await element.read(cursor=cursor)
        self.assertIn("Value 128", second.text)
        self.assertIn("Value 255", second.text)
        self.assertNotIn("Value 256", second.text)
        requests = self.connection.requests[count:]
        self.assertEqual([method for method, _ in requests], ["begin", "inspect", "finish"])
        self.assertEqual(requests[0][1]["mode"], "read")
        page = requests[1][1]["query"]["relationPage"]
        self.assertEqual((page["property"], page["limit"]), ("Table.cells", 128))
        self.assertNotEqual(page["cursor"], cursor, "Public alias must not expose the native cursor")
        ref2, cursor2 = continuation(second.text)
        self.assertEqual(ref2, ref)
        last = await (await self.app.element(int(ref2))).read(cursor=cursor2)
        self.assertIn("Value 299", last.text)
        self.assertNotIn("Continue reading", last.text)
        refs = self.state.refs
        self.assertIs(self.case.driver.store.get(refs[ref]["binding"].node, self.target.surface).obj, self.case.table)
        self.assertTrue(
            any(
                self.case.driver.store.get(s["binding"].node, self.target.surface).obj is self.case.cells[-1]
                for s in refs.values()
            )
        )
        self.assertFalse(any(method == "execute" or p.get("mode") == "write" for method, p in self.connection.requests))

    async def test_wrong_element_or_window_cannot_retarget_a_continuation(self):
        first = await self.first()
        ref, cursor = continuation(first.text)
        wrong = next(key for key in self.state.refs if key != ref)
        count = len(self.connection.requests)
        with self.assertRaises(ComputerUseError) as error:
            await (await self.app.element(int(wrong))).read(cursor=cursor)
        self.assertEqual((error.exception.code, error.exception.effect), ("invalid_request", "none"))
        for field in ("session", "instance", "root_window", "surface", "node"):
            with self.subTest(field=field), self.assertRaises(ComputerUseError):
                self.controller.pages.resolve(cursor, replace(self.target, **{field: "other"}))
        self.assertEqual(len(self.connection.requests), count)

    async def test_changed_coordinate_space_is_reported_and_never_restarted_at_another_offset(self):
        first = await self.first()
        ref, cursor = continuation(first.text)
        self.case.table.n_rows, self.case.table.n_columns = 100, 3
        count = len(self.connection.requests)
        with self.assertRaises(ComputerUseError) as error:
            await (await self.app.element(int(ref))).read(cursor=cursor)
        self.assertEqual((error.exception.code, error.exception.effect), ("snapshot_expired", "none"))
        requests = self.connection.requests[count:]
        self.assertEqual([method for method, _ in requests], ["begin", "inspect", "finish"])
        self.assertFalse(any(method == "execute" for method, _ in requests))

    async def test_public_coordinate_pages_accept_fresh_objects_without_rebinding_old_handles(self):
        first = await self.first()
        ref, cursor = continuation(first.text)
        first_cell = next(
            r
            for r in self.state.refs.values()
            if self.case.driver.store.get(r["binding"].node, self.target.surface).obj is self.case.cells[0]
        )
        old_binding = first_cell["binding"]
        self.case.table.cells = [Node("label", f"Current {i}", parent=self.case.table) for i in range(300)]
        second = await (await self.app.element(int(ref))).read(cursor=cursor)
        self.assertIn("Current 128", second.text)
        self.assertIn("Current 255", second.text)
        self.assertIn("Live coordinate page", second.text)
        self.assertIs(self.case.driver.store.get(old_binding.node, self.target.surface).obj, self.case.cells[0])
        self.assertFalse(any(method == "execute" for method, _ in self.connection.requests))

    async def test_reading_another_snapshot_does_not_rebind_the_original_page(self):
        first = await self.first()
        ref, cursor = continuation(first.text)
        await self.first()
        result = await (await self.app.element(int(ref))).read(cursor=cursor)
        self.assertIn("Value 255", result.text)

    async def test_publishing_keeps_each_collected_scope_without_using_the_mutable_ref_cache(self):
        first = await self.first()
        _, original = continuation(first.text)
        other = replace(self.target, surface="menu-surface", root_window=None)
        self.controller.pages.remember(other, self.tree)
        # Multiple retained trees can outlive entries in the bounded App cache.
        # Publication must use their captured scope, not whichever ref is now
        # retained under the same alias or require a root cache entry to exist.
        self.state.refs.clear()
        result = publish(
            self.controller,
            self.app,
            tree_text(self.controller, self.target, self.tree) + "\n" + tree_text(self.controller, other, self.tree),
            trees=((self.target, self.tree), (other, self.tree)),
        )
        aliases = re.findall(r'cursor="(p\d+)"', result.text)
        self.assertEqual(len(aliases), 2)
        self.assertEqual(aliases[0], original)
        self.assertEqual(self.controller.pages.resolve(aliases[0], self.target)["property"], "Table.cells")
        self.assertEqual(self.controller.pages.resolve(aliases[1], other)["property"], "Table.cells")
        with self.assertRaises(ComputerUseError):
            self.controller.pages.resolve(aliases[1], self.target)

    async def test_bad_options_are_rejected_before_native_access(self):
        first = await self.first()
        ref, cursor = continuation(first.text)
        count = len(self.connection.requests)
        for options in ({"cursor": ""}, {"cursor": 2}, {"cursor": "x" * 257}):
            with self.subTest(options=options), self.assertRaises(ComputerUseError) as error:
                await (await self.app.element(int(ref))).read(**options)
            self.assertEqual(error.exception.code, "invalid_request")
        self.assertEqual(len(self.connection.requests), count)

    async def test_capability_gate_omits_hints_and_does_not_send_a_new_request(self):
        self.connection.capabilities = tuple(c for c in self.connection.capabilities if c != "nativeRelationPages")
        result = await self.first()
        self.assertNotIn("Continue reading", result.text)
        count = len(self.connection.requests)
        with self.assertRaises(ComputerUseError) as error:
            await (await self.app.element(0)).read(cursor="p1")
        self.assertEqual(error.exception.code, "mode_unsupported")
        self.assertEqual(len(self.connection.requests), count)

    async def test_bounded_aliases_expire_without_reusing_their_names(self):
        self.controller.pages = ContentPages(capacity=1)
        first = await self.first()
        ref, cursor = continuation(first.text)
        second = await self.first()
        _, newer = continuation(second.text)
        self.assertNotEqual(cursor, newer)
        with self.assertRaises(ComputerUseError):
            await (await self.app.element(int(ref))).read(cursor=cursor)
        (self.assertIn("Value 128", (await (await self.app.element(int(ref))).read(cursor=newer)).text))

    def test_relation_page_wire_validation_is_shared_before_os_access(self):
        request = dict(
            kind="nodes",
            fields=["role"],
            maxNodes=3,
            maxDepth=1,
            relationPage=dict(property="Table.cells", cursor="original-page", limit=2),
        )
        self.assertEqual(decodeInspectQuery(request), request)
        self.assertEqual(native_query(request), request)
        for invalid in (
            dict(request, maxNodes=2),
            dict(request, maxDepth=0),
            dict(request, childrenPage=dict(cursor=None, limit=2)),
            dict(request, relationPage=dict(property="", cursor=None, limit=2)),
            dict(request, relationPage=dict(property="Table.cells", cursor="p", limit=True)),
        ):
            with self.subTest(request=invalid):
                with self.assertRaises(ValueError):
                    decodeInspectQuery(invalid)
                with self.assertRaises(Failure):
                    native_query(invalid)
