"""Establish the selected menu's native ancestry and submenu state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.native.query.graph import UNKNOWN, NativeGraph

if TYPE_CHECKING:
    from computer_use.interaction.records import Remaining
    from computer_use.native.contracts import Binding
    from computer_use.native.port import NativePort
    from computer_use.protocol.records import FactNode


class MenuContextQuery(NativeGraph):
    """Collect the selected menu's native ancestry and submenu evidence."""

    def __init__(self, port: NativePort, operation: str, target: Binding, remaining: Remaining) -> None:
        super().__init__(port, operation, target, remaining, fields=("role",), max_nodes=512, max_depth=16)

    async def _node(self, token: str, *, depth: int | None = None) -> FactNode:
        return await super()._node(token, depth=0)

    async def resolve(
        self, anchors: tuple[str, ...] = (), *, inspect_submenu: bool = False
    ) -> tuple[tuple[str, ...], bool]:
        """Trace the selected menu to its menu bar and validate retained ancestor anchors.

        Args:
            anchors: Ancestor tokens established by earlier scoped resolution.
            inspect_submenu: Whether to inspect children for the open-menu operation.

        Returns:
            Ancestor tokens in outer-to-inner order and whether one submenu was found.
        """
        chain = [self.root.node]
        for _ in range(16):
            current = chain[-1]
            role = await self._field(current, "role")
            if role == "AXMenuBar":
                break
            if role not in ("AXMenuItem", "AXMenuBarItem", "AXMenu"):
                self.fail("observation_incomplete", "The selected menu has no complete native menu-bar ancestry.")
            parents = await self._relation(current, "parent")
            if len(parents) != 1 or parents[0] in chain:
                self.fail("observation_incomplete", "The selected menu ancestry is missing, ambiguous or cyclic.")
            if current not in await self._relation(parents[0], "children"):
                self.fail("stale_target", "The observed menu is no longer a child of its original parent.")
            chain.append(parents[0])
        else:
            self.fail("observation_incomplete", "Menu ancestry exceeds its preparation budget.")
        within = tuple(reversed(chain[1:]))
        if not set(anchors).issubset(within):
            self.fail("stale_target", "The menu left an explicitly selected ancestor.")
        return within, await self.has_submenu() if inspect_submenu else False

    async def has_submenu(self) -> bool:
        """Report whether the selected menu has exactly one native submenu child."""
        roles = [await self._field(child, "role") for child in await self._relation(self.root.node, "children")]
        if any(role is UNKNOWN for role in roles):
            self.fail("observation_incomplete", "The menu branch has unread child roles.")
        return roles.count("AXMenu") == 1
