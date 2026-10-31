# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Share exact scalar prefixes while retaining every displayed field and target."""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
import json
from os.path import commonprefix
import re
from types import MappingProxyType
from typing import TYPE_CHECKING
import unicodedata

from computer_use.interaction.read.presentation.rendering.layout.chains import expand_tree_chains
from computer_use.interaction.read.presentation.rendering.layout.leaf_headers import expand_leaf_headers
from computer_use.interaction.read.presentation.rendering.layout.multi_column_records import expand_multi_column_records
from computer_use.interaction.read.presentation.rendering.layout.row_records import expand_row_records
from computer_use.interaction.read.presentation.rendering.layout.public_entries import (
    numbered_entries,
    unique_numbered_entries,
    unquoted_positions,
)
from computer_use.protocol.observation import NativeObservation

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame


@dataclass(frozen=True)
class NativeFieldSource:
    """Complete scalar fields and the original object scope supplying them.

    Attributes:
        source: Driver-owned token for the independently referenced object.
        scope: Native session, observation, and original root reference.
        name: Complete native name, when its read succeeded.
        description: Complete native description, independently read from name.
        url: Complete native URL, when its read succeeded.
    """

    source: str
    scope: tuple[str, str, str]
    name: str | None = None
    description: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class FieldPrefixReplacement:
    """One public suffix and the complete original field it represents.

    Attributes:
        ref: Unchanged displayed target reference.
        source: Original driver-owned object token.
        scope: Original session, observation and root.
        path: Exact native field path that supplied this scalar.
        field: Public field kind, Caption or URL.
        value: Complete original scalar, without normalization.
        prefix: Exact shared initial string.
        suffix: Exact remainder, including an empty remainder when applicable.
        quoted: Whether the original displayed scalar used JSON quotes.
        declaration: Complete public prefix declaration.
        replacement: Complete public suffix field at this target.
    """

    ref: str
    source: str
    scope: tuple[str, str, str]
    path: tuple[str, ...]
    field: str
    value: str
    prefix: str
    suffix: str
    quoted: bool
    declaration: str
    replacement: str


@dataclass(frozen=True)
class FieldPrefixLayout:
    """Full text layout and only the field replacements actually published."""

    lines: list[str]
    replacements: tuple[FieldPrefixReplacement, ...] = ()


def capture_field_prefix_sources(
    observation: NativeObservation,
    refs: Mapping[str, str],
    *,
    source_scopes: Mapping[str, str],
) -> Mapping[str, NativeFieldSource]:
    """Index complete native scalars for uniquely bound display references.

    Completeness belongs to each field. A partial node collection can still
    provide completed fields on the objects it returned; a failed field or
    object supplies no prefix evidence for that field.
    """
    counts = Counter(refs.values())
    issues: dict[str, list[tuple[str | int, ...]]] = defaultdict(list)
    for issue in observation.issues:
        if not issue.path or (issue.path == ("nodes",) and issue.state != "partial"):
            return MappingProxyType({})
        if len(issue.path) >= 2 and issue.path[0] == "nodes":
            issues[issue.path[1]].append(issue.path[2:])
    result: dict[str, NativeFieldSource] = {}
    for ref, token in refs.items():
        node = observation.nodes.get(token)
        if node is None or counts[token] != 1 or ref not in source_scopes:
            continue
        values = {}
        for name in ("name", "description", "url"):
            value = getattr(node, name)
            failed = any(not path or path[0] == name for path in issues[token])
            if type(value) is str and not failed:
                values[name] = value
        scope = (observation.session, observation.id, source_scopes[ref])
        result[ref] = NativeFieldSource(token, scope, **values)
    return MappingProxyType(result)


_NODE = re.compile(r"^(\t*)([0-9]+) (.+)\Z")
_DECLARATION = re.compile(r"^(Caption|URL) prefix \(prepend to (quoted )?\1 suffix\): (.+)\Z")
_SUFFIX = re.compile(r"(Caption|URL) suffix: ")
_MENUS = frozenset(("menu", "menuBar", "menuBarItem"))
_MINIMUM_SAVING = 32


def _plain(value: str) -> bool:
    return bool(value) and not any(
        unicodedata.category(char).startswith("C") or char in "\u2028\u2029" for char in value
    )


@dataclass(frozen=True)
class _Field:
    """Exact emitted scalar span with an independently complete native source."""

    ref: str
    source: NativeFieldSource
    path: tuple[str, ...]
    kind: str
    value: str
    quoted: bool
    line: int
    start: int
    end: int


def _scalar_span(body: str, ref: str, prefix: str, kind: str, encoded: str) -> tuple[int, int] | None:
    """Locate one complete generated scalar at its own public field boundary.

    A caption immediately follows its generated role/state prefix. A URL is a
    labelled field outside quoted provider values. Both the original record and
    current entry must independently satisfy this grammar; unrelated shared
    fields can therefore move without changing this scalar's identity.
    """
    field_start = len(ref + " " + prefix + " ")
    if not body.startswith(ref + " " + prefix + " "):
        return None
    if kind == "Caption":
        start = scalar = field_start
    else:
        positions = [
            position
            for position in unquoted_positions(body)
            if body.startswith("URL: ", position)
            and (position == field_start or body[max(0, position - 2) : position] == ", ")
        ]
        if len(positions) != 1:
            return None
        start = positions[0]
        scalar = start + len("URL: ")
    end = scalar + len(encoded)
    if body[scalar:end] != encoded or (end != len(body) and not body[end:].startswith(", ")):
        return None
    return start, end


def _fields(frame: LayoutFrame) -> tuple[int, list[_Field]] | None:
    """Prove current field spans against generated fields and native scalars.

    Existing layout grammars identify numbered entries independently of row
    indices. Each field is checked twice: in its original generated record, then
    at its current public position. This keeps preceding action or identifier
    sharing independent while preserving exact field ownership and boundaries.
    """
    if len(frame.roots) != 1 or frame.roots[0]["role"] in _MENUS:
        return None
    root = frame.roots[0]["ref"]
    nodes, parents, occurrences = {}, {}, Counter()
    pending = [(frame.roots[0], None)]
    while pending:
        node, parent = pending.pop()
        ref = node["ref"]
        nodes[ref], parents[ref] = node, parent
        occurrences[ref] += 1
        pending.extend((child, ref) for child in node["children"])
    records = {item.ref: item for item in frame.rendered_nodes}
    counts = Counter(item.ref for item in frame.rendered_nodes)
    entries = unique_numbered_entries(frame.lines)
    records_expanded = expand_multi_column_records(expand_row_records(frame.lines))
    expanded = expand_tree_chains(expand_leaf_headers(records_expanded))
    verified: dict[str, list[str]] = defaultdict(list)
    for line in expanded:
        if match := _NODE.fullmatch(line):
            verified[match[2]].append(line.lstrip("\t"))
    root_entry = entries.get(root)
    if root_entry is None or root not in records or counts[root] != 1:
        return None
    output: list[_Field] = []
    for ref, source in frame.field_prefix_sources.items():
        node, record, segment = nodes.get(ref), records.get(ref), entries.get(ref)
        prefix = frame.node_prefixes.get(ref)
        span = frame.node_line_ranges.get(ref)
        if (
            node is None
            or record is None
            or segment is None
            or not prefix
            or span is None
            or span[1] <= span[0]
            or occurrences[ref] != 1
            or counts[ref] != 1
            or source.scope[2] != root
            or record.parent != parents[ref]
            or node["role"] in _MENUS
        ):
            continue
        original = record.text.split("\n", 1)[0].lstrip("\t")
        current = segment.expanded
        if verified.get(ref) != [current]:
            continue
        fields = []
        value = node.get("name")
        matches = [
            name
            for name in ("name", "description")
            if getattr(source, name) is not None and getattr(source, name) == value
        ]
        if type(value) is str and len(matches) == 1:
            fields.append(("Caption", value, (matches[0],)))
        if source.url is not None and node.get("url") == source.url:
            fields.append(("URL", source.url, ("url",)))
        for kind, value, path in fields:
            if not _plain(value):
                continue
            encoded = frame.format_field(value)
            original_span = _scalar_span(original, ref, prefix, kind, encoded)
            current_span = _scalar_span(current, ref, prefix, kind, encoded)
            if original_span is None or current_span is None:
                continue
            quoted = encoded.startswith('"')
            if (json.loads(encoded) if quoted else encoded) != value:
                continue
            start, end = current_span
            removed = len(segment.heading) + 1 if segment.heading else 0
            local_start, local_end = start - removed, end - removed
            if local_start < len(ref) + 1:
                continue
            output.append(
                _Field(
                    ref,
                    source,
                    path,
                    kind,
                    value,
                    quoted,
                    segment.line,
                    segment.start + local_start,
                    segment.start + local_end,
                )
            )
    return root_entry.line, output


def _declaration(kind: str, prefix: str, quoted: bool) -> str:
    """Describe the exact reconstruction and original scalar quoting."""
    quoting = "quoted " if quoted else ""
    return f"{kind} prefix (prepend to {quoting}{kind} suffix): " + json.dumps(prefix, ensure_ascii=False)


def expand_field_prefixes(lines: list[str]) -> list[str]:
    """Restore complete fields from root-local public declarations and suffixes.

    Multiple Caption, URL and ID declarations may precede one numbered root.
    Prefixes apply only to explicit suffix markers on that root and descendants;
    a peer root or a new window/menu boundary ends the scope.

    Args:
        lines: Public lines, including each declaration and its numbered root.

    Returns:
        Original complete scalar fields with their exact quoting and layout.

    Raises:
        ValueError: A declaration, suffix or root boundary is ambiguous.
    """
    output: list[str] = []
    prefixes: dict[str, tuple[str, bool]] = {}
    indent = 0
    started = False
    entry_ranges: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for entry in numbered_entries(lines):
        entry_ranges[entry.line].append((entry.start, entry.start + len(entry.body)))
    for index, line in enumerate(lines):
        stripped = line.lstrip("\t")
        depth = len(line) - len(stripped)
        if re.match(r"^(?:Window [0-9]+:|Application menu[: (])", stripped):
            prefixes = {}
            started = False
        if match := _DECLARATION.fullmatch(stripped):
            if started:
                prefixes = {}
                started = False
            if (prefixes and indent != depth) or match[1] in prefixes:
                raise ValueError("ambiguous field prefix declarations")
            value = json.loads(match[3])
            if not isinstance(value, str) or not _plain(value):
                raise ValueError("invalid field prefix")
            prefixes[match[1]] = (value, bool(match[2]))
            indent = depth
            continue
        if stripped.startswith("ID prefix (prepend to ID suffix): "):
            output.append(line)
            continue
        node = _NODE.fullmatch(line)
        if prefixes and node:
            if started and depth <= indent:
                prefixes = {}
                started = False
            elif not started:
                if depth != indent:
                    raise ValueError("field prefix has no matching root")
                started = True
        if prefixes and started:
            outside = unquoted_positions(line)
            edits: list[tuple[int, int, str]] = []
            for match in _SUFFIX.finditer(line):
                within_entry = any(start <= match.start() < end for start, end in entry_ranges[index])
                if (
                    not within_entry
                    or match.start() not in outside
                    or (match.start() and line[match.start() - 1] != " ")
                ):
                    continue
                if match[1] not in prefixes:
                    raise ValueError("field suffix has no declaration")
                suffix, length = json.JSONDecoder().raw_decode(line[match.end() :])
                if not isinstance(suffix, str):
                    raise ValueError("field suffix must be a string")
                end = match.end() + length
                delimiters = (", ", " > ", " ; ", "; ", " | ")
                if end != len(line) and not any(line[end:].startswith(item) for item in delimiters):
                    raise ValueError("ambiguous field suffix boundary")
                prefix, quoted = prefixes[match[1]]
                value = prefix + suffix
                scalar = json.dumps(value, ensure_ascii=False) if quoted else value
                restored = ("URL: " if match[1] == "URL" else "") + scalar
                edits.append((match.start(), end, restored))
            for start, end, value in reversed(edits):
                line = line[:start] + value + line[end:]
        output.append(line)
    return output


@dataclass(frozen=True)
class _PrefixGroup:
    """One profitable same-field, same-quoting subset in a single root."""

    saving: int
    prefix: str
    quoted: bool
    members: tuple[_Field, ...]
    declaration: str


def _best_group(kind: str, members: list[_Field], frame: LayoutFrame, indent: str) -> _PrefixGroup | None:
    """Choose the highest byte-saving exact subset, leaving other values alone."""
    best: _PrefixGroup | None = None
    for quoted in (False, True):
        ordered = sorted((member for member in members if member.quoted == quoted), key=lambda item: item.value)
        values = [member.value for member in ordered]
        prefixes: set[str] = set()
        for left, right in zip(values, values[1:]):
            common = commonprefix((left, right))
            if common:
                prefixes.add(common)
            prefixes.update(common[: i + 1] for i, char in enumerate(common) if char in "./:_")
        for prefix in sorted(prefixes):
            start = bisect_left(values, prefix)
            matching: list[_Field] = []
            for member in ordered[start:]:
                if not member.value.startswith(prefix):
                    break
                matching.append(member)
            if len(matching) < 2:
                continue
            declaration = _declaration(kind, prefix, quoted)
            saving = -len((indent + declaration + "\n").encode("utf-8"))
            for member in matching:
                suffix = member.value[len(prefix) :]
                replacement = kind + " suffix: " + json.dumps(suffix, ensure_ascii=False)
                original = frame.lines[member.line][member.start : member.end]
                saving += len(original.encode("utf-8")) - len(replacement.encode("utf-8"))
            if saving >= _MINIMUM_SAVING and (best is None or saving > best.saving):
                best = _PrefixGroup(saving, prefix, quoted, tuple(matching), declaration)
    return best


def pack_field_prefixes(frame: LayoutFrame) -> FieldPrefixLayout:
    """Share profitable complete scalar prefixes in compact root output.

    Original structured nodes and fields remain untouched. Existing packed
    tables, leaf headings and chains keep their layout and independent targets.
    Native-source and generated-span evidence qualify candidates; a final
    public-only expansion must recover the complete incoming lines exactly.
    """
    if not frame.context.get("compact") or frame.context.get("subtreeRef"):
        return FieldPrefixLayout(frame.lines)
    evidence = _fields(frame)
    if evidence is None:
        return FieldPrefixLayout(frame.lines)
    root_index, members = evidence
    if len(members) < 2 or len({member.source.scope for member in members}) != 1:
        return FieldPrefixLayout(frame.lines)
    root_line = frame.lines[root_index]
    indent = root_line[: len(root_line) - len(root_line.lstrip("\t"))]
    groups: dict[str, list[_Field]] = defaultdict(list)
    for member in members:
        groups[member.kind].append(member)
    selected = [
        group for kind, items in groups.items() if (group := _best_group(kind, items, frame, indent)) is not None
    ]
    if not selected:
        return FieldPrefixLayout(frame.lines)

    output = list(frame.lines)
    edits: dict[int, list[tuple[int, int, str]]] = defaultdict(list)
    replacements: list[FieldPrefixReplacement] = []
    declarations: list[str] = []
    for group in selected:
        declarations.append(indent + group.declaration)
        for member in group.members:
            suffix = member.value[len(group.prefix) :]
            replacement = member.kind + " suffix: " + json.dumps(suffix, ensure_ascii=False)
            edits[member.line].append((member.start, member.end, replacement))
            replacements.append(
                FieldPrefixReplacement(
                    member.ref,
                    member.source.source,
                    member.source.scope,
                    member.path,
                    member.kind,
                    member.value,
                    group.prefix,
                    suffix,
                    group.quoted,
                    group.declaration,
                    replacement,
                )
            )
    for index, changes in edits.items():
        ordered = sorted(changes)
        if any(left[1] > right[0] for left, right in zip(ordered, ordered[1:])):
            return FieldPrefixLayout(frame.lines)
        for start, end, value in reversed(ordered):
            output[index] = output[index][:start] + value + output[index][end:]
    output[root_index:root_index] = declarations

    # This gate validates the composition with every preceding public layout.
    # It also rejects provider text that would make a suffix marker ambiguous.
    try:
        if expand_field_prefixes(output) != frame.lines:
            return FieldPrefixLayout(frame.lines)
    except (ValueError, TypeError, UnicodeError):
        return FieldPrefixLayout(frame.lines)
    return FieldPrefixLayout(output, tuple(replacements))
