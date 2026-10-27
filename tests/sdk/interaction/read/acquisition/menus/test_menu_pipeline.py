"""Menu pipeline composition preserves source scopes, names and query effects."""

import unittest
from dataclasses import replace
from types import SimpleNamespace

from sdk.interaction.read.acquisition.menus.test_window_menus import ROOT, MenuPort, two_bars

from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.directory.service import NativeDirectory
from computer_use.interaction.read.acquisition.menus.model import (
    MatchedMenuItem,
    MenuItemFacts,
    MenuLevel,
    MenuLevelFacts,
    MenuPathRequest,
)
from computer_use.interaction.read.acquisition.menus.passes.match_menu_title import match_menu_title
from computer_use.interaction.read.acquisition.menus.passes.select_submenu import select_submenu
from computer_use.interaction.read.acquisition.menus.query import MenuObservation, MenuRead, NativeMenus, menu_failure
from computer_use.interaction.read.acquisition.reader import NativeReader
from computer_use.interaction.read.app import NativeAppReader
from computer_use.native.semantics import UNKNOWN
from computer_use.pipeline import Effect


class ApplicationMenuPort(MenuPort):
    def __init__(self):
        super().__init__()
        self.connection.capabilities = frozenset({"nativeApplicationRoots"})
        self.capabilities = self.connection.capabilities

    async def directory(self, token, target, query, timeout_ms):
        if query["kind"] == "roots":
            self.events.append(("directory", "roots"))
            return dict(
                session="s",
                application="app",
                stopReasons=[],
                roots=[dict(kind="menuBar", instance="menu-instance", surface="menu-surface", node="bar")],
            )
        return await super().directory(token, target, query, timeout_ms)


class PureMenuMatchingTests(unittest.TestCase):
    def query(self):
        return SimpleNamespace(fail=lambda code, detail: self.fail_menu(code, detail))

    @staticmethod
    def fail_menu(code, detail):
        raise menu_failure(code, detail)

    def level(self, query=None, path=("File", "Open")):
        return MenuLevel(MenuPathRequest(query or self.query(), path), "bar", ("bar",), 0, "File")

    def test_matching_uses_only_collected_sibling_names_and_keeps_unknowns(self):
        level = self.level()
        selected = match_menu_title(MenuLevelFacts(level, (("a", "File"), ("b", "Help"))), None)
        self.assertEqual((selected.selected, selected.ancestors), ("a", ("bar", "a")))
        for names, code in (
            ((("a", "File"), ("b", UNKNOWN)), "observation_incomplete"),
            ((("a", "File"), ("b", "File")), "ambiguous_target"),
            ((("a", "Help"),), "element_not_found"),
        ):
            with self.subTest(names=names), self.assertRaises(ComputerUseError) as caught:
                match_menu_title(MenuLevelFacts(level, names), None)
            self.assertEqual((caught.exception.code, caught.exception.effect), (code, "none"))

    def test_submenu_selection_is_pure_and_never_guesses_between_wrappers(self):
        level = self.level()
        selected = MatchedMenuItem(level, "file", ("bar", "file"))
        value = MenuItemFacts(selected, "menuBarItem", ("wrapper",), (("wrapper", "menu"),))
        self.assertEqual(select_submenu(value, None), ("wrapper", ("bar", "file", "wrapper")))
        with self.assertRaises(ComputerUseError) as caught:
            select_submenu(MenuItemFacts(selected, "menuItem", ("a", "b"), (("a", "menu"), ("b", "menu"))), None)
        self.assertEqual(caught.exception.code, "ambiguous_target")
        final = replace(selected, level=replace(level, request=MenuPathRequest(level.request.query, ("File",))))
        self.assertEqual(
            select_submenu(MenuItemFacts(final, "menuItem", ("a", "b"), (("a", "menu"), ("b", "menu"))), None),
            ("file", ("bar", "file")),
        )


class MenuPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_window_menu_trace_shares_call_identity_and_exposes_real_children(self):
        port, events = MenuPort(), []
        port.pipeline_diagnostic = events.append
        observed = await NativeAppReader(port).read("Fixture", menu_path=["File", "Recent"])
        self.assertIn("Document A", observed.text)
        entered = [event for event in events if event.phase == "enter"]
        names = [event.path[-1] for event in entered]
        for stage in (
            "MenuObservationPipeline",
            "MenuCollectionPipeline",
            "WorkingWindowMenuScope",
            "MenuPathPipeline",
            "MenuPathLevel",
            "collect_menu_level",
            "match_menu_title",
            "collect_selected_menu",
            "select_submenu",
            "FactCollectionPipeline",
            "NumberSnapshotsPipeline",
            "WorkingScopeDiscoveryPipeline",
            "MenuSourcesPresentationPipeline",
            "PrepareSnapshotRoots",
        ):
            self.assertIn(stage, names)
        self.assertEqual(names.count("match_menu_title"), 2)
        self.assertEqual(len({event.call_id for event in entered}), 1)
        self.assertIsNotNone(entered[0].call_id)
        for event in entered:
            if event.path[-1] in ("match_menu_title", "select_submenu"):
                self.assertEqual(event.effects, {Effect.PURE})
        self.assertTrue(
            all(kind in ("begin_read", "finish", "nodes", "directory", "relation") for kind, _ in port.events)
        )

    async def test_application_root_branch_uses_two_separate_scopes_and_original_context(self):
        port = ApplicationMenuPort()
        menus = NativeMenus(NativeDirectory(port), NativeReader(port))
        with port.calls.claim(30000) as remaining:
            observed = await menus.collect("app", remaining, context=ROOT, path=["File"])
        self.assertEqual(observed.scope_kind, "applicationMenu")
        self.assertEqual(observed.target, replace(ROOT, surface="menu-surface", node="file"))
        begins = [target for kind, target in port.events if kind == "begin_read"]
        self.assertEqual(begins[0].surface, None)
        self.assertEqual(begins[1], replace(ROOT, surface="menu-surface", node=None))
        self.assertEqual([kind for kind, _ in port.events].count("directory"), 1)
        self.assertEqual(observed.parts[0].directory["roots"][0]["node"], "bar")

    async def test_injected_fact_collector_keeps_its_original_signature(self):
        port = MenuPort(two_bars())
        native = NativeReader(port)
        calls = []

        async def collect(read, *, node, fields, max_nodes, max_depth, scope_kind):
            calls.append((node, max_nodes, scope_kind))
            return await native.collect(
                read, node=node, fields=fields, max_nodes=max_nodes, max_depth=max_depth, scope_kind=scope_kind
            )

        menus = NativeMenus(NativeDirectory(port), SimpleNamespace(port=port, collect=collect))
        with port.calls.claim(30000) as remaining:
            result = await menus.collect("app", remaining, context=ROOT, path=["File"], max_nodes=14)
        self.assertEqual(calls, [("file", 7, "window"), ("file-second", 7, "window")])
        self.assertEqual(result.target, ROOT)
        self.assertEqual(len(result.parts), 2)

    async def test_injected_directory_collector_keeps_its_original_signature(self):
        port = ApplicationMenuPort()
        directory = NativeDirectory(port)
        original, calls = directory.collect, []

        async def collect(read, query):
            calls.append(query["kind"])
            return await original(read, query)

        directory.collect = collect
        with port.calls.claim(30000) as remaining:
            result = await NativeMenus(directory, NativeReader(port)).collect("app", remaining, path=["File"])
        self.assertEqual(calls, ["roots"])
        self.assertEqual(result.target.instance, "menu-instance")

    def test_source_rendering_preserves_single_and_multiple_part_calls(self):
        for count, gaps in ((1, ()), (1, ("unread source",)), (2, ()), (0, ())):
            calls = []
            parts = []
            for index in range(count):

                def render(*, compact=False, index=index, calls=calls):
                    calls.append((index, compact))
                    return dict(text="source-" + str(index))

                parts.append(MenuObservation(ROOT, SimpleNamespace(render=render), None, ()))
            result = MenuRead(ROOT, tuple(parts), gaps).render(compact=True)
            self.assertEqual(calls, [(index, True) for index in range(count)])
            if count == 1 and not gaps:
                self.assertEqual(result, "source-0")
            else:
                self.assertIn(f"Menu sources: {count} observed.", result)
                for index in range(count):
                    self.assertIn(f"Menu source {index + 1}:\nsource-{index}", result)
