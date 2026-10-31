# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure serialization of prepared regions, fields and independent record members."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.rendering.output import RenderedDocument, RenderedSection

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDocument
    from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
    from computer_use.interaction.read.presentation.rendering.output import RenderedNode
    from computer_use.interaction.read.presentation.records import RenderedTree
    from computer_use.protocol.records import Record


def _reindent(text: str, before: int, after: int) -> str:
    """Move generated indentation, preserving all characters inside source fields."""
    prefix = "\t" * before
    return "\n".join(
        "\t" * after + (line[len(prefix) :] if line.startswith(prefix) else line) for line in text.split("\n")
    )


class _InterfaceFormatter:
    """Serialize one frozen block graph using exact, independently addressable node chunks."""

    def __init__(self, frame: LayoutFrame, document: InterfaceDocument) -> None:
        self.frame = frame
        self.document = document
        self.nodes = {node.ref: node for node in frame.rendered_nodes}
        self.output_nodes: dict[str, RenderedNode] = {}
        self.lines: list[str] = []

    def atom(self, ref: str) -> str | None:
        """Return a single exact generated element line when inline formatting is possible."""
        node = self.nodes[ref]
        return node.text.lstrip("\t") if "\n" not in node.text and not node.hints else None

    def shortened_atom(self, ref: str, prefix_ref: str | None) -> str | None:
        """Remove a generated prefix only when its declared shared spelling matches exactly."""
        text = self.atom(ref)
        if text is None or prefix_ref is None:
            return text
        prefix = self.frame.node_prefixes.get(prefix_ref)
        if prefix != self.frame.node_prefixes.get(ref):
            return None
        start = ref + " " + prefix
        if not text.startswith(start) or len(text) > len(start) and text[len(start)] not in (" ", ","):
            return None
        return ref + text[len(start) :]

    def remember(self, ref: str, depth: int, context: tuple[tuple[str, str], ...]) -> None:
        """Keep uncombined atomic facts and the context needed to interpret a later diff."""
        node = self.nodes[ref]
        before = len(node.text) - len(node.text.lstrip("\t"))
        self.output_nodes[ref] = replace(
            node,
            depth=depth,
            text=_reindent(node.text, before, depth),
            content=_reindent(node.content, before, depth),
            hints=tuple(_reindent(hint, before, depth) for hint in node.hints),
            layout=context,
        )

    def records(self, block: InterfaceBlock, depth: int, context: tuple[tuple[str, str], ...]) -> bool:
        """Serialize proven visual rows using shared column prefixes and individual references."""
        rows = [self.document.blocks[key] for key in block.children]
        matrix = [[self.document.blocks[key].primary_ref for key in row.children] for row in rows]
        if not matrix or any(None in row or len(row) != len(block.columns) for row in matrix):
            return False
        texts = [
            [self.shortened_atom(ref, block.column_prefix_refs[column]) for column, ref in enumerate(row)]
            for row in matrix
        ]
        if any(text is None for row in texts for text in row):
            return False
        prefixes = [
            self.frame.node_prefixes.get(ref, "") if ref is not None else label
            for label, ref in zip(block.columns, block.column_prefix_refs)
        ]
        header = "Visual records: " + " | ".join(prefixes)
        self.lines.append("\t" * depth + header)
        for row_index, (refs, cells) in enumerate(zip(matrix, texts)):
            self.lines.append(
                "\t" * (depth + 1)
                + " | ".join(self.frame.format_field(text) if "|" in text else text for text in cells)
            )
            row_context = (*context, (block.key + ":" + str(row_index), "\t" * depth + "Visual row:"))
            for ref in refs:
                self.remember(ref, depth + 1, row_context)
        return True


def _find_exact(lines: list[str], expected: list[str]) -> int | None:
    """Locate a unique producer-generated sequence without interpreting application text."""
    if not expected:
        return None
    matches = [
        index
        for index, line in enumerate(lines)
        if line == expected[0] and lines[index : index + len(expected)] == expected
    ]
    return matches[0] if len(matches) == 1 else None


def _inline_lines(cells: list[str], depth: int, header: str = "") -> list[str]:
    """Wrap complete numbered cells while keeping their shared prefix scoped to the group."""
    joined = (header + " " if header else "") + " | ".join(cells)
    if len(joined) <= 240:
        return ["\t" * depth + joined]
    result = ["\t" * depth + header] if header else []
    indentation = "\t" * (depth + bool(header))
    row: list[str] = []
    for cell in cells:
        if row and len(" | ".join((*row, cell))) > 240:
            result.append(indentation + " | ".join(row))
            row = []
        row.append(cell)
    if row:
        result.append(indentation + " | ".join(row))
    return result


def _inline_fragment(
    frame: LayoutFrame, block: InterfaceBlock, lines: list[str]
) -> tuple[int, list[str], list[str]] | None:
    """Adapt one planned run to its existing exact atomic or shared-prefix spelling.

    Existing table, field, chain or range packing is retained when it has already
    absorbed a member. All matching alternatives come from generated field slots.
    """
    document = frame.interface
    members = [document.blocks[key] for key in block.children]
    if not members or any(member.primary_ref is None or member.children for member in members):
        return None
    nodes = {node.ref: node for node in frame.rendered_nodes}
    refs = [member.primary_ref for member in members]
    atoms = [nodes[ref] for ref in refs]
    if any("\n" in atom.text or atom.hints for atom in atoms):
        return None
    depths = {len(atom.text) - len(atom.text.lstrip("\t")) for atom in atoms}
    if len(depths) != 1:
        return None
    depth = next(iter(depths))
    prefix = frame.node_prefixes.get(block.shared_prefix_ref) if block.shared_prefix_ref else None
    if prefix is not None and any(frame.node_prefixes.get(ref) != prefix for ref in refs):
        return None
    variants = [[atom.text.lstrip("\t") for atom in atoms]]
    slots = [frame.node_action_lines.get(ref) for ref in refs]
    if all(slot is not None and slot[0] == atom.text.lstrip("\t") for atom, slot in zip(atoms, slots)):
        variants.append([slot[1] for slot in slots])
    for bodies in variants:
        short = [ref + body[len(ref + " " + prefix) :] for ref, body in zip(refs, bodies)] if prefix else bodies
        if prefix and any(not body.startswith(ref + " " + prefix) for ref, body in zip(refs, bodies)):
            continue
        # Old prefix sharing may cover several newly planned geometric rows.
        # Keep that exact heading in place when only one row is being changed.
        if prefix:
            expected = ["\t" * (depth + 1) + body for body in short]
            start = _find_exact(lines, expected)
            header = "\t" * depth + "Elements [" + prefix + "]:"
            if start is not None:
                parent = next(
                    (
                        index
                        for index in range(start - 1, -1, -1)
                        if lines[index] and len(lines[index]) - len(lines[index].lstrip("\t")) <= depth
                    ),
                    None,
                )
                if parent is not None and lines[parent] == header:
                    after = start + len(expected)
                    whole_group = parent == start - 1 and (
                        after == len(lines) or len(lines[after]) - len(lines[after].lstrip("\t")) <= depth
                    )
                    if whole_group:
                        return parent, [header, *expected], _inline_lines(short, depth, header.lstrip("\t"))
                    return start, expected, _inline_lines(short, depth + 1)
        expected = ["\t" * depth + body for body in bodies]
        start = _find_exact(lines, expected)
        if start is not None:
            heading = "Elements [" + prefix + "]:" if prefix else ""
            return start, expected, _inline_lines(short, depth, heading)
    return None


def format_interface(frame: LayoutFrame) -> LayoutFrame:
    """Apply local block layouts while retaining established compact representations.

    The structured pipeline selects regions, fields and records before rendering.
    This adapter changes only complete, exact generated spans; an already packed
    subtree keeps its existing field, action and containment declarations. Every
    replacement retains its source references and the unpacked diff records.
    """
    document = frame.interface
    if document is None or not document.decisions or not frame.rendered_nodes:
        return frame
    nodes = {node.ref: node for node in frame.rendered_nodes}
    if set(document.target_refs) != set(nodes):
        return replace(
            frame,
            records=(
                *frame.records,
                {"stage": "interface-format", "fallback": "atomic inventory differs from prepared blocks"},
            ),
        )
    lines = list(frame.lines)
    applied: list[str] = []
    contexts: dict[str, tuple[tuple[str, str], ...]] = {}
    ordered_blocks = []
    pending = list(reversed(document.roots))
    while pending:
        block = document.blocks[pending.pop()]
        ordered_blocks.append(block)
        pending.extend(reversed(block.children))
    # Parent records own their row representation; child inline choices are local fallbacks.
    for block in ordered_blocks:
        if block.layout == "records":
            rows = [document.blocks[key] for key in block.children]
            refs = [[document.blocks[key].primary_ref for key in row.children] for row in rows]
            if any(ref is None or "\n" in nodes[ref].text or nodes[ref].hints for row in refs for ref in row):
                continue
            first = nodes[refs[0][0]]
            depth = len(first.text) - len(first.text.lstrip("\t")) - 1
            if depth < 0:
                continue
            expected = []
            for row in refs:
                expected.append("\t" * depth + "Visual row:")
                expected.extend(nodes[ref].text for ref in row)
            start = _find_exact(lines, expected)
            formatter = _InterfaceFormatter(frame, document)
            if start is not None and formatter.records(block, depth, ()):
                if len("\n".join(formatter.lines)) < len("\n".join(expected)):
                    lines[start : start + len(expected)] = formatter.lines
                    contexts.update((ref, item.layout) for ref, item in formatter.output_nodes.items())
                    applied.append(block.key)
        elif block.layout == "inline":
            fragment = _inline_fragment(frame, block, lines)
            if fragment is not None:
                start, expected, replacement = fragment
                savings = len("\n".join(expected)) - len("\n".join(replacement))
                if savings > 0 or savings == 0 and block.origin == "visual":
                    lines[start : start + len(expected)] = replacement
                    applied.append(block.key)
    # Real native headings delimit source-selected sections. An exact outside
    # sibling anchor bounds each region, so field/table packing within it survives.
    order = [node.ref for node in frame.rendered_nodes]
    for block in document.blocks.values():
        if block.kind != "region" or block.primary_ref is not None or block.label_ref is None:
            continue
        heading = nodes[block.label_ref]
        if "\n" in heading.text:
            continue
        members: set[str] = set()
        pending = list(block.children)
        while pending:
            child = document.blocks[pending.pop()]
            members.update(child.source_refs)
            pending.extend(child.children)
        positions = [order.index(ref) for ref in members]
        if sorted(positions) != list(range(min(positions), max(positions) + 1)):
            continue
        start = _find_exact(lines, [heading.text])
        next_position = max(positions) + 1
        end = (
            _find_exact(lines, [nodes[order[next_position]].text.split("\n", 1)[0]])
            if next_position < len(order)
            else len(lines)
        )
        if start is None or end is None or end <= start:
            continue
        depth = len(heading.text) - len(heading.text.lstrip("\t"))
        # Trailing summaries remain outside the final section.
        while end > start + 1 and (
            not lines[end - 1] or len(lines[end - 1]) - len(lines[end - 1].lstrip("\t")) < depth
        ):
            end -= 1
        lines[start + 1 : end] = ["\t" + line if line else line for line in lines[start + 1 : end]]
        title = "\t" * depth + "Section: " + heading.text.lstrip("\t")
        for ref in members - {block.label_ref}:
            contexts[ref] = ((block.key, title), *contexts.get(ref, nodes[ref].layout))
        applied.append(block.key)
    if not applied:
        return frame

    def finalize(laid_out: list[str], records: list[Record]) -> RenderedTree:
        result = frame.finalize(laid_out, records)
        rendered = getattr(result, "document", None)
        if isinstance(rendered, RenderedDocument):
            result.document = replace(
                rendered,
                parts=tuple(
                    replace(
                        part,
                        nodes=tuple(replace(node, layout=contexts.get(node.ref, node.layout)) for node in part.nodes),
                    )
                    if isinstance(part, RenderedSection)
                    else part
                    for part in rendered.parts
                ),
            )
        result["interfaceLayout"] = {
            "version": document.format_version,
            "applied": tuple(applied),
            "decisions": document.decisions,
            "references": document.target_refs,
        }
        return result

    return replace(
        frame,
        lines=lines,
        finalize=finalize,
        records=(*frame.records, {"stage": "interface-format", "applied": tuple(applied)}),
    )
