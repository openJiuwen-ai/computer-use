"""Read native menus inside their actual application or working-window scope.

No menu opening, focus, command validation or input occurs here. Application
roots require a declared driver capability; otherwise discovery stays inside
one already selected working window.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from computer_use.errors import ComputerUseError
from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
from computer_use.native.contracts import Binding
from computer_use.native.query.graph import NativeGraph
from computer_use.native.query.path import TargetQuery
from computer_use.native.semantics import UNKNOWN, role_name

if TYPE_CHECKING:
    from typing import NoReturn, Sequence

    from computer_use.interaction.read.acquisition.reader import NativeReader
    from computer_use.interaction.records import Remaining
    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import FactNode
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts


def menu_failure(code: str, detail: str) -> ComputerUseError:
    return ComputerUseError(
        dict(
            code=code,
            effect="none",
            target="menu observation",
            detail=detail,
            next=(
                "read the parent menu or working window and use an exact observed title or "
                "element reference; no menu was "
                "opened"
            ),
        )
    )


class MenuQuery(TargetQuery):
    "Resolve menu items through native target-query semantics."

    def fail(self, code: str, detail: str) -> NoReturn:
        raise menu_failure(code, detail)

    async def _node(self, token: str, *, depth: int | None = None) -> FactNode:
        # Query only the facts needed to establish this level. Unopened deep
        # submenus must not consume the budget before sibling uniqueness is known.
        return await super()._node(token, depth=0 if depth is None else depth)

    async def _menu_children(self, token: str, ancestors: tuple[str, ...]) -> list[str]:
        """Menu children.

        Args:
            token: Opaque native object token owned by the current driver session.
        """
        children = list(dict.fromkeys(await self._relation(token, "children")))
        for child in children:
            if child in ancestors:
                self.fail("observation_incomplete", "Menu containment is cyclic.")
            await self._node(child)
            self._check_parent(child, ancestors)
        return children

    async def _role(self, token: str) -> str | object:
        role = await self._field(token, "role")
        if role is UNKNOWN or not isinstance(role, str) or not role:
            self.fail("observation_incomplete", "A menu element has an unread native role.")
        return role_name(self.platform, role)

    async def resolve_menu(self, path: Sequence[str], *, _control: Control | None = None) -> Binding:
        from computer_use.interaction.read.acquisition.menus.model import MenuPathRequest
        from computer_use.interaction.read.acquisition.menus.pipeline import MenuPathPipeline

        return await MenuPathPipeline().arun(MenuPathRequest(self, path), _control)


@dataclass(frozen=True)
class MenuSources:
    """Discovered native menu roots with unresolved discovery notes.

    Attributes:
        targets: Native identities of the discovered window-menu roots.
        gaps: Explicit collection failures or omissions retained in the observation.
    """

    targets: tuple[Binding, ...]
    gaps: tuple[str, ...] = ()


class WindowMenus(NativeGraph):
    "Discover menu roots in a bounded native graph read."

    def __init__(
        self,
        port: NativePort,
        operation: str,
        root: Binding | None,
        remaining: Remaining,
        *,
        max_nodes: int = 1000,
        max_depth: int | None = 40,
    ) -> None:
        super().__init__(port, operation, root, remaining, fields=("role",), max_nodes=max_nodes, max_depth=max_depth)

    def fail(self, code: str, detail: str) -> NoReturn:
        raise menu_failure(code, detail)

    async def collect(self) -> MenuSources:
        if self.root.node is None or self.root.capture is not None:
            self.fail("observation_incomplete", "The working window has no accessible root for menu discovery.")
        pending, visited, candidates, gaps = deque([(self.root.node, (), 0)]), set(), [], []
        while pending:
            self.remaining()
            token, ancestors, depth = pending.popleft()
            try:
                if token in ancestors or depth > self.max_depth:
                    self.fail(
                        "observation_incomplete", "Window-menu containment is cyclic or exceeds its depth budget."
                    )
                await self._node(token, depth=0)
                if ancestors:
                    self._check_parent(token, ancestors)
                if token in visited:
                    continue
                visited.add(token)
                raw = await self._field(token, "role")
                if raw is UNKNOWN or not isinstance(raw, str) or not raw:
                    self.fail("observation_incomplete", "A window branch has an unread native role.")
                role = role_name(self.platform, raw)
                if token != self.root.node and role in ("window", "sheet"):
                    continue
                if role == "menuBar":
                    candidates.append(replace(self.root, node=token))
                    continue
                edge = self.edges.get((token, self.relations.get("children")), {})
                # Virtual containers can contain millions of children. Do not
                # let an unfinishable branch consume all other menu discovery.
                # It remains explicitly unread, not an inferred menu-free area.
                matches_edge = (
                    True
                    if (
                        edge.get("state") == "partial"
                        and edge.get("certainty") == "confirmed"
                        and edge.get("offset", 0) == 0
                        and edge.get("cursor") is not None
                        and edge.get("total", 0) > self.max_nodes - len(self.nodes)
                    )
                    else False
                )
                if matches_edge:
                    gaps.append(
                        "A window branch has more children than the menu-discovery budget; its subtree is unread."
                    )
                    continue
                else:
                    children = await self._relation(token, "children")
                pending.extend((child, (*ancestors, token), depth + 1) for child in children)
            except ComputerUseError as error:
                # A disappearing descendant cannot invalidate other confirmed
                # menu branches. The working root is rechecked after collection;
                # an expired root/session or a selected menu still fails closed.
                unavailable_child = error.code == "stale_target" and token != self.root.node
                if error.effect != "none" or not (error.code == "observation_incomplete" or unavailable_child):
                    raise
                gaps.append(
                    "A window branch became unavailable during discovery." if unavailable_child else error.detail
                )
                if len(self.nodes) >= self.max_nodes:
                    break
        if not candidates:
            self.fail(
                "observation_incomplete",
                "; ".join(dict.fromkeys(gaps)) or "The working window exposes no readable native menu bar.",
            )
        return MenuSources(tuple(candidates), tuple(dict.fromkeys(gaps)))


@dataclass(frozen=True)
class MenuObservation:
    """One native menu snapshot with its directory evidence and resolved path.

    Attributes:
        target: Native identity of the element, window, or application being observed.
        tree: Semantic snapshot with retained native facts and display-reference bindings.
        directory: Observed application/window catalogue, including incomplete relationships.
        path: Ordered semantic lookup/menu components within the declared target scope.
    """

    target: Binding
    tree: SemanticSnapshot
    directory: DirectoryFacts | None
    path: tuple[str, ...]


@dataclass(frozen=True)
class MenuRead:
    """Separately captured menu sources belonging to a common query scope.

    A read may describe several menus; it does not choose an action target or merge objects just
    because their titles or commands happen to be equal.

    Attributes:
        target: Common query scope for the separately collected menu parts.
        parts: Each menu source's native snapshot, directory evidence, and resolved path.
        discovery_gaps: Incomplete menu-discovery notes associated with the collected sources.
    """

    target: Binding
    parts: tuple[MenuObservation, ...]
    discovery_gaps: tuple[str, ...] = ()

    @property
    def scope_kind(self) -> str | None:
        return self.parts[0].tree.scope_kind

    @property
    def observations(self) -> tuple[tuple[Binding, SemanticSnapshot], ...]:
        return tuple((part.target, part.tree) for part in self.parts)

    def render(self, *, compact: bool = False, _control: Control | None = None) -> str:
        from computer_use.interaction.read.delivery.menu.model import MenuSourceRenderRequest
        from computer_use.interaction.read.delivery.menu.pipeline import MenuSourcesPresentationPipeline

        return MenuSourcesPresentationPipeline().run(MenuSourceRenderRequest(self, compact), _control).text


class NativeMenus:
    "Collect application or window menus and their native source identities."

    def __init__(self, directory: NativeDirectory, reader: NativeReader) -> None:
        self.directory, self.reader = directory, reader
        self.port = reader.port

    async def collect(
        self,
        application: str,
        remaining: Remaining,
        *,
        context: Binding | None = None,
        path: Sequence[str] = (),
        summary: bool = False,
        source_directory: DirectoryFacts | None = None,
        fields: list[str] | None = None,
        max_nodes: int = 1000,
        max_depth: int | None = 40,
        _control: Control | None = None,
    ) -> MenuRead:
        """Collect.

        Args:
            remaining: Callback returning milliseconds left on the original call; may raise on
                expiration.
            path: Ordered semantic lookup/menu components within the declared target scope.
            summary: Whether to collect a bounded menu summary instead of the deeper requested subtree.
            source_directory: Catalogue captured with the source facts, before scope rechecking.
            fields: Requested or accounted native fields; unknown fields remain explicit facts.
            max_nodes: Maximum number of native objects this collection may read.
            max_depth: Maximum native relation depth; None uses the existing platform default.
            _control: Optional inherited control metadata; a subpipeline does not start a new
                call.
        """
        from computer_use.interaction.control import scope_control
        from computer_use.interaction.read.acquisition.menus.model import MenuCollectionRequest
        from computer_use.interaction.read.acquisition.menus.pipeline import MenuCollectionPipeline
        from computer_use.pipeline import Control

        request = MenuCollectionRequest(
            application, context, path, summary, source_directory, fields, max_nodes, max_depth
        )
        control = scope_control(self.port, remaining, _control or Control())
        return await MenuCollectionPipeline(self).arun(request, control)


if TYPE_CHECKING:
    from computer_use.interaction.read.acquisition.directory.service import NativeDirectory
