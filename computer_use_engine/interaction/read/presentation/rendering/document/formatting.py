# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Generate full or incremental text at the final presentation boundary."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.content import render_text
from computer_use.interaction.read.presentation.delta import RustDeltaPlan
from computer_use.interaction.read.presentation.document import DisplayDocument, DisplayTree
from computer_use.interaction.read.presentation.rendering.document.details import detail_footer, separate_details
from computer_use.interaction.read.presentation.rendering.document.headers import full_header_text
from computer_use.interaction.read.presentation.rendering.document.model import RustRenderedTree

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.delta import DeltaPlan


def _header(text: str, mode: str) -> str:
    title = f"Tree: {mode}"
    if re.search(r"^Tree: ", text, re.MULTILINE):
        return re.sub(r"^Tree: .*$", lambda _: title, text, count=1, flags=re.MULTILINE)
    lines = text.split("\n") if text else []
    lines.insert(min(1, len(lines)), title)
    return "\n".join(lines)


def format_document(
    document: DisplayDocument,
    snapshots: Mapping[int, RustRenderedTree],
    plan: DeltaPlan | RustDeltaPlan | None = None,
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
        if incremental and not isinstance(plan, RustDeltaPlan):
            raise ValueError("Accessibility tree requires its structured delivery plan")
        handle = plan.handle if isinstance(plan, RustDeltaPlan) else None
        output, part_refreshed = rendered.handle.part(part.indent, part.details, handle, tree_index)
        (diff_parts if incremental else full_parts).append(output)
        refreshed |= part_refreshed
        tree_index += 1
    footer = detail_footer(detail_roots)
    full_text = ("\n\n".join((*directories, *payloads)) + "\n\n" if directories else "") + "".join(full_parts) + footer
    if not incremental:
        return full_text
    diff_text = _header("".join(diff_parts) + footer, "diff" if refreshed else plan.mode)
    return diff_text
