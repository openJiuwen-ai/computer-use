# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Generate full or incremental text at the final presentation boundary."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.content import render_text
from computer_use.interaction.read.presentation.document import DisplayDocument, DisplayTree
from computer_use.interaction.read.presentation.rendering.document.details import detail_footer, separate_details
from computer_use.interaction.read.presentation.rendering.document.headers import full_header_text
from computer_use.interaction.read.presentation.rendering.output import (
    RenderedDocument,
    RenderedLiteral,
    RenderedSection,
)
from computer_use.protocol.primitives import trim_start

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.delta import DeltaPlan, TreeDelta
    from computer_use.interaction.read.presentation.records import RenderedTree


def _header(text: str, mode: str) -> str:
    title = f"Tree: {mode}"
    if re.search(r"^Tree: ", text, re.MULTILINE):
        return re.sub(r"^Tree: .*$", lambda _: title, text, count=1, flags=re.MULTILINE)
    lines = text.split("\n") if text else []
    lines.insert(min(1, len(lines)), title)
    return "\n".join(lines)


def _section_delta(section: RenderedSection, delta: TreeDelta, missing: tuple[str, ...], plan: DeltaPlan) -> str:
    unobserved = [ref for ref in missing if ref in plan.not_observed or ref in plan.retained]
    removed = [ref for ref in missing if ref not in unobserved]
    lines = [section.prefix] if section.prefix else []
    if removed:
        lines.append("Removed element IDs: " + ", ".join(removed))
    if unobserved:
        lines.append("Not observed in this read: " + ", ".join(unobserved))
    layout: tuple[tuple[str, str], ...] = ()
    for node in section.nodes:
        if section.refresh or node.ref in delta.changes or node.ref in delta.context:
            common = 0
            for previous, current in zip(layout, node.layout):
                if previous != current:
                    break
                common += 1
            lines.extend(text for _, text in node.layout[common:])
            layout = node.layout
        if section.refresh or node.ref in delta.changes:
            lines.append(delta.changes.get(node.ref, "~") + node.text)
        elif node.ref in delta.context:
            lines.append(" " + (node.text if node.ref in delta.expanded else node.text.split("\n", 1)[0]))
        if not section.refresh and node.ref not in delta.changes and node.ref not in delta.expanded:
            lines.extend(
                f"Element {node.ref}: {trim_start(hint)}" if hint.lstrip("\t").startswith("Omitted: ") else hint
                for hint in node.hints
            )
    if section.suffix:
        lines.append(section.suffix)
    return "\n".join(lines)


def _tree_delta(rendered: RenderedDocument, delta: TreeDelta, plan: DeltaPlan) -> str:
    parts: list[str] = []
    missing = delta.missing
    for part in rendered.parts:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, RenderedLiteral):
            parts.append(part.incremental_text)
        else:
            parts.append(_section_delta(part, delta, missing, plan))
            missing = ()
    return "".join(parts)


def _indent(text: str, depth: int) -> str:
    return "\n".join("\t" * depth + line for line in text.splitlines()) if text and depth else text


def format_document(
    document: DisplayDocument,
    snapshots: Mapping[int, RenderedTree],
    plan: DeltaPlan | None = None,
    *,
    public_api: bool = False,
) -> str:
    """Render captured fields once using the selected complete or incremental view.

    Args:
        document: Immutable observation with prepared projections and captured text fields.
        snapshots: Final snapshot renders produced by the explicit parent pipeline.
        plan: Structural comparison produced before any final text formatting.
        public_api: Whether continuation hints use Python API call syntax.

    Returns:
        Full text or a contextual diff with all current summaries and continuation hints.
    """
    full_parts: list[str] = []
    diff_parts: list[str] = []
    detail_roots: list[str] = []
    incremental = plan is not None and plan.mode != "full"
    refreshed = False
    directories, payloads = [], []
    catalogue_sources = set()
    for part in document.parts:
        if isinstance(part, DisplayTree) and part.details and id(part.prepared) not in catalogue_sources:
            catalogue_sources.add(id(part.prepared))
            rendered = snapshots[id(part.prepared)]
            directory = rendered.get("capturedContentDirectory")
            if directory:
                directories.append(directory)
                payloads.append(rendered["capturedContentPayload"])
    tree_index = 0
    for position, part in enumerate(document.parts):
        if not isinstance(part, DisplayTree):
            retained, roots = separate_details(part, snapshots)
            detail_roots.extend(roots)
            value = render_text(retained, snapshots=snapshots)
            if not incremental:
                shortened = full_header_text(document, position, snapshots)
                full_parts.append(value if shortened is None else shortened)
            diff_parts.append(value)
            continue
        rendered = snapshots[id(part.prepared)]
        if part.details:
            full = rendered["detailSupplement"] if "detailSupplement" in rendered else rendered["details"]
        else:
            full = rendered["text"]
        if not incremental:
            full_parts.append(_indent(full, part.indent))
            continue
        if incremental and tree_index < len(plan.trees) and plan.trees[tree_index] is None:
            diff_parts.append("Full tree for this scope:\n" + _indent(full, part.indent))
            tree_index += 1
            continue
        output = getattr(rendered, "document", None)
        if part.details:
            # This part is an explicit request for captured native details.
            # It cannot force unrelated compact trees back to full delivery.
            diff_parts.append(_indent(full, part.indent))
            tree_index += 1
            continue
        if tree_index >= len(plan.trees) or not isinstance(output, RenderedDocument):
            # A malformed terminal part must remain visible without discarding
            # the independently planned deltas of other windows.
            diff_parts.append("Presentation refresh for this scope:\n" + _indent(full, part.indent))
            refreshed = True
            tree_index += 1
            continue
        for _ in range(part.indent):
            output = output.indented()

        refreshed |= any(isinstance(section, RenderedSection) and section.refresh for section in output.parts)
        diff_parts.append(_tree_delta(output, plan.trees[tree_index], plan))
        tree_index += 1
    footer = detail_footer(detail_roots)
    full_text = ("\n\n".join((*directories, *payloads)) + "\n\n" if directories else "") + "".join(full_parts) + footer
    if not incremental:
        return full_text
    diff_text = _header("".join(diff_parts) + footer, "diff" if refreshed else plan.mode)
    return diff_text
