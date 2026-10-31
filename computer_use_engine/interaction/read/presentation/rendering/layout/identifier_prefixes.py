# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Share one exact native ID prefix within a compact full root presentation."""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
import json
from os.path import commonprefix
import re
from types import MappingProxyType
import unicodedata

from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame, NativeIdentifier
from computer_use.protocol.observation import NativeObservation

_DECLARATION = "ID prefix (prepend to ID suffix): "
_BOUNDARIES = frozenset("./:_")
_MENUS = frozenset(("menu", "menuBar", "menuBarItem"))
_MINIMUM_SAVING = 32
_PREFIX_PREAMBLE = re.compile(r"([A-Za-z]+) prefix \(prepend to (?:quoted )?\1 suffix\): (.*)")


def capture_identifiers(
    observation: NativeObservation, refs: Mapping[str, str], *, source_scopes: Mapping[str, str]
) -> Mapping[str, NativeIdentifier]:
    """Retain complete IDs and unique native bindings without retaining the snapshot.

    A partial node collection can contain successfully read identifier fields.
    Global failures and failures of an object or its identifier still exclude
    the corresponding evidence.
    """
    counts = Counter(refs.values())
    blocked = set()
    for issue in observation.issues:
        if not issue.path or (issue.path == ("nodes",) and issue.state != "partial"):
            return MappingProxyType({})
        if (
            issue.path[0] == "nodes"
            and len(issue.path) >= 2
            and (len(issue.path) == 2 or issue.path[2] == "identifier")
        ):
            blocked.add(issue.path[1])
    result: dict[str, NativeIdentifier] = {}
    for ref, token in refs.items():
        node = observation.nodes.get(token)
        if node is None or counts[token] != 1 or ref not in source_scopes or not isinstance(node.identifier, str):
            continue
        if token in blocked:
            continue
        result[ref] = NativeIdentifier(
            node.identifier, token, (observation.session, observation.id, source_scopes[ref])
        )
    return MappingProxyType(result)


def _plain(value: str) -> bool:
    return bool(value) and not any(
        unicodedata.category(char).startswith("C") or char in "\u2028\u2029" for char in value
    )


@dataclass(frozen=True)
class _IdentifierLine:
    """One unchanged generated node line ending in its complete native ID."""

    index: int
    value: str
    start: int
    scope: tuple[str, str, str]


def _identifier_lines(frame: LayoutFrame) -> tuple[int, list[_IdentifierLine]] | None:
    if len(frame.roots) != 1 or frame.roots[0]["role"] in _MENUS:
        return None
    root = frame.roots[0]
    nodes, parents, occurrences = {}, {}, Counter()
    pending = [(root, None)]
    while pending:
        node, parent = pending.pop()
        ref = node["ref"]
        nodes[ref], parents[ref] = node, parent
        occurrences[ref] += 1
        pending.extend((child, ref) for child in node["children"])
    positions: dict[str, list[int]] = {}
    for index, line in enumerate(frame.lines):
        positions.setdefault(line, []).append(index)
    counts = Counter(record.ref for record in frame.rendered_nodes)
    root_record = next((record for record in frame.rendered_nodes if record.ref == root["ref"]), None)
    if (
        root_record is None
        or counts[root["ref"]] != 1
        or "\n" in root_record.text
        or not root_record.text.startswith("\t" * root_record.depth + root["ref"] + " ")
        or len(positions.get(root_record.text, ())) != 1
    ):
        return None
    root_index = positions[root_record.text][0]
    candidates: list[_IdentifierLine] = []
    for record in frame.rendered_nodes:
        source, node = frame.identifier_sources.get(record.ref), nodes.get(record.ref)
        span = frame.node_line_ranges.get(record.ref)
        if (
            source is None
            or node is None
            or source.scope[2] != root["ref"]
            or counts[record.ref] != 1
            or occurrences[record.ref] != 1
            or record.parent != parents[record.ref]
            or node["role"] in _MENUS
            or record.partial
            or record.hints
            or record.related
            or record.layout
            or span is None
            or span[1] - span[0] != 1
            or "\n" in record.text
            or len(positions.get(record.text, ())) != 1
            or not _plain(source.value)
            or node.get("identifier") != source.value
            or frame.format_field(source.value) != source.value
        ):
            continue
        index = positions[record.text][0]
        prefix = "\t" * record.depth + record.ref + " "
        suffix = "ID: " + source.value
        if (
            index < root_index
            or not record.text.startswith(prefix)
            or not record.text.endswith(suffix)
            or " | " in record.text
            or " > " in record.text
        ):
            continue
        start = len(record.text) - len(suffix)
        if start <= len(prefix) or record.text[start - 1] != " ":
            continue
        candidates.append(_IdentifierLine(index, source.value, start, source.scope))
    return root_index, candidates


def expand_identifier_prefixes(lines: list[str]) -> list[str]:
    """Restore full ID fields from public prefix/suffix declarations alone.

    Each declaration belongs to the following root and its indented contents.
    Other field declarations may precede the same numbered root. A following
    peer root, same-kind declaration, window or menu heading ends that scope.
    Numbered lines retain their own identifiers; only
    an explicit, complete, terminal ``ID suffix`` string uses the declaration.
    """
    output: list[str] = []
    prefix: str | None = None
    indent = ""
    root_started = False
    for line in lines:
        if re.fullmatch(r"\s*(?:Window \d+:|Application menu:.*)", line):
            prefix = None
        stripped = line.lstrip("\t")
        if stripped.startswith(_DECLARATION):
            if prefix is not None and not root_started:
                raise ValueError("multiple identifier prefixes in one root scope")
            prefix = json.loads(stripped[len(_DECLARATION) :])
            indent = line[: len(line) - len(stripped)]
            root_started = False
            if not isinstance(prefix, str) or not _plain(prefix):
                raise ValueError("invalid identifier prefix")
            continue
        if prefix is not None and line:
            depth = len(line) - len(stripped)
            if root_started and depth <= len(indent):
                prefix = None
            elif not root_started and depth == len(indent):
                preamble = _PREFIX_PREAMBLE.fullmatch(stripped)
                if preamble is not None:
                    try:
                        value = json.loads(preamble[2])
                    except (ValueError, TypeError):
                        value = None
                    if isinstance(value, str) and _plain(value):
                        output.append(line)
                        continue
                if re.match(r"^(?:0|[1-9][0-9]*) ", stripped):
                    root_started = True
                else:
                    prefix = None
        if prefix is not None and re.match(r"^\t*(?:0|[1-9][0-9]*) ", line):
            marker = ", ID suffix: "
            at = line.rfind(marker)
            if at < 0:
                marker = " ID suffix: "
                at = line.rfind(marker)
            if at >= 0:
                suffix = json.loads(line[at + len(marker) :])
                if not isinstance(suffix, str):
                    raise ValueError("invalid identifier suffix")
                line = line[:at] + marker.replace("ID suffix:", "ID:") + prefix + suffix
        output.append(line)
    return output


def pack_identifier_prefixes(frame: LayoutFrame) -> list[str]:
    """Shorten only proven unchanged ID fields; all structured records stay full.

    Share at most one complete path-component prefix in this root. Candidate
    cost uses UTF-8 bytes without a tokenizer dependency. Complex layouts and
    explicit element reads retain their original self-contained ID fields.
    """
    if not frame.context.get("compact") or frame.context.get("subtreeRef"):
        return frame.lines
    evidence = _identifier_lines(frame)
    if evidence is None:
        return frame.lines
    root_index, members = evidence
    root_indent = frame.lines[root_index][: -len(frame.lines[root_index].lstrip("\t"))]
    if len(members) < 2 or len({member.scope for member in members}) != 1:
        return frame.lines
    ordered = sorted(members, key=lambda item: item.value)
    values = [member.value for member in ordered]
    prefixes = set()
    for left, right in zip(values, values[1:]):
        common = commonprefix((left, right))
        endings = [index + 1 for index, char in enumerate(common) if char in _BOUNDARIES]
        if endings:
            prefixes.add(common[: endings[-1]])
    best: tuple[int, str, list[_IdentifierLine]] | None = None
    for prefix in sorted(prefixes):
        start = bisect_left(values, prefix)
        matching = []
        for member in ordered[start:]:
            if not member.value.startswith(prefix):
                break
            matching.append(member)
        if len(matching) < 2:
            continue
        declaration = _DECLARATION + json.dumps(prefix, ensure_ascii=False)
        saving = -len((root_indent + declaration + "\n").encode("utf-8"))
        for member in matching:
            replacement = "ID suffix: " + json.dumps(member.value[len(prefix) :], ensure_ascii=False)
            saving += len(("ID: " + member.value).encode("utf-8")) - len(replacement.encode("utf-8"))
        if saving >= _MINIMUM_SAVING and (best is None or saving > best[0]):
            best = saving, prefix, matching
    if best is None:
        return frame.lines
    _, prefix, matching = best
    output = list(frame.lines)
    for member in matching:
        output[member.index] = (
            output[member.index][: member.start]
            + "ID suffix: "
            + json.dumps(member.value[len(prefix) :], ensure_ascii=False)
        )
    output.insert(root_index, root_indent + _DECLARATION + json.dumps(prefix, ensure_ascii=False))
    try:
        return output if expand_identifier_prefixes(output) == frame.lines else frame.lines
    except (ValueError, TypeError):
        return frame.lines
