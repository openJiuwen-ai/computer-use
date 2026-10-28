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
        layout: Unnumbered group keys and headings needed to interpret this element.
    """

    ref: str
    parent: str | None
    depth: int
    text: str
    content: str
    hints: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    partial: bool = False
    layout: tuple[tuple[str, str], ...] = ()


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
class RenderedLiteral:
    """Keep a contextual full-view note and its independent incremental form.

    A full note may refer to a table or other structure present in that complete
    observation. Its incremental form retains the explicit information needed
    when unchanged structure is omitted from a diff.

    Attributes:
        text: Complete-view wording, interpreted within the full document.
        incremental_text: Self-contained wording used with incremental trees.
    """

    text: str
    incremental_text: str


@dataclass(frozen=True)
class RenderedDocument:
    """Ordered text sections and trees used by full and incremental delivery.

    Attributes:
        parts: Text fragments, mode-specific notes, and structured trees in output order.
        context: Scope, document, and modal relationships that require a fresh full view
            when they change. Observation timestamps and display-only titles stay outside it.
    """

    parts: tuple[str | RenderedLiteral | RenderedSection, ...]
    context: tuple[Hashable, ...] = ()

    @property
    def text(self) -> str:
        """Return the complete presentation without changing its layout."""
        return "".join(part if isinstance(part, str) else part.text for part in self.parts)

    @classmethod
    def join(cls, parts: Iterable[str | RenderedDocument], separator: str = "\n") -> RenderedDocument:
        """Compose text and documents while retaining every structured tree."""
        result: list[str | RenderedLiteral | RenderedSection] = []
        for index, part in enumerate(parts):
            if index:
                result.append(separator)
            result.extend(part.parts if isinstance(part, cls) else (part,))
        return cls(tuple(result))

    def indented(self) -> RenderedDocument:
        """Indent a related-root presentation without changing its parent relationships."""

        def indent(text: str) -> str:
            return "\n".join("\t" + line for line in text.splitlines()) if text else ""

        def indent_part(
            part: str | RenderedLiteral | RenderedSection,
        ) -> str | RenderedLiteral | RenderedSection:
            if isinstance(part, str):
                return indent(part)
            if isinstance(part, RenderedLiteral):
                return replace(part, text=indent(part.text), incremental_text=indent(part.incremental_text))
            return replace(
                part,
                text=indent(part.text),
                prefix=indent(part.prefix),
                suffix=indent(part.suffix),
                nodes=tuple(
                    replace(
                        node,
                        depth=node.depth + 1,
                        text=indent(node.text),
                        content=indent(node.content),
                        hints=tuple(indent(hint) for hint in node.hints),
                        layout=tuple((key, indent(text)) for key, text in node.layout),
                    )
                    for node in part.nodes
                ),
            )

        return replace(self, parts=tuple(indent_part(part) for part in self.parts))

    def scoped(self, scope: tuple[Hashable, ...]) -> RenderedDocument:
        """Attach the native scope used for comparison; execution bindings stay separate."""
        return replace(
            self,
            parts=tuple(
                replace(part, scope=scope) if isinstance(part, RenderedSection) else part for part in self.parts
            ),
        )
