# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Reversible shorthand for distinct sibling leaves with identical display bodies."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re

from computer_use.interaction.read.presentation.records import NodeView
from computer_use.interaction.read.presentation.tree import has_incomplete_children

_INTEGER = r"(?:0|[1-9][0-9]*)"
_NODE = re.compile(r"^(\t*)(" + _INTEGER + r") (.+)\Z")
_PACKED = re.compile(
    r"^(\t*)Elements \[(.+)\]: ("
    + _INTEGER
    + r"(?:–"
    + _INTEGER
    + r")?(?:, "
    + _INTEGER
    + r"(?:–"
    + _INTEGER
    + r")?)*)\Z"
)


def _ordered_references(refs: Sequence[str]) -> str:
    """Shorten ascending runs while preserving the exact element order."""
    parts: list[str] = []
    start = 0
    while start < len(refs):
        end = start + 1
        while end < len(refs) and int(refs[end]) == int(refs[end - 1]) + 1:
            end += 1
        if end - start >= 3:
            parts.append(refs[start] + "–" + refs[end - 1])
        else:
            parts.extend(refs[start:end])
        start = end
    return ", ".join(parts)


def expand_identical_leaves(lines: list[str]) -> list[str]:
    """Restore every numbered element from an Elements shorthand line.

    The bracketed body belongs to each listed element independently. Reference
    order, indentation and the complete body are restored verbatim.
    """
    output: list[str] = []
    for line in lines:
        match = _PACKED.fullmatch(line)
        if match is None:
            output.append(line)
            continue
        refs: list[str] = []
        for part in match[3].split(", "):
            endpoints = part.split("–")
            if len(endpoints) == 1:
                refs.append(part)
            elif int(endpoints[1]) > int(endpoints[0]):
                refs.extend(str(value) for value in range(int(endpoints[0]), int(endpoints[1]) + 1))
            else:
                break
        else:
            if len(refs) >= 3 and len(refs) == len(set(refs)):
                output.extend(match[1] + ref + " " + match[2] for ref in refs)
                continue
        output.append(line)
    return output


def pack_identical_leaves(lines: list[str], roots: Sequence[NodeView], context: Mapping[str, object]) -> list[str]:
    """Pack adjacent same-parent leaves only when their full bodies are equal.

    Attached lines and incomplete children keep their individual layout. Native
    nodes, references, actions and structured diff records remain unchanged.
    """
    if not context.get("compact") or context.get("subtreeRef"):
        return lines
    siblings: dict[str, tuple[str, int]] = {}
    pending = list(roots)
    while pending:
        parent = pending.pop()
        children = parent["children"]
        pending.extend(children)
        for index, child in enumerate(children):
            if not child["children"] and not has_incomplete_children(child):
                siblings[child["ref"]] = (parent["ref"], index)

    def candidate(index: int) -> re.Match[str] | None:
        match = _NODE.fullmatch(lines[index])
        if match is None or match[2] not in siblings:
            return None
        if index + 1 < len(lines):
            following = _NODE.fullmatch(lines[index + 1])
            if following is None or len(following[1]) > len(match[1]):
                return None
        return match

    output: list[str] = []
    index = 0
    while index < len(lines):
        first = candidate(index)
        if first is None:
            output.append(lines[index])
            index += 1
            continue
        refs = [first[2]]
        parent, position = siblings[first[2]]
        while index + len(refs) < len(lines):
            item = candidate(index + len(refs))
            if (
                item is None
                or item[1] != first[1]
                or item[3] != first[3]
                or siblings[item[2]] != (parent, position + len(refs))
            ):
                break
            refs.append(item[2])
        packed = first[1] + "Elements [" + first[3] + "]: " + _ordered_references(refs)
        if len(refs) >= 3 and len(packed) < len("\n".join(lines[index : index + len(refs)])):
            output.append(packed)
            index += len(refs)
        else:
            output.append(lines[index])
            index += 1
    return output if expand_identical_leaves(output) == lines else lines
