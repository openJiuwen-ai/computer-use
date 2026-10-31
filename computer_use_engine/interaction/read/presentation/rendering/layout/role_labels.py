# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Share exact role spellings in one full root while preserving native roles."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
import json
import re
from types import MappingProxyType
from typing import TYPE_CHECKING
import unicodedata

from computer_use.interaction.read.presentation.rendering.layout.public_entries import (
    NumberedEntry,
    numbered_entries,
    unique_numbered_entries,
)
from computer_use.protocol.observation import NativeObservation

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame


@dataclass(frozen=True)
class NativeRoleSource:
    """Complete role spelling and the native object supplying it.

    Attributes:
        source: Driver-owned object token, distinct from its displayed number.
        scope: Native session, observation and original root reference.
        native_role: Platform-neutral role reported by the observation.
        display_label: Exact native role description, before presentation.
        provider_role_detail: Complete provider subtype supporting a code label.
    """

    source: str
    scope: tuple[str, str, str]
    native_role: str
    display_label: str
    provider_role_detail: str | None = None


@dataclass(frozen=True)
class RoleLabelReplacement:
    """One role slot represented by an exact public spelling declaration."""

    ref: str
    source: str
    scope: tuple[str, str, str]
    native_role: str
    display_label: str
    alias: str
    provider_role_detail: str | None
    declaration: str
    replacement: str
    path: tuple[str, ...] = ("role_description",)


@dataclass(frozen=True)
class RoleLabelLayout:
    """Final lines and the source destinations actually published in them."""

    lines: list[str]
    replacements: tuple[RoleLabelReplacement, ...] = ()


_DECLARATION = "Role labels (exact display labels): "
_NODE = re.compile(r"^(\t*)([0-9]+) (.+)\Z")
_MENUS = frozenset(("menu", "menuBar", "menuBarItem", "menuItem"))
_MINIMUM_SAVING = 8
# Aliases describe familiar controls. Provider-specific unknown roles stay literal.
_ALIASES = {
    name: name.replace("_", " ")
    for name in (
        "window",
        "dialog",
        "alert",
        "sheet",
        "popover",
        "split_group",
        "scroll_area",
        "toolbar",
        "separator",
        "button",
        "toggle_button",
        "checkbox",
        "radio_button",
        "switch",
        "menu_button",
        "popup_button",
        "combo_box",
        "text_field",
        "text_area",
        "slider",
        "spinner",
        "scrollbar",
        "progress_indicator",
        "heading",
        "image",
        "link",
        "document",
        "web_area",
        "list",
        "list_item",
        "tree",
        "tree_item",
        "row",
        "column",
        "cell",
        "table",
        "grid",
        "row_header",
        "column_header",
        "tab_group",
        "tab",
        "header_item",
        "value_indicator",
        "browser",
        "ruler",
        "handle",
        "color_well",
        "date_field",
        "time_field",
        "disclosure_triangle",
    )
}
_ALIASES["static_text"] = "text"


def _plain(value: str) -> bool:
    return bool(value) and not any(
        unicodedata.category(char).startswith("C") or char in "\u2028\u2029" for char in value
    )


def capture_role_sources(
    observation: NativeObservation,
    refs: Mapping[str, str],
    *,
    source_scopes: Mapping[str, str],
) -> Mapping[str, NativeRoleSource]:
    """Capture complete role facts for unique display references.

    Missing descendants do not invalidate completed role fields. Read issues
    on the object, role or role description do invalidate that object's proof.
    A code alias additionally requires the exact, independently read subtype.
    """
    counts = Counter(refs.values())
    issues: dict[str, list[tuple[str | int, ...]]] = defaultdict(list)
    for issue in observation.issues:
        if not issue.path or (issue.path == ("nodes",) and issue.state != "partial"):
            return MappingProxyType({})
        if len(issue.path) >= 2 and issue.path[0] == "nodes":
            issues[str(issue.path[1])].append(issue.path[2:])
    result: dict[str, NativeRoleSource] = {}
    for ref, token in refs.items():
        node = observation.nodes.get(token)
        if (
            node is None
            or counts[token] != 1
            or ref not in source_scopes
            or not isinstance(node.role, str)
            or not isinstance(node.role_description, str)
            or not _plain(node.role_description)
            or any(not path or path[0] in ("role", "role_description") for path in issues[token])
        ):
            continue
        subtype_fields = [
            (index, item.value) for index, item in enumerate(node.supplements) if item.key == "provider.role_detail"
        ]
        details = [value for _, value in subtype_fields]
        subtype_indices = {index for index, _ in subtype_fields}
        subtype_failed = any(
            path
            and path[0] == "supplements"
            and (len(path) == 1 or path[1] in subtype_indices or path[1] in ("provider.role_detail", "AXSubrole"))
            for path in issues[token]
        )
        code = (
            node.role == "group" and node.role_detail is None and details == ["AXCodeStyleGroup"] and not subtype_failed
        )
        result[ref] = NativeRoleSource(
            token,
            (observation.session, observation.id, source_scopes[ref]),
            str(node.role),
            node.role_description,
            "AXCodeStyleGroup" if code else None,
        )
    return MappingProxyType(result)


def _overlap(left: str, right: str) -> bool:
    """Reject equal or word-prefix labels that a public reader cannot separate."""
    return left == right or left.startswith(right + " ") or right.startswith(left + " ")


def _alias(source: NativeRoleSource) -> str | None:
    if source.native_role == "group":
        return "code" if source.provider_role_detail == "AXCodeStyleGroup" else None
    return _ALIASES.get(source.native_role)


def _starts(body: str, prefix: str) -> bool:
    return body == prefix or body.startswith(prefix + " ") or body.startswith(prefix + ", ")


@dataclass(frozen=True)
class _Slot:
    """Exact generated role position backed by one native role description."""

    entry: NumberedEntry
    source: NativeRoleSource
    alias: str


def _slots(frame: LayoutFrame) -> tuple[int, list[_Slot], tuple[str, ...]] | None:
    """Check identity, parent structure and generated roles before sharing text."""
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
    records = {record.ref: record for record in frame.rendered_nodes}
    counts = Counter(record.ref for record in frame.rendered_nodes)
    entries = unique_numbered_entries(frame.lines)
    root_entry = entries.get(root)
    if root_entry is None or root not in records or counts[root] != 1:
        return None
    sources = {ref: source for ref, source in frame.role_sources.items() if ref in nodes}
    # An embedded menu may retain a separate native root. Only the outer
    # root's roles are candidates; all sources still guard public collisions
    # and duplicate bindings below.
    if len({source.scope for source in sources.values() if source.scope[2] == root}) != 1:
        return None
    source_counts = Counter(source.source for source in sources.values())
    conflicts: dict[str, set[str | None]] = defaultdict(set)
    for source in sources.values():
        conflicts[source.display_label].add(_alias(source))
    output: list[_Slot] = []
    for ref, source in sources.items():
        node, record, entry = nodes[ref], records.get(ref), entries.get(ref)
        prefix, span = frame.node_prefixes.get(ref), frame.node_line_ranges.get(ref)
        alias = _alias(source)
        label = source.display_label
        if (
            record is None
            or entry is None
            or entry.heading
            or not entry.role_explicit
            or not prefix
            or span is None
            or span[1] <= span[0]
            or alias is None
            or node["role"] in _MENUS
            or occurrences[ref] != 1
            or counts[ref] != 1
            or source_counts[source.source] != 1
            or source.scope[2] != root
            or record.parent != parents[ref]
            or node.get("roleDescription") != label
            or not _plain(label)
            or frame.format_field(label) != label
            or len(conflicts[label]) != 1
            or (prefix != label and not prefix.startswith(label + " ("))
        ):
            continue
        original = record.text.split("\n", 1)[0].lstrip("\t")
        if not _starts(original, ref + " " + prefix) or not _starts(entry.body, ref + " " + prefix):
            continue
        output.append(_Slot(entry, source, alias))
    # Prefixes contain only generated role and state labels, never UI names.
    public_roles = tuple(prefix.split(" (", 1)[0] for prefix in frame.node_prefixes.values())
    return root_entry.line, output, public_roles


def _declaration(mapping: Mapping[str, str]) -> str:
    return _DECLARATION + json.dumps(dict(mapping), ensure_ascii=False)


def _parse_declaration(text: str) -> dict[str, str]:
    pairs: list[tuple[str, str]] = json.loads(text, object_pairs_hook=list)
    if not isinstance(pairs, list) or not pairs or any(not isinstance(item, tuple) or len(item) != 2 for item in pairs):
        raise ValueError("role labels must be a nonempty JSON object")
    labels: dict[str, str] = {}
    for alias, original in pairs:
        if (
            not isinstance(alias, str)
            or not re.fullmatch(r"[a-z]+(?: [a-z]+)*", alias)
            or not isinstance(original, str)
            or not _plain(original)
            or alias in labels
            or original in labels.values()
            or any(_overlap(alias, previous) for previous in labels)
        ):
            raise ValueError("ambiguous role label declaration")
        labels[alias] = original
    return labels


def expand_role_labels(lines: list[str]) -> list[str]:
    """Restore exact role spellings using only the public root-local dictionary.

    Explicit object entries in trees, chains and record cells use the mapping.
    Shared Elements headings and inherited row/cell roles keep their spelling.
    Window headings, peer roots and root-level summaries end the declaration.

    Args:
        lines: Full public lines containing any role declarations.

    Returns:
        The same layout with original role spellings and declarations removed.

    Raises:
        ValueError: A declaration or its following root is ambiguous.
    """
    by_line: dict[int, list[NumberedEntry]] = defaultdict(list)
    for entry in numbered_entries(lines):
        if entry.role_explicit and not entry.heading:
            by_line[entry.line].append(entry)
    output: list[str] = []
    mapping: dict[str, str] = {}
    indent, started = 0, False
    for index, line in enumerate(lines):
        stripped = line.lstrip("\t")
        depth = len(line) - len(stripped)
        if stripped.startswith(_DECLARATION):
            if mapping and not started:
                raise ValueError("multiple role declarations before one root")
            mapping = _parse_declaration(stripped[len(_DECLARATION) :])
            indent, started = depth, False
            continue
        if mapping:
            node = _NODE.fullmatch(line)
            if not started:
                if stripped.startswith(
                    ("ID prefix (prepend to ID suffix): ", "Caption prefix (prepend to ", "URL prefix (prepend to ")
                ):
                    output.append(line)
                    continue
                if node is None or depth != indent:
                    raise ValueError("role declaration has no matching root")
                started = True
            elif stripped and depth <= indent:
                mapping, started = {}, False
            if mapping:
                edits: list[tuple[int, int, str]] = []
                for entry in by_line[index]:
                    start = len(entry.ref) + 1
                    body = entry.body[start:]
                    matched = [alias for alias in mapping if _starts(body, alias)]
                    if len(matched) > 1:
                        raise ValueError("role entry has ambiguous aliases")
                    if matched:
                        alias = matched[0]
                        edits.append((entry.start + start, entry.start + start + len(alias), mapping[alias]))
                for start, end, original in reversed(sorted(edits)):
                    line = line[:start] + original + line[end:]
        output.append(line)
    if mapping and not started:
        raise ValueError("role declaration has no root")
    return output


def pack_role_labels(frame: LayoutFrame) -> RoleLabelLayout:
    """Share profitable exact display spellings without changing structured nodes.

    Native roles choose familiar labels; the dictionary retains the precise
    original display label. UTF-8 byte savings provide a dependency-free layout
    threshold. Public expansion must reproduce every incoming line exactly.
    """
    if not frame.context.get("compact") or frame.context.get("subtreeRef"):
        return RoleLabelLayout(frame.lines)
    evidence = _slots(frame)
    if evidence is None:
        return RoleLabelLayout(frame.lines)
    root_index, slots, public_roles = evidence
    root_line = frame.lines[root_index]
    indent = root_line[: len(root_line) - len(root_line.lstrip("\t"))]
    groups: dict[tuple[str, str], list[_Slot]] = defaultdict(list)
    for slot in slots:
        if slot.alias != slot.source.display_label and not any(_overlap(slot.alias, role) for role in public_roles):
            groups[(slot.alias, slot.source.display_label)].append(slot)
    candidates: list[tuple[int, str, str, list[_Slot]]] = []
    for (alias, label), members in groups.items():
        declaration = indent + _declaration({alias: label})
        saving = len(members) * (len(label.encode("utf-8")) - len(alias.encode("utf-8"))) - len(
            (declaration + "\n").encode("utf-8")
        )
        if saving >= _MINIMUM_SAVING:
            candidates.append((saving, alias, label, members))
    selected: list[_Slot] = []
    mapping: dict[str, str] = {}
    for _, alias, label, members in sorted(candidates, key=lambda item: (-item[0], item[1], item[2])):
        if alias in mapping or label in mapping.values() or any(_overlap(alias, chosen) for chosen in mapping):
            continue
        mapping[alias] = label
        selected.extend(members)
    if not selected:
        return RoleLabelLayout(frame.lines)
    mapping = dict(sorted(mapping.items()))
    declaration = _declaration(mapping)
    output = list(frame.lines)
    edits: dict[int, list[tuple[int, int, str]]] = defaultdict(list)
    replacements: list[RoleLabelReplacement] = []
    for slot in selected:
        source, entry = slot.source, slot.entry
        start = entry.start + len(entry.ref) + 1
        edits[entry.line].append((start, start + len(source.display_label), slot.alias))
        replacements.append(
            RoleLabelReplacement(
                entry.ref,
                source.source,
                source.scope,
                source.native_role,
                source.display_label,
                slot.alias,
                source.provider_role_detail,
                declaration,
                slot.alias,
            )
        )
    for line, changes in edits.items():
        ordered = sorted(changes)
        if any(left[1] > right[0] for left, right in zip(ordered, ordered[1:])):
            return RoleLabelLayout(frame.lines)
        for start, end, alias in reversed(ordered):
            output[line] = output[line][:start] + alias + output[line][end:]
    output.insert(root_index, indent + declaration)
    if len("\n".join(frame.lines).encode("utf-8")) - len("\n".join(output).encode("utf-8")) < _MINIMUM_SAVING:
        return RoleLabelLayout(frame.lines)
    try:
        if expand_role_labels(output) != frame.lines:
            return RoleLabelLayout(frame.lines)
    except (ValueError, TypeError, UnicodeError):
        return RoleLabelLayout(frame.lines)
    return RoleLabelLayout(output, tuple(replacements))
