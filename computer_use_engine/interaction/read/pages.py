# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public continuation aliases for observed native relationship pages.

Aliases grant no input authority. A page keeps its source binding, property
and native prefix cursor; neither a new query nor a numeric offset can retarget
it. The driver still checks that the original prefix is current when read.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from computer_use.errors import ComputerUseError
from computer_use.interaction.read.presentation.content import (
    DisplayText,
    join,
    json_value,
    render_text,
    template,
    text,
)
from computer_use.native.contracts import Binding

if TYPE_CHECKING:
    from typing import Iterable

    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot


@dataclass(frozen=True)
class ContentPage:
    """A retained native relationship cursor and the consumed prefix length.

    Attributes:
        target: Native identity of the element, window, or application being observed.
        property: Exact native relationship/property name to read or account for.
        cursor: Opaque native continuation token retained from this relationship read.
        offset: Starting position in the source's declared text or relation units.
        total: Total native relation entries reported when this cursor was captured.
    """

    target: Binding
    property: str
    cursor: str
    offset: int
    total: int


class ContentPages:
    """Keep bounded continuation aliases for relationships in observed element trees."""

    def __init__(self, capacity: int = 512) -> None:
        self.capacity = capacity
        self._next = 1
        self._pages = OrderedDict()
        self._aliases = {}

    def remember(self, target: Binding, tree: SemanticSnapshot) -> None:
        """Retain continuation cursors from confirmed relationships in this snapshot.

        Args:
            target: Binding that supplies the snapshot's application and window scope.
            tree: Snapshot containing the native relation cursors to retain.
        """
        for relation in tree.facts["relations"]:
            if relation["certainty"] != "confirmed" or "cursor" not in relation:
                continue
            selector = (
                tree.collection.relation_fields.get((relation["source"], relation["recordKey"]))
                if tree.collection is not None
                else None
            )
            if selector is None:
                continue
            page = ContentPage(
                replace(target, node=relation["source"], capture=None),
                selector,
                relation["cursor"],
                relation.get("offset", 0) + len(relation["targets"]),
                relation["total"],
            )
            if page in self._aliases:
                self._pages.move_to_end(self._aliases[page])
                continue
            alias = "p" + str(self._next)
            self._next += 1
            self._aliases[page] = alias
            self._pages[alias] = page
            while len(self._pages) > self.capacity:
                _, expired = self._pages.popitem(last=False)
                del self._aliases[expired]

    def resolve(self, alias: str, target: Binding) -> Record:
        """Resolve a retained page alias within its original element binding.

        Args:
            alias: Public continuation alias printed by an earlier observation.
            target: Element binding against which the retained page is checked.

        Returns:
            Native relationship name, continuation cursor, and page-size limit.
        """
        page = self._pages.get(alias)
        if page is None or page.target != replace(target, capture=None):
            raise ComputerUseError(
                dict(
                    code="snapshot_expired" if page is None else "invalid_request",
                    effect="none",
                    target="element content",
                    detail="This continuation is expired or belongs to a different element or window.",
                    next="read the intended element again and use a continuation printed in that result",
                )
            )
        return dict(property=page.property, cursor=page.cursor, limit=128)

    def hints(
        self,
        target: Binding,
        tree: SemanticSnapshot,
        *,
        compact: bool = False,
        inline_children: Iterable[str] = (),
        public_api: bool = False,
        children_only: bool = False,
    ) -> str:
        """Format continuation commands for the snapshot's paged native relationships.

        Args:
            target: Binding that supplies the snapshot's application and window scope.
            tree: Snapshot whose continuation aliases have been retained.
            compact: Suppress child-page hints already represented by inline entries.
            inline_children: References with an inline child-page entry in the render.
            public_api: Emit commands using the public integer-reference API.
            children_only: Limit hints to native relationships normalized as children.

        Returns:
            Continuation instructions, or an empty string when no paged relation applies.
        """
        return render_text(
            self.plan_hints(
                target,
                tree,
                compact=compact,
                inline_children=inline_children,
                public_api=public_api,
                children_only=children_only,
            )
        )

    def plan_hints(
        self,
        target: Binding,
        tree: SemanticSnapshot,
        *,
        compact: bool = False,
        inline_children: Iterable[str] = (),
        public_api: bool = False,
        children_only: bool = False,
        concise: bool = False,
    ) -> DisplayText:
        """Capture continuation aliases and commands as structured fields for final rendering."""
        refs = {node: ref for ref, node in tree.refs.items()}
        lines = []
        inline_children = set(inline_children) if compact else set()
        for relation in tree.facts["relations"]:
            if relation["certainty"] != "confirmed" or "cursor" not in relation:
                continue
            selector = (
                tree.collection.relation_fields.get((relation["source"], relation["recordKey"]))
                if tree.collection is not None
                else None
            )
            if selector is None:
                continue
            page = ContentPage(
                replace(target, node=relation["source"], capture=None),
                selector,
                relation["cursor"],
                relation.get("offset", 0) + len(relation["targets"]),
                relation["total"],
            )
            alias = self._aliases.get(page)
            ref = refs[relation["source"]]
            label = relation["kind"]
            if children_only and label != "children":
                continue
            if not public_api and ref in inline_children and label == "children":
                # A successfully rendered branch already supplies an offset
                # entry, or a first-page menu entry with no consumed prefix.
                continue
            if alias is not None:
                if public_api:
                    if concise and compact:
                        lines.append(
                            template(
                                "{1} [{0}]: cursor={2} (live order)",
                                ref,
                                "Children" if label == "children" else label,
                                json_value(alias),
                            )
                        )
                    else:
                        lines.append(
                            template(
                                "{0} {1}, continuation: node = await app.element({0}); await node.read(cursor={2})",
                                ref,
                                label,
                                json_value(alias),
                            )
                        )
                    continue
                lines.append(
                    template(
                        '{0} {1}, next offset {2} of {3}: app.element({4}).read({{"cursor": {5}}})',
                        ref,
                        label,
                        page.offset,
                        page.total,
                        json_value(ref),
                        json_value(alias),
                    )
                )
            else:
                lines.append(template("{0} {1}: continuation expired; read this element again.", ref, label))
        if concise and compact and public_api:
            return text("") if not lines else join(("\n", join(lines)), separator="")
        return (
            text("") if not lines else join(("\nContinue reading (live order; changed prefixes expire):", join(lines)))
        )


if TYPE_CHECKING:
    pass
if TYPE_CHECKING:
    from computer_use.protocol.records import Record
