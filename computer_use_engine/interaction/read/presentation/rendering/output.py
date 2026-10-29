# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Terminal renderer output; these formatted records never enter structural diff planning."""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class RenderedNode:
    """One displayed element before reversible line packing.

    Attributes:
        ref: Display reference; native bindings remain in the observation owner.
        parent: Displayed parent reference in this tree, or None for a root.
        depth: Display indentation, including any related-root indentation.
        text: Element line and its own multiline values and annotations.
        content: First line without state flags, used to retain descendant context.
        hints: Coverage and continuation lines repeated even when the element is unchanged.
        related: Label and container references needed to understand this element.
        partial: Whether this element reports incomplete children.
    """

    ref: str
    parent: str | None
    depth: int
    text: str
    content: str
    hints: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    partial: bool = False


@dataclass(frozen=True)
class RenderedSection:
    """A rendered tree with its unpacked elements and surrounding summaries.

    Attributes:
        text: Complete rendered text, including reversible line packing.
        nodes: Elements in display traversal order, with explicit parent relationships.
        prefix: Tree-local heading and interaction information before the elements.
        suffix: Selection, focus, and other summaries following the elements.
        scope: Exact native scope identity used to keep comparisons within the same tree.
    """

    text: str
    nodes: tuple[RenderedNode, ...]
    prefix: str = ""
    suffix: str = ""
    scope: tuple[Hashable, ...] = ()


@dataclass(frozen=True)
class RenderedDocument:
    """Ordered text sections and trees used by full and incremental delivery.

    Attributes:
        parts: Literal text fragments and structured trees in their exact output order.
        context: Scope, document, and modal relationships that require a fresh full view
            when they change. Observation timestamps and display-only titles stay outside it.
    """

    parts: tuple[str | RenderedSection, ...]
    context: tuple[Hashable, ...] = ()

    @property
    def text(self) -> str:
        """Return the complete presentation without changing its layout."""
        return "".join(part if isinstance(part, str) else part.text for part in self.parts)

    @classmethod
    def join(cls, parts: Iterable[str | RenderedDocument], separator: str = "\n") -> RenderedDocument:
        """Compose text and documents while retaining every structured tree."""
        result: list[str | RenderedSection] = []
        for index, part in enumerate(parts):
            if index:
                result.append(separator)
            result.extend(part.parts if isinstance(part, cls) else (part,))
        return cls(tuple(result))

    def indented(self) -> RenderedDocument:
        """Indent a related-root presentation without changing its parent relationships."""

        def indent(text: str) -> str:
            return "\n".join("\t" + line for line in text.splitlines()) if text else ""

        parts = []
        for part in self.parts:
            if isinstance(part, str):
                parts.append(indent(part))
                continue
            text, prefix, suffix = indent(part.text), indent(part.prefix), indent(part.suffix)
            nodes = []
            for node in part.nodes:
                nodes.append(
                    replace(
                        node,
                        depth=node.depth + 1,
                        text=indent(node.text),
                        content=indent(node.content),
                        hints=tuple(indent(hint) for hint in node.hints),
                    )
                )
            parts.append(replace(part, text=text, prefix=prefix, suffix=suffix, nodes=tuple(nodes)))
        return replace(self, parts=tuple(parts))

    def scoped(self, scope: tuple[Hashable, ...]) -> RenderedDocument:
        """Attach the native scope used for comparison; execution bindings stay separate."""
        return replace(
            self,
            parts=tuple(
                replace(part, scope=scope) if isinstance(part, RenderedSection) else part for part in self.parts
            ),
        )
