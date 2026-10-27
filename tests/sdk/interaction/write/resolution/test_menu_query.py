"""Menu preparation uses an observed native path, never a same-title lookup."""

import unittest
from dataclasses import replace

from observation_fixture import facts_fixture
from sdk.native.query.test_target_query import ROOT, GraphPort, node

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.interaction.write.resolution.menu import MenuContextQuery
from computer_use.native.contracts import Intent


def menus():
    return dict(
        bar=node("AXMenuBar", children=["file"]),
        file=node("AXMenuBarItem", "File", children=["menu"]),
        menu=node("AXMenu", children=["leaf", "branch"]),
        leaf=node("AXMenuItem", "Run"),
        branch=node("AXMenuItem", "Advanced", children=["submenu"]),
        submenu=node("AXMenu", children=["nested"]),
        nested=node("AXMenuItem", "Run"),
    )


class MenuPort(GraphPort):
    capabilities = frozenset({"nativeMenuActions"})

    async def query_nodes(self, operation, target, fields, max_nodes, max_depth, timeout_ms):
        result = await super().query_nodes(operation, target, fields, max_nodes, max_depth, timeout_ms)
        parents = [ref for ref, source in self.graph.items() if target.node in source["children"]]
        edge = self.edges.get((target.node, "AXParent"), dict(targets=parents, state="complete", certainty="confirmed"))
        result["requestedRelations"].append("AXParent")
        result["relations"].append(dict(source=target.node, property="AXParent", **edge))
        result["unread"] = sorted(set(result["unread"]) | (set(edge["targets"]) - {target.node}))
        return facts_fixture(result)

    async def inspect(self, operation, target, timeout_ms, *, within=()):
        facts = await super().inspect(operation, target, timeout_ms, within=within)
        return replace(facts, native_role=self.graph[target.node]["values"]["AXRole"])


class MenuQueryTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def query(port, ref="nested"):
        return MenuContextQuery(port, "op", replace(ROOT, node=ref), lambda: 1000)

    async def refused(self, port, code, *, ref="nested", anchors=()):
        with self.assertRaises(ComputerUseError) as caught:
            await self.query(port, ref).resolve(anchors)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.effect, "none")
        self.assertFalse(any(call[0] == "execute" for call in port.events))

    async def test_same_named_leaves_keep_their_distinct_native_ancestry(self):
        port = MenuPort(menus())
        path, opens = await self.query(port).resolve(("menu",))
        self.assertEqual(path, ("bar", "file", "menu", "branch", "submenu"))
        self.assertFalse(opens)
        self.assertNotIn("leaf", [e[1].node for e in port.events if e[0] == "nodes"])
        await Engine(port).execute(Intent("click"), replace(ROOT, node="nested"))
        inspections = [e for e in port.events if e[0] == "inspect"]
        self.assertEqual(inspections[-1][2], path)
        self.assertEqual([e[1].node for e in port.events if e[0] == "execute"], ["nested"])

    async def test_submenu_evidence_is_required_before_press_means_open(self):
        port = MenuPort(menus())
        self.assertTrue((await self.query(port, "branch").resolve(inspect_submenu=True))[1])
        self.assertFalse((await self.query(port, "leaf").resolve(inspect_submenu=True))[1])
        with self.assertRaises(ComputerUseError):
            await Engine(port).execute(Intent("openMenu"), replace(ROOT, node="leaf"))
        self.assertFalse(any(call[0] == "execute" for call in port.events))
        await Engine(port).execute(Intent("openMenu"), replace(ROOT, node="branch"))
        self.assertEqual([e[1].node for e in port.events if e[0] == "execute"], ["branch"])

    async def test_conflicting_or_missing_ancestry_cannot_prepare_a_different_menu(self):
        for parents in ([], ["submenu", "menu"], ["nested"]):
            port = MenuPort(menus())
            port.edges[("nested", "AXParent")] = dict(targets=parents, state="complete", certainty="confirmed")
            with self.subTest(parents=parents):
                await self.refused(port, "observation_incomplete")
        port = MenuPort(menus())
        port.edges[("nested", "AXParent")] = dict(targets=["menu"], state="complete", certainty="confirmed")
        await self.refused(port, "stale_target")
        await self.refused(MenuPort(menus()), "stale_target", anchors=("leaf",))

    async def test_partial_forward_relation_cannot_certify_the_path(self):
        port = MenuPort(menus())
        port.edges[("submenu", "AXChildren")] = dict(targets=["nested"], state="partial", certainty="confirmed")
        await self.refused(port, "observation_incomplete")

    async def test_unread_submenu_role_does_not_authorize_a_leaf(self):
        port = MenuPort(menus())
        port.graph["submenu"]["statuses"]["AXRole"] = "error"
        with self.assertRaises(ComputerUseError) as caught:
            await self.query(port, "branch").resolve(inspect_submenu=True)
        self.assertEqual(caught.exception.code, "observation_incomplete")

    async def test_chain_without_menu_bar_is_bounded(self):
        graph = {f"n{i}": node("AXMenu", children=[f"n{i + 1}"]) for i in range(18)}
        graph["n18"] = node("AXMenuItem", "Run")
        port = MenuPort(graph)
        await self.refused(port, "observation_incomplete", ref="n18")
        self.assertLessEqual(len([e for e in port.events if e[0] == "nodes"]), 17)


class LocalMenuPort(MenuPort):
    async def inspect(self, operation, target, timeout_ms, *, within=()):
        return replace(await super().inspect(operation, target, timeout_ms, within=within), application_menu=False)


class LocalMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_menu_click_and_explicit_action_need_no_menu_bar(self):
        for intent in (Intent("click"), Intent("action", {"action": "AXPress"})):
            port = LocalMenuPort(
                dict(
                    root=node("AXWindow", children=["popup"]),
                    popup=node("AXGroup", children=["leaf"]),
                    leaf=node("AXMenuItem", "Run"),
                )
            )
            await Engine(port).execute(intent, replace(ROOT, node="leaf"))
            self.assertFalse(any(e[0] == "nodes" for e in port.events))
            self.assertEqual(len([e for e in port.events if e[0] == "execute"]), 1)

    async def test_local_open_menu_still_requires_submenu_evidence(self):
        port = LocalMenuPort(
            dict(
                branch=node("AXMenuItem", children=["submenu"]),
                submenu=node("AXMenu", children=["leaf"]),
                leaf=node("AXMenuItem"),
            )
        )
        await Engine(port).execute(Intent("openMenu"), replace(ROOT, node="branch"))
        self.assertEqual(len([e for e in port.events if e[0] == "execute"]), 1)
        with self.assertRaises(ComputerUseError):
            await Engine(port).execute(Intent("openMenu"), replace(ROOT, node="leaf"))
