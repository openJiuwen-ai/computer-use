# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Reuse complete ancestor text without folding its independently numbered children."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
import json
import re
from types import MappingProxyType
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.accounting.fields import RenderedResult
from computer_use.interaction.read.presentation.rendering.layout.chains import split_chain
from computer_use.protocol.observation import NativeObservation

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.records import RenderedTree
    from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame


@dataclass(frozen=True)
class TextSource:
    """Complete original fields and the proven direct containment of one source.

    Attributes:
        source: Original native object token, independent of display numbering.
        scope: Session, observation and original window or root identity.
        parent: Unambiguous original parent in the same scope, when available.
        name: Complete native name; None means it cannot supply an anchor.
        value: Complete primary control value, independent of text-range units.
    """

    source: str
    scope: tuple[str, str, str]
    parent: str | None
    name: str | None = None
    value: str | None = None


@dataclass(frozen=True)
class TextSliceSources:
    """Read-only field and containment evidence, keyed by display reference.

    Attributes:
        nodes: Frozen sources indexed by the observation's unchanged references.
    """

    nodes: Mapping[str, TextSource]

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", MappingProxyType(dict(self.nodes)))


def capture_text_slice_sources(
    observation: NativeObservation,
    refs: Mapping[str, str],
    *,
    source_scopes: Mapping[str, str],
    value_fields: Mapping[str, str] | None = None,
) -> TextSliceSources:
    """Index exact string fields and unambiguous original parent edges once.

    Only reported, complete child relations establish containment. A gap in the
    overall node collection does not invalidate a field already acquired; issues
    on that object, field, or child relation do. No role or geometry infers a link.
    """
    counts = Counter(refs.values())
    inverse = {token: ref for ref, token in refs.items() if counts[token] == 1}
    issues: dict[str, list[tuple[str | int, ...]]] = {}
    for issue in observation.issues:
        if not issue.path:
            return TextSliceSources({})
        if len(issue.path) >= 2 and issue.path[0] == "nodes":
            issues.setdefault(issue.path[1], []).append(issue.path[2:])

    def complete(token: str, path: tuple[str, ...]) -> bool:
        return not any(path[: len(issue)] == issue or issue[: len(path)] == path for issue in issues.get(token, ()))

    parents: dict[str, set[str]] = {}
    unreliable: set[str] = set()
    for token, node in observation.nodes.items():
        for key, relation in node.relations.items():
            if relation.kind != "children":
                continue
            valid = (
                complete(token, ("relations", key))
                and relation.evidence in ("reported", "verified")
                and relation.enumeration == "enumerated"
                and relation.offset in (None, 0)
                and (relation.total is None or relation.total == len(relation.targets))
                and (relation.positions is None or relation.positions == tuple(range(len(relation.targets))))
                and len(set(relation.targets)) == len(relation.targets)
            )
            for child in relation.targets:
                parents.setdefault(child, set()).add(token)
                if not valid:
                    unreliable.add(child)
    result = {}
    for ref, token in refs.items():
        node = observation.nodes.get(token)
        if node is None or counts[token] != 1 or ref not in source_scopes:
            continue
        parent_tokens = parents.get(token, set())
        parent = next(iter(parent_tokens)) if len(parent_tokens) == 1 and token not in unreliable else None
        # Explicit native parent reports must agree with the child edge.
        for key, relation in node.relations.items():
            if relation.kind == "parent" and (
                not complete(token, ("relations", key))
                or relation.evidence not in ("reported", "verified")
                or relation.targets != (parent,)
            ):
                parent = None
        parent_ref = inverse.get(parent)
        if source_scopes.get(parent_ref) != source_scopes[ref]:
            parent_ref = None
        name = node.name if type(node.name) is str and complete(token, ("name",)) else None
        primary_control = (
            value_fields.get(ref) == "control_value.value"
            if value_fields is not None
            else node.text is None or node.text.content_field == ("control_value", "value")
        )
        value = (
            node.control_value.value
            if primary_control
            and node.control_value is not None
            and type(node.control_value.value) is str
            and complete(token, ("control_value", "value"))
            else None
        )
        result[ref] = TextSource(
            token, (observation.session, observation.id, source_scopes[ref]), parent_ref, name, value
        )
    # Cut only edges that participate in a cycle. A broken or conflicting
    # ancestor above an otherwise reliable local segment does not invalidate
    # that segment; candidate search never crosses its already-cut edge.
    checked: set[str] = set()
    cyclic: set[str] = set()
    for ref in result:
        current, path, positions = ref, [], {}
        while current in result and current not in checked:
            if current in positions:
                cyclic.update(path[positions[current] :])
                break
            positions[current] = len(path)
            path.append(current)
            current = result[current].parent
        checked.update(path)
    return TextSliceSources(
        {
            ref: replace(source, parent=None) if ref in cyclic or ref == source.scope[2] else source
            for ref, source in result.items()
        }
    )


_REFERENCE = re.compile(r"(Text|Value): text \[(\d+), (\d+)\) of element (\d+)\.(Name|Value) \(UTF-16\)")
_NODE = re.compile(r"^(\t*)(\d+) (.+)$")
_MINIMUM_SAVING = 64


def _boundaries(text: str) -> list[int]:
    """Return character positions outside JSON-quoted scalar strings."""
    positions, quoted, escaped = [], False, False
    for index, char in enumerate(text):
        if not quoted:
            positions.append(index)
        if escaped:
            escaped = False
        elif quoted and char == "\\":
            escaped = True
        elif char == '"':
            quoted = not quoted
    return positions


def _scalar(text: str, start: int) -> tuple[str, int]:
    if text[start : start + 1] == '"':
        value, length = json.JSONDecoder().raw_decode(text[start:])
        if not isinstance(value, str):
            raise ValueError("text anchor must be a string")
        return value, start + length
    end = text.find(", ", start)
    end = len(text) if end < 0 else end
    return text[start:end], end


def _segments(lines: list[str]) -> dict[str, tuple[int, int, str]]:
    """Index only whole numbered bodies, including reversible chain members."""
    result, duplicates = {}, set()
    for index, line in enumerate(lines):
        if not _NODE.match(line):
            continue
        start = len(line) - len(line.lstrip("\t"))
        for body in split_chain(line[start:]):
            match = re.match(r"^(\d+) ", body)
            if not match:
                break
            ref = match[1]
            if ref in result:
                duplicates.add(ref)
            result[ref] = index, start, body
            start += len(body) + 3
    return {ref: item for ref, item in result.items() if ref not in duplicates}


def expand_exact_text_slices(lines: list[str], format_field: Callable[[str], str]) -> list[str]:
    """Restore source text using only public declarations and their UTF-16 ranges.

    ``Text`` restores bare field text; ``Value`` retains its explicit field label.
    A referenced ``Name`` is explicitly marked at its original source. Anchors
    remain literal, so expansion never follows a second reference or a cycle.
    """
    segments = _segments(lines)
    replacements: dict[int, list[tuple[int, int, str]]] = {}
    marked_names: set[str] = set()
    for _, (index, offset, body) in segments.items():
        outside = set(_boundaries(body))
        for match in _REFERENCE.finditer(body):
            if match.start() not in outside or (match.start() and body[match.start() - 1] != " "):
                continue
            if match.end() != len(body) and not body[match.end() :].startswith(", "):
                raise ValueError("ambiguous text slice field")
            _, _, anchor = segments[match[4]]
            label = match[5] + ": "
            positions = [
                p for p in _boundaries(anchor) if anchor.startswith(label, p) and p > 0 and anchor[p - 1] == " "
            ]
            if len(positions) != 1:
                raise ValueError("missing or ambiguous literal text anchor")
            value, _ = _scalar(anchor, positions[0] + len(label))
            if _REFERENCE.search(value):
                raise ValueError("text anchors must remain literal")
            raw = value.encode("utf-16-le")
            start, end = int(match[2]), int(match[3])
            if start < 0 or start >= end or end * 2 > len(raw):
                raise ValueError("text slice is outside its anchor")
            restored = format_field(raw[start * 2 : end * 2].decode("utf-16-le"))
            if match[1] == "Value":
                restored = "Value: " + restored
            replacements.setdefault(index, []).append((offset + match.start(), offset + match.end(), restored))
            if match[5] == "Name":
                marked_names.add(match[4])
    for ref in marked_names:
        index, offset, body = segments[ref]
        locations = [p for p in _boundaries(body) if body.startswith("Name: ", p) and p > 0 and body[p - 1] == " "]
        if len(locations) != 1:
            raise ValueError("ambiguous Name marker")
        replacements.setdefault(index, []).append((offset + locations[0], offset + locations[0] + 6, ""))
    result = list(lines)
    for index, edits in replacements.items():
        for start, end, replacement in sorted(edits, reverse=True):
            result[index] = result[index][:start] + replacement + result[index][end:]
    return result


@dataclass(frozen=True)
class _Field:
    ref: str
    attribute: str
    path: tuple[str, ...]
    value: str
    line: int
    start: int
    end: int
    bare: bool


@dataclass(frozen=True)
class TextSlice:
    """One exact public replacement with retained child and ancestor sources.

    Attributes:
        field: Child field whose complete text is repeated by the ancestor.
        anchor: Complete publicly displayed ancestor Name or Value.
        start: Inclusive UTF-16 code-unit offset in the anchor's string.
        end: Exclusive UTF-16 code-unit offset in the anchor's string.
        statement: Exact replacement inserted at the child's original field.
    """

    field: _Field
    anchor: _Field
    start: int
    end: int
    statement: str


@dataclass(frozen=True)
class TextSliceLayout:
    """Packed text and the exact public destinations changed by this layout.

    Attributes:
        lines: Complete output lines after reversible field replacement.
        slices: Source-preserving replacements actually present in those lines.
    """

    lines: list[str]
    slices: tuple[TextSlice, ...] = ()


def _public_fields(frame: LayoutFrame) -> dict[tuple[str, str], _Field]:
    evidence = frame.text_slice_sources
    if evidence is None:
        return {}
    nodes, counts = {}, Counter()
    pending = list(frame.roots)
    while pending:
        node = pending.pop()
        nodes[node["ref"]] = node
        counts[node["ref"]] += 1
        pending.extend(node["children"])
    records = {record.ref: record for record in frame.rendered_nodes}
    record_counts = Counter(record.ref for record in frame.rendered_nodes)
    result = {}
    for ref, (index, offset, body) in _segments(frame.lines).items():
        node, record, source = nodes.get(ref), records.get(ref), evidence.nodes.get(ref)
        prefix = ref + " " + frame.node_prefixes.get(ref, "")
        if (
            node is None
            or record is None
            or source is None
            or counts[ref] != 1
            or record_counts[ref] != 1
            or record.partial
            or not frame.node_prefixes.get(ref)
            or ": " in prefix
            or body != record.text.split("\n", 1)[0].lstrip("\t")
            or not body.startswith(prefix + " ")
        ):
            continue
        for attribute, value, path in (
            ("Name", source.name, ("name",)),
            ("Value", source.value, ("control_value", "value")),
        ):
            key = attribute.lower()
            if (
                value is None
                or not value
                or any(c in value for c in "\r\n\u2028\u2029")
                or _REFERENCE.search(value)
                or node.get(key) != value
            ):
                continue
            try:
                encoded = frame.format_field(value)
                value.encode("utf-16-le")
            except (UnicodeError, ValueError):
                continue
            bare = (
                attribute == "Value"
                and node["role"] == "staticText"
                and not node.get("name")
                and not node.get("description")
            )
            if attribute == "Name" or bare:
                start = len(prefix) + 1
                if not body.startswith(encoded, start):
                    continue
            else:
                marker = "Value: "
                starts = [
                    p + len(marker)
                    for p in _boundaries(body)
                    if body.startswith(marker, p) and p > 0 and body[p - 1] == " "
                ]
                if len(starts) != 1:
                    continue
                start = starts[0]
                if not body.startswith(encoded, start):
                    continue
            end = start + len(encoded)
            if end != len(body) and not body[end:].startswith(", "):
                continue
            # Decoding the exact emitted scalar excludes shortened/encoded values
            # and makes punctuation in provider text distinct from field delimiters.
            try:
                decoded, decoded_end = _scalar(body, start)
            except (ValueError, TypeError):
                continue
            if decoded != value or decoded_end != end:
                continue
            result[(ref, attribute)] = _Field(ref, key, path, value, index, offset + start, offset + end, bare)
    return result


def pack_exact_text_slices(frame: LayoutFrame) -> TextSliceLayout:
    """Replace profitable exact child values using complete public ancestor fields.

    Native scope and containment restrict candidates; generated text is checked
    independently. A selected anchor remains literal and cannot itself become a
    reference. Full expansion must recover every original output character.
    """
    unchanged = TextSliceLayout(frame.lines)
    if not frame.context.get("compact") or frame.context.get("subtreeRef") or frame.text_slice_sources is None:
        return unchanged
    if any(_REFERENCE.search(line) for line in frame.lines):
        return unchanged
    fields = _public_fields(frame)
    evidence = frame.text_slice_sources.nodes
    selected: list[TextSlice] = []
    anchors: set[tuple[str, str]] = set()
    replaced: set[tuple[str, str]] = set()
    for (ref, attribute), field in fields.items():
        if attribute != "Value" or (ref, attribute) in anchors:
            continue
        current, seen = evidence[ref].parent, {ref}
        while current is not None and current not in seen:
            seen.add(current)
            parent = evidence.get(current)
            if parent is None or parent.scope != evidence[ref].scope:
                break
            found = False
            for owner_field in ("Value", "Name"):
                owner = fields.get((current, owner_field))
                if owner is None or (current, owner_field) in replaced or owner.bare:
                    continue
                position = owner.value.find(field.value)
                if position < 0 or owner.value.find(field.value, position + 1) >= 0:
                    continue
                start = len(owner.value[:position].encode("utf-16-le")) // 2
                end = start + len(field.value.encode("utf-16-le")) // 2
                statement = (
                    "text ["
                    + str(start)
                    + ", "
                    + str(end)
                    + ") of element "
                    + current
                    + "."
                    + owner_field
                    + " (UTF-16)"
                )
                replacement = ("Text: " if field.bare else "") + statement
                saving = len(frame.lines[field.line][field.start : field.end].encode("utf-8")) - len(
                    replacement.encode("utf-8")
                )
                if owner_field == "Name" and (current, owner_field) not in anchors:
                    saving -= 6
                if saving < _MINIMUM_SAVING:
                    continue
                selected.append(TextSlice(field, owner, start, end, replacement))
                anchors.add((current, owner_field))
                replaced.add((ref, attribute))
                found = True
                break
            if found:
                break
            current = parent.parent
    if not selected:
        return unchanged
    edits: dict[int, list[tuple[int, int, str]]] = {}
    for item in selected:
        edits.setdefault(item.field.line, []).append((item.field.start, item.field.end, item.statement))
    for ref, attribute in anchors:
        if attribute == "Name":
            anchor = fields[(ref, attribute)]
            edits.setdefault(anchor.line, []).append((anchor.start, anchor.start, "Name: "))
    result = list(frame.lines)
    for index, replacements in edits.items():
        for start, end, replacement in sorted(replacements, reverse=True):
            result[index] = result[index][:start] + replacement + result[index][end:]
    try:
        if expand_exact_text_slices(result, frame.format_field) != frame.lines:
            return unchanged
    except (KeyError, ValueError, TypeError, UnicodeError):
        return unchanged
    return TextSliceLayout(result, tuple(selected))


def record_text_slices(rendered: RenderedTree, layout: TextSliceLayout, sources: TextSliceSources) -> RenderedTree:
    """Record actual public slice destinations after the original finalizer runs.

    Structured node bodies and their comparison fields retain original text.
    Only successful public field emissions gain the exact slice destination.
    """
    if not layout.slices:
        return rendered
    covered = {(item.field.ref, item.field.attribute) for item in layout.slices}
    entries = []
    for item in layout.slices:
        source, anchor = sources.nodes[item.field.ref], sources.nodes[item.anchor.ref]
        entries.append(
            dict(
                ref=item.field.ref,
                native=source.source,
                field=item.field.attribute,
                path=list(item.field.path),
                value=item.field.value,
                anchor=item.anchor.ref,
                anchorNative=anchor.source,
                anchorField=item.anchor.attribute,
                anchorPath=list(item.anchor.path),
                start=item.start,
                end=item.end,
                unit="utf16_code_unit",
                scope=list(source.scope),
                bare=item.field.bare,
                statement=item.statement,
            )
        )
    rendered["compactTextSlices"] = entries
    if "fieldCoverage" in rendered:
        rendered["fieldCoverage"] = [
            dict(claim, kind="text-slice") if (claim["target"], claim["field"]) in covered else claim
            for claim in rendered["fieldCoverage"]
        ]
    if isinstance(rendered, RenderedResult):
        rendered.source_emissions = tuple(
            replace(claim, kind="text-slice") if (claim.target, claim.field) in covered else claim
            for claim in rendered.source_emissions
        )
    return rendered
