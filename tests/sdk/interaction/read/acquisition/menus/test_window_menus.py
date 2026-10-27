"""Window menu reads use bounded native containment, never open menus or input."""

import unittest
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

from observation_fixture import facts_fixture, relation_fixture, reply
from v5_handles import nativeComputer  # Frozen facade: private pipeline behavior baseline.

from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.menus.query import MenuQuery, WindowMenus
from computer_use.interaction.read.app import NativeAppReader
from computer_use.native.contracts import Binding, Cleanup, HostPolicy, OpenedOperation
from computer_use.native.lifecycle import CallLifecycle

ROOT = Binding("s", "i", "window", "surface", "root")


def node(role="filler", name="", children=(), **values):
    return dict(values=dict(role=role, name=name, **values), children=list(children), statuses={}, labels=[])


def graph():
    return dict(
        root=node("frame", "Document A", ["layout"]),
        layout=node(name="Named layout", children=["bar", "body"]),
        body=node("entry", "Document input"),
        bar=node("menu bar", children=["file", "help"]),
        file=node("menu", "File", ["open", "recent", "advanced", "disabled"]),
        open=node("menu item", "Open A", enabled=True, showing=False),
        recent=node("menu", "Recent", ["document"]),
        document=node("menu item", "Document A"),
        advanced=node("menu", "Advanced", ["run"]),
        run=node("menu item", "Run A"),
        disabled=node("menu item", "Disabled A", enabled=False),
        help=node("menu", "Help"),
    )


def two_bars():
    source = graph()
    subtree = ("bar", "file", "open", "recent", "document", "advanced", "run", "disabled", "help")
    for key in subtree:
        copy = deepcopy(source[key])
        copy["children"] = [child + "-second" for child in copy["children"]]
        source[key + "-second"] = copy
    source["layout"]["children"].append("bar-second")
    return source


class MenuPort:
    """Native facts seam: declared raw fields and relationships stay unmodified."""

    platform = "linux"
    policy = HostPolicy()

    def __init__(self, source=None):
        self.graph = deepcopy(graph() if source is None else source)
        self.connection = SimpleNamespace(
            capabilities=frozenset(), session="s", platform=self.platform, sessionIdentity="generation"
        )
        self.capabilities = self.connection.capabilities
        self.calls, self.events, self.edges, self.pages = CallLifecycle(self), [], {}, {}
        self.directory_count = 0
        self.change_directory = None
        self.windows = [self.surface()]
        self.node_kwargs = []

    def surface(self, **kwargs):
        return dict(
            window="window",
            instance="i",
            surface="surface",
            node="root",
            title="Document A",
            parent=None,
            parentState="none",
            modal=False,
            kind="window",
            nativeWindow=True,
            accessibility=True,
            **kwargs,
        )

    async def connect(self, timeout_ms):
        return Binding(self.connection.session, None, None, None)

    async def begin_read(self, target, timeout_ms):
        self.events.append(("begin_read", target))
        return OpenedOperation("op-" + str(len(self.events)), target)

    async def finish(self, token):
        self.events.append(("finish", token))
        return Cleanup.COMPLETE

    async def directory(self, token, target, query, timeout_ms):
        self.events.append(("directory", query["kind"]))
        if query["kind"] == "applications":
            return dict(
                session="s",
                stopReasons=[],
                applications=[dict(id="app", displayName="Fixture", identifier=None, path=None, isRunning=True)],
            )
        if query["kind"] != "surfaces":
            raise AssertionError("Window menu route must not issue a roots query")
        self.directory_count += 1
        if self.change_directory:
            self.change_directory(self)
        return dict(session="s", stopReasons=[], application="app", surfaces=deepcopy(self.windows))

    async def nodes(self, *args, **kwargs):
        scope_kind = kwargs.pop("scope_kind", None)
        return reply(
            await self.query_nodes(*args, **kwargs),
            driver_features=getattr(self, "capabilities", ()),
            scope_kind=scope_kind,
        )

    async def relation(self, *args, **kwargs):
        return reply(await self.query_relation(*args, **kwargs))

    async def query_nodes(self, operation, target, fields, max_nodes, max_depth, timeout_ms, **kwargs):
        self.events.append(("nodes", target))
        self.node_kwargs.append(dict(fields=list(fields), max_nodes=max_nodes, max_depth=max_depth, **kwargs))
        records, relations, visited = [], [], set()
        # Discovery uses depth zero. Final NativeReader collects the requested subtree.
        stack = [(target.node, 0)]
        while stack and len(records) < max_nodes:
            token, depth = stack.pop()
            if token in visited:
                continue
            visited.add(token)
            original = self.graph[token]
            values = {key: value for key, value in original["values"].items() if key in fields}
            statuses = {key: dict(state="absent") for key in fields if key not in values}
            for key, state in original["statuses"].items():
                if key in fields:
                    values.pop(key, None)
                    statuses[key] = dict(state=state)
            records.append(
                dict(
                    node=token,
                    requestedFields=fields,
                    values=values,
                    fieldStatus=statuses,
                    actions=dict(state="complete", items=[]),
                )
            )
            parents = [ref for ref, source in self.graph.items() if token in source["children"]]
            for property_name, destinations_value in (
                ("children", original["children"]),
                ("parent", parents),
                ("labelled-by", original["labels"]),
            ):
                edge = self.edges.get(
                    (token, property_name), dict(targets=destinations_value, state="complete", certainty="confirmed")
                )
                relations.append(dict(source=token, property=property_name, **deepcopy(edge)))
            if depth < max_depth:
                stack.extend((child, depth + 1) for child in reversed(original["children"]))
        unread = set(child for edge in relations for child in edge["targets"]) - visited
        return facts_fixture(
            dict(
                kind="nodes",
                session=target.session,
                surface=target.surface,
                platform=self.platform,
                observed=dict(id="facts-" + str(len(self.events)), startedAt=1, completedAt=2),
                roots=[target.node],
                nodes=records,
                requestedRelations=["children", "parent", "labelled-by"],
                relations=relations,
                unread=sorted(unread),
                stopReasons=[],
            )
        )

    async def query_relation(self, operation, target, property, cursor, limit, timeout_ms):
        self.events.append(("relation", cursor))
        return relation_fixture(
            dict(
                kind="relation",
                session=target.session,
                surface=target.surface,
                platform=self.platform,
                observed=dict(id="page-" + str(cursor), startedAt=2, completedAt=3),
                relation=deepcopy(self.pages[cursor]),
            )
        )


class WindowMenuQueryTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def query(port, root=ROOT, **kwargs):
        return MenuQuery(port, "op", root, lambda: 1000, **kwargs)

    @staticmethod
    def discovery(port, root=ROOT, **kwargs):
        return WindowMenus(port, "op", root, lambda: 1000, **kwargs)

    async def refused(self, awaitable, code="observation_incomplete"):
        with self.assertRaises(ComputerUseError) as caught:
            await awaitable
        self.assertEqual((caught.exception.code, caught.exception.effect), (code, "none"))
        return caught.exception

    async def test_named_layout_and_direct_named_menu_containers(self):
        port = MenuPort()
        self.query(port)
        bar = (await self.discovery(port).collect()).targets[0]
        self.assertEqual(bar, replace(ROOT, node="bar"))
        for path, target in [
            (["File"], "file"),
            (["File", "Recent"], "recent"),
            (["File", "Recent", "Document A"], "document"),
            (["File", "Open A"], "open"),
            (["File", "Disabled A"], "disabled"),
        ]:
            self.assertEqual((await self.query(port, bar).resolve_menu(path)).node, target)
        self.assertTrue(all(event[0] in ("nodes", "relation") for event in port.events))

    async def test_only_direct_siblings_match_and_names_must_be_unique(self):
        port = MenuPort()
        root = replace(ROOT, node="bar")
        await self.refused(self.query(port, root).resolve_menu(["File", "Document A"]), "element_not_found")
        port.graph["file"]["children"].append("other")
        port.graph["other"] = node("menu item", "Open A")
        await self.refused(self.query(port, root).resolve_menu(["File", "Open A"]), "ambiguous_target")
        port.graph["other"]["statuses"]["name"] = "timeout"
        await self.refused(self.query(port, root).resolve_menu(["File", "Open A"]))

    async def test_single_item_menu_wrapper_and_ambiguous_children(self):
        source = graph()
        source["file"] = node("menu item", "File", ["wrapper"])
        source["wrapper"] = node("menu", children=["open", "recent"])
        port = MenuPort(source)
        bar = replace(ROOT, node="bar")
        self.assertEqual((await self.query(port, bar).resolve_menu(["File"])).node, "wrapper")
        self.assertEqual((await self.query(port, bar).resolve_menu(["File", "Open A"])).node, "open")
        port.graph["file"]["children"].append("advanced")
        # A final read keeps every child rather than discarding the competing menu.
        self.assertEqual((await self.query(port, bar).resolve_menu(["File"])).node, "file")
        await self.refused(self.query(port, bar).resolve_menu(["File", "Open A"]), "ambiguous_target")
        port.graph["file"]["children"] = ["wrapper", "body"]
        await self.refused(self.query(port, bar).resolve_menu(["File", "Open A"]))

    async def test_standard_popup_menu_is_a_container_without_granting_an_action(self):
        source = graph()
        source["file"] = node("menu item", "File", ["wrapper"])
        source["wrapper"] = node("popup menu", children=["open", "recent"])
        source["recent"] = node("menu item", "Recent", ["nested"])
        source["nested"] = node("popup menu", children=["document"])
        port = MenuPort(source)
        app = nativeComputer(port).app("Fixture")
        observed = await app.read({"menuPath": ["File", "Recent"]})
        self.assertIn("Document A", observed.text)
        self.assertNotIn("Open A", observed.text)
        # Normalization retains menu semantics and the original object identity.
        raw = await NativeAppReader(port).read("Fixture", menu_path=["File", "Recent"])
        container = next(item for item in raw.menu.parts[0].tree.facts["nodes"] if item["node"] == "nested")
        self.assertEqual(container["values"]["role"], "menu")
        self.assertEqual(container["values"]["role_detail"], "popup")
        self.assertNotIn("(clickable)", observed.text)
        self.assertEqual(app._controller.state(app).query_target, replace(ROOT, node="nested"))
        leaf = await app.read({"menuPath": ["File", "Recent", "Document A"]})
        self.assertIn("Document A", leaf.text)
        self.assertEqual(app._controller.state(app).query_target, replace(ROOT, node="document"))
        port.edges[("nested", "parent")] = dict(targets=["body"], state="complete", certainty="confirmed")
        await self.refused(app.read({"menuPath": ["File", "Recent", "Document A"]}))
        self.assertTrue(
            all(kind in ("begin_read", "finish", "nodes", "directory", "relation") for kind, _ in port.events)
        )

    async def test_unknown_container_role_remains_explicitly_unresolved(self):
        source = graph()
        source["file"] = node("menu item", "File", ["wrapper"])
        source["wrapper"] = node("custom menu container", children=["open"])
        port = MenuPort(source)
        bar = replace(ROOT, node="bar")
        self.assertEqual((await self.query(port, bar).resolve_menu(["File"])).node, "file")
        await self.refused(self.query(port, bar).resolve_menu(["File", "Open A"]))

    async def test_other_windows_and_sheets_are_boundaries_not_candidates(self):
        port = MenuPort()
        port.graph["layout"]["children"].extend(["otherWindow", "otherSheet"])
        port.graph["otherWindow"] = node("frame", children=["barB"])
        port.graph["otherSheet"] = node("dialog", children=["barC"])
        port.graph["barB"] = node("menu bar")
        port.graph["barC"] = node("menu bar")
        self.assertEqual((await self.discovery(port).collect()).targets[0].node, "bar")
        self.assertFalse({"barB", "barC"} & {event[1].node for event in port.events if event[0] == "nodes"})
        port.graph["layout"]["children"].append("barB")
        discovered = await self.discovery(port).collect()
        self.assertEqual([target.node for target in discovered.targets], ["bar"])
        self.assertTrue(discovered.gaps)  # conflicting parent is not borrowed

    async def test_two_menu_bars_absence_unknown_role_and_incomplete_branches(self):
        port = MenuPort()
        port.graph["layout"]["children"].append("another")
        port.graph["another"] = node("menu bar")
        self.assertEqual(len((await self.discovery(port).collect()).targets), 2)
        port = MenuPort(dict(root=node("frame")))
        await self.refused(self.discovery(port).collect())
        for state in ("error", "absent"):
            port = MenuPort()
            port.graph["body"]["statuses"]["role"] = state
            discovered = await self.discovery(port).collect()
            self.assertEqual([target.node for target in discovered.targets], ["bar"])
            self.assertTrue(discovered.gaps)
        port = MenuPort()
        port.edges[("body", "children")] = dict(targets=[], state="partial", certainty="confirmed")
        self.assertTrue((await self.discovery(port).collect()).gaps)

    async def test_cycles_conflicting_parent_and_budget_do_not_prove_uniqueness(self):
        port = MenuPort()
        port.graph["body"]["children"].append("layout")
        await self.refused(self.discovery(port).collect())
        port = MenuPort()
        port.edges[("bar", "parent")] = dict(targets=["body"], state="complete", certainty="confirmed")
        await self.refused(self.discovery(port).collect())
        for options in (dict(max_nodes=2), dict(max_depth=1)):
            await self.refused(self.discovery(MenuPort(), **options).collect())
        port = MenuPort()
        port.graph["file"]["children"] = ["bar"]
        await self.refused(self.query(port, replace(ROOT, node="bar")).resolve_menu(["File", "File"]))

    async def test_complete_forward_edges_tolerate_unavailable_optional_reverse_parent(self):
        port = MenuPort()
        port.edges[("bar", "parent")] = dict(targets=[], state="error", certainty="unknown")
        self.assertEqual((await self.discovery(port).collect()).targets[0].node, "bar")

    async def test_paginated_siblings_are_fully_read_before_uniqueness(self):
        port = MenuPort()
        port.edges[("layout", "children")] = dict(
            targets=["bar"], state="partial", certainty="confirmed", offset=0, total=2, cursor="tail"
        )
        port.pages["tail"] = dict(
            source="layout",
            property="children",
            targets=["body"],
            state="partial",
            certainty="confirmed",
            offset=1,
            total=2,
        )
        self.assertEqual((await self.discovery(port).collect()).targets[0].node, "bar")
        self.assertIn(("relation", "tail"), port.events)
        port.graph["body"]["values"]["role"] = "menu bar"
        self.assertEqual(len((await self.discovery(port).collect()).targets), 2)
        port.pages["tail"]["total"] = 3
        await self.refused(self.discovery(port).collect())


class WindowMenuReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_matching_bars_keep_distinct_native_objects_and_read_intervals(self):
        port = MenuPort(two_bars())
        result = await NativeAppReader(port).read("Fixture", menu_path=["File"])
        self.assertEqual(result.menu.target, ROOT)
        self.assertEqual([p.target.node for p in result.menu.parts], ["file", "file-second"])
        a, b = result.menu.parts
        self.assertNotEqual(a.tree.facts["observed"]["id"], b.tree.facts["observed"]["id"])
        self.assertFalse(a.tree.refs.keys() & b.tree.refs.keys())
        self.assertEqual(result.text.count("Open A"), 2)
        self.assertIn("Menu sources: 2", result.text)
        self.assertTrue(all(p.tree.scope_kind == "window" for p in result.menu.parts))
        self.assertFalse(
            any(kind not in ("begin_read", "finish", "nodes", "directory", "relation") for kind, _ in port.events)
        )

    async def test_a_path_present_in_only_one_bar_keeps_that_exact_scope(self):
        source = two_bars()
        source["file-second"]["values"]["name"] = "Document"
        result = await NativeAppReader(MenuPort(source)).read("Fixture", menu_path=["File", "Recent"])
        self.assertEqual(len(result.menu.parts), 1)
        self.assertEqual(result.menu.target, replace(ROOT, node="recent"))
        self.assertNotIn("Menu sources:", result.text)
        self.assertIn("Document A", result.text)

    async def test_missing_path_in_all_bars_reports_no_match(self):
        with self.assertRaises(ComputerUseError) as caught:
            await NativeAppReader(MenuPort(two_bars())).read("Fixture", menu_path=["Absent"])
        self.assertEqual((caught.exception.code, caught.exception.effect), ("element_not_found", "none"))
        self.assertIn("2 observed menu sources", caught.exception.detail)

    async def test_unread_names_or_ambiguous_siblings_in_a_source_are_not_silently_skipped(self):
        for kind in ("unread", "duplicate"):
            source = two_bars()
            if kind == "unread":
                source["file-second"]["statuses"]["name"] = "error"
            else:
                source["extra"] = node("menu", "File")
                source["bar-second"]["children"].append("extra")
            with self.subTest(kind=kind), self.assertRaises(ComputerUseError) as caught:
                await NativeAppReader(MenuPort(source)).read("Fixture", menu_path=["File"])
            self.assertEqual(caught.exception.effect, "none")
            self.assertEqual(
                caught.exception.code, "observation_incomplete" if kind == "unread" else "ambiguous_target"
            )

    async def test_multi_source_node_budget_is_shared(self):
        port = MenuPort(two_bars())
        result = await NativeAppReader(port).read("Fixture", menu_path=["File"], max_nodes=14, max_depth=2)
        limits = [part["max_nodes"] for part in port.node_kwargs[-2:]]
        self.assertEqual(limits, [7, 7])
        self.assertLessEqual(
            sum(sum(node["observed"] for node in p.tree.facts["nodes"]) for p in result.menu.parts), 14
        )
        self.assertEqual(len(result.menu.parts), 2)

    async def test_partial_discovery_keeps_known_menus_and_reports_unread_virtual_tail(self):
        source = two_bars()
        source["body"]["values"]["role"] = "table"
        source["body"]["children"] = [f"cell-{n}" for n in range(20)]
        source.update({f"cell-{n}": node("table cell") for n in range(20)})
        port = MenuPort(source)
        port.edges[("body", "children")] = dict(
            targets=source["body"]["children"],
            state="partial",
            certainty="confirmed",
            offset=0,
            total=1000000,
            cursor="huge-tail",
        )
        result = await NativeAppReader(port).read("Fixture", menu_path=["File"], max_nodes=30, max_depth=3)
        self.assertEqual(len(result.menu.parts), 2)
        self.assertEqual(result.menu.target, ROOT)
        self.assertIn("Menu discovery incomplete", result.text)
        self.assertIn("subtree is unread", result.text)
        self.assertFalse(any(target.node.startswith("cell-") for kind, target in port.events if kind == "nodes"))
        self.assertNotIn(("relation", "huge-tail"), port.events)
        self.assertEqual(result.text.count("Open A"), 2)
        self.assertEqual(port.node_kwargs[0]["fields"], ["role"])

    async def test_partial_discovery_does_not_turn_an_unseen_path_into_absence(self):
        source = graph()
        source["body"]["statuses"]["role"] = "error"
        result = await NativeAppReader(MenuPort(source)).read("Fixture", menu_path=["File"])
        self.assertEqual(len(result.menu.parts), 1)
        self.assertEqual(result.menu.target, ROOT)
        self.assertIn("Other sources may exist", result.text)
        with self.assertRaises(ComputerUseError) as caught:
            await NativeAppReader(MenuPort(source)).read("Fixture", menu_path=["Not observed"])
        self.assertEqual(caught.exception.code, "observation_incomplete")
        self.assertIn("Other sources may remain unread", caught.exception.detail)

    async def test_disappearing_descendant_is_a_read_gap_but_expired_root_still_fails(self):
        for unavailable in ("body", "root", "file"):
            port = MenuPort()
            original = port.query_nodes

            async def query_nodes(operation, target, *args, original=original, unavailable=unavailable, **kwargs):
                if target.node == unavailable:
                    raise ComputerUseError(
                        dict(
                            code="stale_target",
                            effect="none",
                            target="native object",
                            detail="Object became unavailable",
                            next="Read again",
                        )
                    )
                return await original(operation, target, *args, **kwargs)

            port.query_nodes = query_nodes
            if unavailable == "body":
                result = await NativeAppReader(port).read("Fixture", menu_path=["File"])
                self.assertIn("Open A", result.text)
                self.assertIn("A window branch became unavailable", result.text)
                self.assertEqual(result.menu.target, ROOT)
            else:
                with self.subTest(unavailable=unavailable), self.assertRaises(ComputerUseError) as caught:
                    await NativeAppReader(port).read("Fixture", menu_path=["File"])
                self.assertEqual(caught.exception.code, "stale_target")

    async def test_public_multi_menu_read_registers_both_sources_without_picking_a_default(self):
        port = MenuPort(two_bars())
        app = nativeComputer(port).app("Fixture")
        result = await app.read({"menuPath": ["File"]})
        state = app._controller.state(app)
        self.assertEqual(state.query_target, ROOT)
        self.assertEqual(state.selection.root, replace(ROOT, node=None))
        bindings = {item["binding"].node: ref for ref, item in state.refs.items()}
        self.assertNotEqual(bindings["open"], bindings["open-second"])
        elements = {target: app.element(bindings[target]) for target in ("open", "open-second")}
        for target, element in elements.items():
            observed = await element.read()
            self.assertIn("Open A", observed.text)
            self.assertEqual(element._snapshot["binding"], replace(ROOT, node=target))
            self.assertIn(replace(ROOT, node=target), [item["binding"] for item in state.refs.values()])
        with self.assertRaises(ComputerUseError) as caught:
            await app.find([{"role": "menuItem", "name": "Open A"}]).read()
        self.assertEqual(caught.exception.code, "ambiguous_target")
        self.assertIn("Menu sources: 2", result.text)
        self.assertIsNone(result.screenshot)

    async def test_app_read_uses_window_scope_fixed_binding_and_one_menu_operation(self):
        port = MenuPort()
        reader = NativeAppReader(port)
        result = await reader.read("Fixture", menu_path=["File", "Recent"])
        self.assertEqual(result.menu.target, replace(ROOT, node="recent"))
        self.assertEqual(result.menu.parts[0].tree.scope_kind, "window")
        self.assertIn("working-window menu only", result.text)
        self.assertIn("Document A", result.text)
        self.assertNotIn("Open A", result.text)
        self.assertEqual(result.selection.root, replace(ROOT, node=None))
        self.assertEqual(result.menu.parts[0].directory["surfaces"], port.windows)
        begins = [target for kind, target in port.events if kind == "begin_read"]
        self.assertEqual(
            begins, [Binding("s", None, None, None), replace(ROOT, node=None), Binding("s", None, None, None)]
        )
        self.assertFalse(any(kind == "directory" and value == "roots" for kind, value in port.events))

    async def test_partial_tree_limits_and_explicit_fields_reach_selected_subtree(self):
        port = MenuPort()
        result = await NativeAppReader(port).read(
            "Fixture", menu_path=["File"], fields=["role", "name"], max_nodes=10, max_depth=2
        )
        self.assertEqual(port.node_kwargs[-1], dict(fields=["role", "name"], max_nodes=10, max_depth=2))
        self.assertEqual(result.menu.parts[0].tree.facts["roots"], ["file"])

    async def test_default_read_does_not_append_a_duplicate_menu_summary(self):
        port = MenuPort()
        result = await NativeAppReader(port).read("Fixture")
        self.assertIsNone(result.menus)
        self.assertNotIn("Application menu (", result.text)
        self.assertEqual(result.text.count("File"), 1)

    async def test_new_window_session_or_root_close_never_retargets_the_menu(self):
        for change in ("closed", "replaced", "session"):
            port = MenuPort()

            def alter(p, *, change=change):
                if p.directory_count == 2:
                    if change == "closed":
                        p.windows = []
                    elif change == "replaced":
                        p.windows[0]["surface"] = "other-surface"
                    else:
                        p.connection.session = "new"

            # Session changes are represented by connect on the final recheck.
            if change == "session":
                original = port.finish

                async def finish(token, *, original=original, port=port):
                    result = await original(token)
                    if len([x for x in port.events if x[0] == "begin_read"]) == 2:
                        port.connection.session = "new"
                    return result

                port.finish = finish
            else:
                port.change_directory = alter
            with self.subTest(change=change), self.assertRaises(ComputerUseError) as caught:
                await NativeAppReader(port).read("Fixture", menu_path=["File"])
            self.assertEqual(caught.exception.code, "stale_target")

    async def test_no_accessible_root_does_not_guess_or_query_global_roots(self):
        port = MenuPort()
        port.windows[0]["node"] = None
        port.windows[0]["accessibility"] = False
        with self.assertRaises(ComputerUseError) as caught:
            await NativeAppReader(port).read("Fixture", menu_path=["File"])
        self.assertEqual(caught.exception.code, "observation_incomplete")
        self.assertFalse(any(kind == "nodes" for kind, _ in port.events))

    async def test_public_menu_read_preserves_window_observation_and_element_bindings(self):
        port = MenuPort()
        app = nativeComputer(port).app("Fixture")
        await app.read()
        state = app._controller.state(app)
        observed = state.observation
        old_query = state.query_target
        result = await app.read({"menuPath": ["File", "Recent"]})
        self.assertIs(state.observation, observed)
        self.assertEqual(state.query_target, replace(ROOT, node="recent"))
        self.assertEqual(state.query_scope_kind, "window")
        self.assertEqual(state.selection.root, replace(ROOT, node=None))
        document = next(ref for ref, value in state.refs.items() if value["binding"].node == "document")
        self.assertEqual(state.refs[document]["binding"], replace(ROOT, node="document"))
        self.assertEqual(state.refs[document]["scope_kind"], "window")
        self.assertIn("Document A", (await app.element(document).read()).text)
        self.assertIn("Document A", (await app.find([{"role": "menuItem", "name": "Document A"}]).read()).text)
        self.assertEqual(observed.target, old_query)
        self.assertIsNone(result.screenshot)
        self.assertFalse(
            any(kind not in ("begin_read", "finish", "nodes", "directory", "relation") for kind, _ in port.events)
        )


if __name__ == "__main__":
    unittest.main()
