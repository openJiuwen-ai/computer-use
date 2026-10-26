"""Discover window menu sources."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.menus.model import (
    QUERY,
    DiscoveredWindowMenus,
    MenuLease,
)
from computer_use.interaction.read.acquisition.menus.query import WindowMenus
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Awaitable, Callable

    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control


def create(*, port: NativePort) -> Callable[[MenuLease, Control], Awaitable[DiscoveredWindowMenus]]:
    async def discover(lease: MenuLease, control: Control) -> DiscoveredWindowMenus:
        """Read bounded native window-menu roots and pair the discovery result with its lease."""
        request, read = lease.request, lease.read
        sources = await WindowMenus(
            port, read.token, request.context, read.remaining, max_nodes=request.max_nodes, max_depth=request.max_depth
        ).collect()
        return DiscoveredWindowMenus(lease, sources)

    return discover


stage = Pass.factory(
    name="discover_window_menu_sources",
    factory=create,
    contract=Contract(MenuLease, DiscoveredWindowMenus, QUERY),
)
