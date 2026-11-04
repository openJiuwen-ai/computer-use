# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Reversible role/state headings for distinct same-parent displayed leaves."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re

from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.interaction.read.presentation.tree import has_incomplete_children

_INTEGER = r"(?:0|[1-9][0-9]*)"
_NODE = re.compile(r"^(\t*)(" + _INTEGER + r") (.+)\Z")
_HEADER = re.compile(r"^(\t*)Elements \[([^\[\]\r\n]+)\]:\Z")
_SCOPE_ROLES = frozenset(("application", "window", "sheet", "menuBar", "menu"))
_MINIMUM_SAVING = 8


def _layout_size(text: str) -> int:
    """Estimate repeated text cost in UTF-8 bytes without a tokenizer dependency."""
    return len(text.encode("utf-8", errors="surrogatepass"))


@dataclass(frozen=True)
class _Leaf:
    """One complete generated line and its exact sibling position."""

    ref: str
    parent: str
    position: int
    indent: str
    prefix: str
    tail: str
    layout: tuple[tuple[str, str], ...]


def expand_leaf_headers(lines: list[str]) -> list[str]:
    """Restore each numbered line using only the public shared-heading syntax.

    A heading supplies the exact role/state prefix to each directly indented
    numbered row. Each row retains its complete remaining body and identity.
    Existing same-body ``Elements [body]: 1–3`` lines remain unchanged.
    """
    output: list[str] = []
    index = 0
    while index < len(lines):
        header = _HEADER.fullmatch(lines[index])
        if header is None:
            output.append(lines[index])
            index += 1
            continue
        start = index
        index += 1
        members: list[re.Match[str]] = []
        while index < len(lines):
            member = _NODE.fullmatch(lines[index])
            if member is None or member[1] != header[1] + "\t":
                break
            members.append(member)
            index += 1
        if len(members) >= 3 and len({member[2] for member in members}) == len(members):
            output.extend(header[1] + member[2] + " " + header[2] + " " + member[3] for member in members)
        else:
            output.extend(lines[start:index])
    return output


def _unexpanded_menus(frame: LayoutFrame) -> dict[str, int]:
    """Use generated coverage only while its exact summary remains delivered."""
    counts = Counter(ref for summary in frame.unexpanded_menus for ref, _ in summary.entries)
    return {
        ref: total
        for summary in frame.unexpanded_menus
        if frame.lines.count(summary.text) == 1
        for ref, total in summary.entries
        if counts[ref] == 1 and type(total) is int and total > 0
    }


def _leaves(frame: LayoutFrame) -> dict[int, _Leaf]:
    """Match generated prefixes to complete sibling leaves in this exact view."""
    records = {record.ref: record for record in frame.rendered_nodes}
    record_counts = Counter(record.ref for record in frame.rendered_nodes)
    line_counts = Counter(match[2] for line in frame.lines if (match := _NODE.fullmatch(line)))
    positions: dict[str, tuple[str, int]] = {}
    occurrences: Counter[str] = Counter()
    unexpanded = _unexpanded_menus(frame)
    pending = list(frame.roots)
    while pending:
        parent = pending.pop()
        pending.extend(parent["children"])
        for position, child in enumerate(parent["children"]):
            ref = child["ref"]
            occurrences[ref] += 1
            covered_menu = (
                child["role"] == "menuBarItem"
                and ref in unexpanded
                and type(child.get("childrenStart")) is int
                and child["childrenStart"] == 0
                and type(child.get("childrenEnd")) is int
                and child["childrenEnd"] == 0
                and type(child.get("childCount")) is int
                and child.get("childCount") == unexpanded[ref]
            )
            if (
                not child["children"]
                and (not has_incomplete_children(child) or covered_menu)
                and child["role"] not in _SCOPE_ROLES
                and child.get("subrole") not in ("dialog", "systemDialog", "popover")
            ):
                positions[ref] = (parent["ref"], position)
    result: dict[int, _Leaf] = {}
    for index, line in enumerate(frame.lines):
        match = _NODE.fullmatch(line)
        if match is None or match[2] not in positions:
            continue
        ref = match[2]
        record = records.get(ref)
        parent, position = positions[ref]
        if (
            record is None
            or record_counts[ref] != 1
            or line_counts[ref] != 1
            or occurrences[ref] != 1
            or record.parent != parent
            or record.partial
            or record.hints
            or "\n" in record.text
        ):
            continue
        if (
            index + 1 < len(frame.lines)
            and frame.lines[index + 1]
            and len(frame.lines[index + 1]) - len(frame.lines[index + 1].lstrip("\t")) > len(match[1])
        ):
            continue
        prefix = frame.node_prefixes.get(ref, "")
        if (
            not prefix
            or any(char in prefix for char in "[]\r\n\t")
            or not record.text.startswith(match[1] + ref + " " + prefix)
            or not match[3].startswith(prefix + " ")
        ):
            continue
        tail = match[3][len(prefix) + 1 :]
        if tail:
            result[index] = _Leaf(ref, parent, position, match[1], prefix, tail, record.layout)
    return result


def pack_leaf_headers(frame: LayoutFrame) -> list[str]:
    """Share role/state headings while preserving each complete leaf's body.

    Consecutive complete leaves share a prefix within their known parent and
    layout scope. A parent's collection gaps remain explicit beside the group.
    Unexpanded menu items retain their generated child-coverage summary. All
    fields and actions remain on their numbered rows; structured nodes and diff
    records remain independent.
    """
    if not frame.context.get("compact") or frame.context.get("subtreeRef") or not frame.rendered_nodes:
        return frame.lines
    candidates = _leaves(frame)
    output: list[str] = []
    index = 0
    while index < len(frame.lines):
        first = candidates.get(index)
        if first is None:
            output.append(frame.lines[index])
            index += 1
            continue
        members = [first]
        while index + len(members) < len(frame.lines):
            current = candidates.get(index + len(members))
            if (
                current is None
                or current.parent != first.parent
                or current.position != first.position + len(members)
                or current.indent != first.indent
                or current.prefix != first.prefix
                or current.layout != first.layout
            ):
                break
            members.append(current)
        packed = [first.indent + "Elements [" + first.prefix + "]:"]
        packed.extend(first.indent + "\t" + member.ref + " " + member.tail for member in members)
        original = frame.lines[index : index + len(members)]
        saving = _layout_size("\n".join(original)) - _layout_size("\n".join(packed))
        if len(members) >= 3 and saving >= _MINIMUM_SAVING:
            output.extend(packed)
            index += len(members)
        else:
            output.append(frame.lines[index])
            index += 1
    return output if expand_leaf_headers(output) == frame.lines else frame.lines
