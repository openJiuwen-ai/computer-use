# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
from dataclasses import replace

from computer_use.interaction.read.acquisition.menus.query import MenuQuery, WindowMenus
from computer_use.native.contracts import Binding


def test_menu_query_defaults_to_shallow_nodes_but_preserves_explicit_depth():
    query = MenuQuery.__new__(MenuQuery)
    query.platform = "windows"
    query.max_nodes = 10
    query.max_depth = 40
    query.nodes = {}
    query.remaining = lambda: 500
    query._remember_nodes = query.nodes.update
    calls = []

    async def collect(token, available, depth):
        calls.append((token, available, depth))
        return {token: {"token": token}}

    query._collect_nodes = collect

    async def read():
        assert await query._node("implicit") == {"token": "implicit"}
        assert await query._node("explicit", depth=3) == {"token": "explicit"}
        assert await query._node("explicit", depth=8) == {"token": "explicit"}

    asyncio.run(read())
    assert calls == [("implicit", 10, 0), ("explicit", 9, 3)]


def test_working_window_menu_discovery_skips_oversized_and_unavailable_branches():
    class Graph(WindowMenus):
        def __init__(self):
            self.root = Binding("driver", "instance", "window", "surface", "root")
            self.platform = "macos"
            self.max_depth = 4
            self.max_nodes = 12
            self.nodes = {}
            self.relations = {"children": "AXChildren"}
            self.edges = {
                ("oversized", "AXChildren"): {
                    "state": "partial",
                    "certainty": "confirmed",
                    "cursor": "next",
                    "total": 1000,
                },
            }
            self.calls = []

        def remaining(self):
            return 500

        async def _node(self, token, *, depth=None):
            self.calls.append(("node", token, depth))
            if token == "unavailable":
                self.fail("stale_target", "The child is gone.")
            self.nodes[token] = {}

        def _check_parent(self, token, ancestors):
            self.calls.append(("parent", token, ancestors))

        async def _field(self, token, semantic):
            self.calls.append((semantic, token))
            return {
                "root": "AXWindow",
                "oversized": "AXGroup",
                "other-window": "AXWindow",
                "menu": "AXMenuBar",
            }[token]

        async def _relation(self, token, kind):
            self.calls.append((kind, token))
            assert token == "root"
            return ("oversized", "unavailable", "other-window", "menu")

    graph = Graph()
    result = asyncio.run(graph.collect())

    assert result.targets == (replace(graph.root, node="menu"),)
    assert result.gaps == (
        "A window branch has more children than the menu-discovery budget; its subtree is unread.",
        "A window branch became unavailable during discovery.",
    )
    assert graph.calls == [
        ("node", "root", 0),
        ("role", "root"),
        ("children", "root"),
        ("node", "oversized", 0),
        ("parent", "oversized", ("root",)),
        ("role", "oversized"),
        ("node", "unavailable", 0),
        ("node", "other-window", 0),
        ("parent", "other-window", ("root",)),
        ("role", "other-window"),
        ("node", "menu", 0),
        ("parent", "menu", ("root",)),
        ("role", "menu"),
    ]
