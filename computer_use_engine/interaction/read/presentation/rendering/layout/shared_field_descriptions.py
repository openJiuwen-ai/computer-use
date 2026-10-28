# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Share identical complete descriptions beside their unchanged URL targets."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
import json
import re
from typing import TYPE_CHECKING
import unicodedata

from computer_use.interaction.read.presentation.rendering.layout.public_entries import (
    numbered_entries,
    unique_numbered_entries,
    unquoted_positions,
)

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.rendering.layout.field_prefixes import NativeFieldSource
    from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame


@dataclass(frozen=True)
class SharedDescriptionReplacement:
    """One complete description published in its root's explicit member list.

    Attributes:
        ref: Unchanged reference of the object owning the description.
        source: Original driver-owned object token.
        scope: Native session, observation and original root reference.
        path: Original description field path.
        value: Complete original description without normalization.
        quoted: Whether the original scalar used the formatter's JSON quotes.
        root: Public root containing the declaration and member.
        declaration: Exact shared declaration, excluding indentation.
        original: Removed labelled scalar and its following comma and space.
        anchor: Remaining URL or URL suffix field immediately after that scalar.
    """

    ref: str
    source: str
    scope: tuple[str, str, str]
    path: tuple[str, ...]
    value: str
    quoted: bool
    root: str
    declaration: str
    original: str
    anchor: str


@dataclass(frozen=True)
class SharedDescriptionLayout:
    """Full lines and exact public destinations of shared description fields."""

    lines: list[str]
    replacements: tuple[SharedDescriptionReplacement, ...] = ()


@dataclass(frozen=True)
class _Member:
    ref: str
    source: NativeFieldSource
    line: int
    start: int
    original: str
    anchor: str


_DECLARATION = re.compile(r"^(\t*)Description \[elements ((?:0|[1-9][0-9]*)(?:, (?:0|[1-9][0-9]*))*)\]: (.+)\Z")
_MENUS = frozenset(("menu", "menuBar", "menuBarItem"))
_MINIMUM_SAVING = 32


def _plain(value: str) -> bool:
    """Accept complete nonempty scalars that occupy one public line."""
    return bool(value) and not any(
        unicodedata.category(char).startswith("C") or char in "\u2028\u2029" for char in value
    )


def _starts(body: str, label: str, minimum: int) -> list[int]:
    """Find labelled scalar starts outside quoted UI text."""
    return sorted(
        position
        for position in unquoted_positions(body)
        if position >= minimum
        and body.startswith(label + ": ", position)
        and (
            position == minimum
            or body[max(0, position - 2) : position] == ", "
            or (position > minimum and body[position - 1] == " " and ":" not in body[:position])
        )
    )


def _adjacent_slot(body: str, prefix: str, description: str, url_field: str) -> int | None:
    """Prove the generated description immediately precedes one literal URL."""
    if not body.startswith(prefix):
        return None
    starts = _starts(body, "Description", len(prefix))
    urls = [(position, label) for label in ("URL", "URL suffix") for position in _starts(body, label, len(prefix))]
    if len(starts) != 1 or len(urls) != 1:
        return None
    start, (url_start, _) = starts[0], urls[0]
    original = "Description: " + description + ", "
    end = url_start + len(url_field)
    if (
        body[start:url_start] != original
        or body[url_start:end] != url_field
        or (end != len(body) and not body[end:].startswith(", "))
    ):
        return None
    return start


def _url_start(body: str, ref: str) -> tuple[int, str]:
    """Locate an independent literal URL using only the public numbered line."""
    minimum = len(ref) + 1
    urls = [(position, label) for label in ("URL", "URL suffix") for position in _starts(body, label, minimum)]
    if len(urls) != 1 or _starts(body, "Description", minimum):
        raise ValueError("shared Description requires one URL field and no remaining Description")
    start, label = urls[0]
    scalar_start = start + len(label) + 2
    if body[scalar_start : scalar_start + 1] == '"':
        value, size = json.JSONDecoder().raw_decode(body[scalar_start:])
        end = scalar_start + size
        if type(value) is not str or (end != len(body) and not body[end:].startswith(", ")):
            raise ValueError("shared Description requires a literal URL scalar")
    else:
        end = body.find(", ", scalar_start)
        end = len(body) if end < 0 else end
        value = body[scalar_start:end]
        if label == "URL suffix" or not value or " + Description" in value:
            raise ValueError("shared Description cannot supply a URL expression")
    return start, label


def expand_shared_descriptions(
    lines: list[str],
    format_field: Callable[[str], str],
) -> list[str]:
    """Restore each listed description at its preserved adjacent URL field.

    A declaration sits one indentation level inside a numbered root. Only its
    later ordinary descendants may be members. The JSON literal supplies the
    complete value; the unique URL field supplies the insertion point. The
    original quote style follows the same deterministic scalar formatter.

    Args:
        lines: Public lines with explicit Description member declarations.
        format_field: Original deterministic scalar formatter.

    Returns:
        Lines with each shared field restored and its declaration removed.

    Raises:
        ValueError: A declaration or member lacks an unambiguous public scope.
    """
    result = list(lines)
    hidden: set[int] = set()
    used: set[tuple[int, str]] = set()
    for index, line in enumerate(lines):
        match = _DECLARATION.fullmatch(line)
        if match is None:
            continue
        depth = len(match[1]) - 1
        if depth < 0:
            raise ValueError("shared Description must be inside a numbered root")
        # Numbered-entry parsing distinguishes real members from table cells,
        # inherited headings and chain components without inspecting UI values.
        preceding = numbered_entries(lines[:index])
        roots = [
            entry
            for entry in preceding
            if entry.body == lines[entry.line].lstrip("\t")
            and entry.role_explicit
            and not entry.heading
            and len(lines[entry.line]) - len(entry.body) == depth
        ]
        if not roots:
            raise ValueError("shared Description has no preceding root")
        root = max(roots, key=lambda entry: entry.line)
        end = next(
            (
                position
                for position in range(index + 1, len(lines))
                if lines[position].strip() and len(lines[position]) - len(lines[position].lstrip("\t")) <= depth
            ),
            len(lines),
        )
        refs = tuple(match[2].split(", "))
        value = json.loads(match[3])
        if len(refs) < 2 or len(set(refs)) != len(refs) or type(value) is not str or not _plain(value):
            raise ValueError("shared Description requires unique members and one complete string")
        entries = unique_numbered_entries(lines[root.line + 1 : end])
        original = "Description: " + format_field(value) + ", "
        for ref in refs:
            entry = entries.get(ref)
            if entry is None:
                raise ValueError("shared Description member is missing or duplicated")
            position = root.line + 1 + entry.line
            if (
                (root.line, ref) in used
                or ref == root.ref
                or position <= index
                or not entry.role_explicit
                or entry.heading
                or entry.body != lines[position].lstrip("\t")
            ):
                raise ValueError("shared Description requires later ordinary members of its own root")
            used.add((root.line, ref))
            start, _ = _url_start(entry.body, ref)
            offset = entry.start
            result[position] = lines[position][: offset + start] + original + lines[position][offset + start :]
        hidden.add(index)
    return [line for index, line in enumerate(result) if index not in hidden]


def pack_shared_descriptions(frame: LayoutFrame) -> SharedDescriptionLayout:
    """Publish repeated native descriptions once beside their unchanged members.

    Original and current scalar slots must agree with complete native fields.
    Only ordinary descendants of one root participate; independent URL suffix
    declarations remain in place. Native objects, structured nodes, actions and
    previously published table evidence are unchanged.
    """
    unchanged = SharedDescriptionLayout(frame.lines)
    if (
        not frame.context.get("compact")
        or frame.context.get("subtreeRef")
        or len(frame.roots) != 1
        or frame.roots[0]["role"] in _MENUS
    ):
        return unchanged
    root = frame.roots[0]["ref"]
    root_source = frame.field_prefix_sources.get(root)
    entries = unique_numbered_entries(frame.lines)
    root_entry = entries.get(root)
    if (
        root_source is None
        or root_source.scope[2] != root
        or root_entry is None
        or not root_entry.role_explicit
        or root_entry.heading
        or root_entry.body != frame.lines[root_entry.line].lstrip("\t")
    ):
        return unchanged
    nodes, parents, occurrences = {}, {}, Counter()
    pending = [(frame.roots[0], None)]
    while pending:
        node, parent = pending.pop()
        ref = node["ref"]
        nodes[ref], parents[ref] = node, parent
        occurrences[ref] += 1
        pending.extend((child, ref) for child in node["children"])
    records = {record.ref: record for record in frame.rendered_nodes}
    record_counts = Counter(record.ref for record in frame.rendered_nodes)
    source_counts = Counter(source.source for source in frame.field_prefix_sources.values())
    spellings = {item.ref: item for item in frame.role_label_replacements}
    spelling_counts = Counter(item.ref for item in frame.role_label_replacements)
    urls = {item.ref: item for item in frame.field_prefix_replacements if item.path == ("url",)}
    url_counts = Counter(item.ref for item in frame.field_prefix_replacements if item.path == ("url",))
    excluded = {item.ref for item in frame.local_field_suffix_replacements}
    for grid in frame.table_sources:
        excluded.add(grid.ref)
        excluded.update(column.ref for column in grid.columns)
        for row in grid.rows:
            excluded.add(row.ref)
            excluded.update(cell.ref for cell in row.cells)
        descendants = [nodes[grid.ref]] if grid.ref in nodes else []
        while descendants:
            item = descendants.pop()
            excluded.add(item["ref"])
            descendants.extend(item["children"])
    groups: dict[str, list[_Member]] = defaultdict(list)
    for ref, entry in entries.items():
        source, node, record = frame.field_prefix_sources.get(ref), nodes.get(ref), records.get(ref)
        prefix, span = frame.node_prefixes.get(ref), frame.node_line_ranges.get(ref)
        if (
            ref == root
            or ref in excluded
            or source is None
            or node is None
            or record is None
            or not prefix
            or span is None
            or span[1] <= span[0]
            or occurrences[ref] != 1
            or record_counts[ref] != 1
            or source_counts[source.source] != 1
            or source.scope != root_source.scope
            or record.parent != parents[ref]
            or node["role"] in _MENUS
            or entry.line <= root_entry.line
            or not entry.role_explicit
            or entry.heading
            or entry.body != frame.lines[entry.line].lstrip("\t")
            or "\n" in record.text
        ):
            continue
        description, url = source.description, source.url
        if (
            description is None
            or url is None
            or not _plain(description)
            or not _plain(url)
            or node.get("description") != description
            or node.get("url") != url
        ):
            continue
        current_prefix = prefix
        if spelling := spellings.get(ref):
            if (
                spelling_counts[ref] != 1
                or spelling.source != source.source
                or spelling.scope != source.scope
                or (prefix != spelling.display_label and not prefix.startswith(spelling.display_label + " ("))
            ):
                continue
            current_prefix = spelling.alias + prefix[len(spelling.display_label) :]
        original_url = "URL: " + frame.format_field(url)
        current_url = original_url
        if shared_url := urls.get(ref):
            if (
                url_counts[ref] != 1
                or shared_url.field != "URL"
                or shared_url.value != url
                or shared_url.source != source.source
                or shared_url.scope != source.scope
            ):
                continue
            current_url = shared_url.replacement
        encoded = frame.format_field(description)
        original = record.text.lstrip("\t")
        if _adjacent_slot(original, ref + " " + prefix + " ", encoded, original_url) is None:
            continue
        start = _adjacent_slot(entry.body, ref + " " + current_prefix + " ", encoded, current_url)
        if start is None:
            continue
        groups[description].append(
            _Member(
                ref,
                source,
                entry.line,
                entry.start + start,
                "Description: " + encoded + ", ",
                "URL suffix" if current_url.startswith("URL suffix: ") else "URL",
            )
        )
    result, declarations, replacements = list(frame.lines), [], []
    root_indent = frame.lines[root_entry.line][: root_entry.start]
    for value, members in groups.items():
        members.sort(key=lambda item: item.line)
        if len(members) < 2:
            continue
        declaration = (
            "Description [elements "
            + ", ".join(item.ref for item in members)
            + "]: "
            + json.dumps(value, ensure_ascii=False)
        )
        public_line = root_indent + "\t" + declaration
        saving = sum(len(item.original.encode("utf-8")) for item in members) - len((public_line + "\n").encode("utf-8"))
        if saving < _MINIMUM_SAVING:
            continue
        declarations.append(public_line)
        for item in members:
            result[item.line] = result[item.line][: item.start] + result[item.line][item.start + len(item.original) :]
            replacements.append(
                SharedDescriptionReplacement(
                    item.ref,
                    item.source.source,
                    item.source.scope,
                    ("description",),
                    value,
                    frame.format_field(value).startswith('"'),
                    root,
                    declaration,
                    item.original,
                    item.anchor,
                )
            )
    if not replacements:
        return unchanged
    result[root_entry.line + 1 : root_entry.line + 1] = declarations
    try:
        if expand_shared_descriptions(result, frame.format_field) != frame.lines:
            return unchanged
    except (ValueError, TypeError, KeyError, UnicodeError):
        return unchanged
    return SharedDescriptionLayout(result, tuple(replacements))
