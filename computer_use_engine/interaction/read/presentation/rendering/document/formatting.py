"""Generate full or incremental text at the final presentation boundary."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.content import render_text
from computer_use.interaction.read.presentation.document import DisplayDocument, DisplayTree
from computer_use.interaction.read.presentation.rendering.output import RenderedDocument, RenderedSection
from computer_use.protocol.primitives import trimStart, utf16Length

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.delta import DeltaPlan, TreeDelta
    from computer_use.interaction.read.presentation.records import RenderedTree


def _header(text: str, mode: str, public_api: bool) -> str:
    title = (
        f"Tree: {mode} (not full; read(full=True) for full text)"
        if public_api
        else f'Tree: {mode} (not full; read({{"full": True}}) for full text)'
    )
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
        lines.append("Not observed in this read (not proof of removal): " + ", ".join(unobserved))
    for node in section.nodes:
        if node.ref in delta.changes:
            lines.append(delta.changes[node.ref] + node.text)
        elif node.ref in delta.context:
            lines.append(" " + (node.text if node.ref in delta.expanded else node.text.split("\n", 1)[0]))
        if node.ref not in delta.changes and node.ref not in delta.expanded:
            lines.extend(
                f"Element {node.ref}: {trimStart(hint)}" if hint.lstrip("\t").startswith("Omitted: ") else hint
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
    """Render captured fields once and choose the shorter complete or incremental view.

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
    incremental = plan is not None and plan.mode != "full"
    tree_index = 0
    for part in document.parts:
        if not isinstance(part, DisplayTree):
            value = render_text(part, snapshots=snapshots)
            full_parts.append(value)
            diff_parts.append(value)
            continue
        rendered = snapshots[id(part.prepared)]
        full = rendered["details"] if part.details else rendered["text"]
        full_parts.append(_indent(full, part.indent))
        output = getattr(rendered, "document", None)
        if not incremental or tree_index >= len(plan.trees) or part.details or not isinstance(output, RenderedDocument):
            incremental = False
            tree_index += 1
            continue
        for _ in range(part.indent):
            output = output.indented()
        displayed = {
            node.ref for section in output.parts if isinstance(section, RenderedSection) for node in section.nodes
        }
        if displayed != {node.ref for node in part.nodes}:
            incremental = False
            tree_index += 1
            continue
        diff_parts.append(_tree_delta(output, plan.trees[tree_index], plan))
        tree_index += 1
    full_text = "".join(full_parts)
    if not incremental or tree_index != len(plan.trees):
        return full_text
    diff_text = _header("".join(diff_parts), plan.mode, public_api)
    return diff_text if utf16Length(diff_text) < utf16Length(full_text) else full_text
