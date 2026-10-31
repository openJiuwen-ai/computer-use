# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Reuse a literal description at the end of the same object's complete URL."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
import json
from typing import TYPE_CHECKING
import unicodedata

from computer_use.interaction.read.presentation.rendering.layout.public_entries import (
    unique_numbered_entries,
    unquoted_positions,
)

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame


@dataclass(frozen=True)
class LocalFieldSuffixReplacement:
    """One URL expressed by a literal prefix and its own description.

    Attributes:
        ref: Unchanged element reference owning both original fields.
        source: Original driver-owned object token.
        scope: Native session, observation and original root reference.
        path: Original URL field path.
        anchor_path: Independently acquired description field path.
        value: Complete original URL, without decoding or normalization.
        anchor_value: Complete description retained literally in this element.
        prefix: Exact initial part of the URL preceding the description.
        quoted: Whether the original URL scalar used JSON quotes.
        statement: Complete replacement URL field in the public output.
    """

    ref: str
    source: str
    scope: tuple[str, str, str]
    path: tuple[str, ...]
    anchor_path: tuple[str, ...]
    value: str
    anchor_value: str
    prefix: str
    quoted: bool
    statement: str


@dataclass(frozen=True)
class LocalFieldSuffixLayout:
    """Full lines and exact source destinations for local URL expressions."""

    lines: list[str]
    replacements: tuple[LocalFieldSuffixReplacement, ...] = ()


_MINIMUM_SAVING = 32
_MENUS = frozenset(("menu", "menuBar", "menuBarItem"))


def _plain(value: str) -> bool:
    """Accept nonempty scalars whose characters remain on one public line."""
    return bool(value) and not any(
        unicodedata.category(char).startswith("C") or char in "\u2028\u2029" for char in value
    )


def _field_starts(body: str, label: str, start: int, *, first_field: bool = False) -> list[int]:
    """Locate labelled fields outside quoted provider text."""
    return sorted(
        position
        for position in unquoted_positions(body)
        if position >= start
        and body.startswith(label + ": ", position)
        and (
            position == start
            or body[max(0, position - 2) : position] == ", "
            or (first_field and position > start and body[position - 1] == " " and ":" not in body[:position])
        )
    )


def _literal_span(body: str, prefix: str, label: str, encoded: str) -> tuple[int, int] | None:
    """Verify one exact generated field and its complete scalar boundary."""
    if not body.startswith(prefix):
        return None
    starts = _field_starts(body, label, len(prefix))
    if len(starts) != 1:
        return None
    start = starts[0]
    end = start + len(label) + 2 + len(encoded)
    if body[start:end] != label + ": " + encoded or (end != len(body) and not body[end:].startswith(", ")):
        return None
    return start, end


def _scalar(body: str, start: int) -> tuple[str, int]:
    """Read one literal JSON or plain scalar, without evaluating expressions."""
    if body[start : start + 1] == '"':
        value, size = json.JSONDecoder().raw_decode(body[start:])
        if type(value) is not str:
            raise ValueError("local description must be a string")
        return value, start + size
    end = body.find(", ", start)
    end = len(body) if end < 0 else end
    return body[start:end], end


def expand_local_field_suffixes(
    lines: list[str],
    format_field: Callable[[str], str],
) -> list[str]:
    """Restore local URLs from their public literal prefix and description.

    The only expression is ``URL: <JSON string> + Description``. Its fixed
    operand names the literal Description field on the same numbered line.
    No external reference, nested expression or executable text is evaluated.

    Args:
        lines: Public lines containing complete numbered element entries.
        format_field: Formatter used for the original scalar representation.

    Returns:
        Lines with each local expression replaced by its complete original URL.

    Raises:
        ValueError: A local expression has no unambiguous literal description.
    """
    result = list(lines)
    for entry in unique_numbered_entries(lines).values():
        if not entry.role_explicit or entry.heading or entry.body != lines[entry.line].lstrip("\t"):
            continue
        body = entry.body
        starts = _field_starts(body, "URL", len(entry.ref) + 1, first_field=True)
        if len(starts) != 1:
            continue
        start = starts[0]
        scalar_start = start + len("URL: ")
        if body[scalar_start : scalar_start + 1] != '"':
            continue
        prefix, size = json.JSONDecoder().raw_decode(body[scalar_start:])
        end = scalar_start + size
        if not body.startswith(" + Description", end):
            continue
        end += len(" + Description")
        if end != len(body) and not body[end:].startswith(", "):
            raise ValueError("local URL expression has an ambiguous end")
        if type(prefix) is not str or not _plain(prefix):
            raise ValueError("local URL prefix must be a nonempty literal string")
        descriptions = _field_starts(body, "Description", len(entry.ref) + 1, first_field=True)
        if len(descriptions) != 1:
            raise ValueError("local URL expression requires its own Description")
        description, description_end = _scalar(body, descriptions[0] + len("Description: "))
        if (
            not _plain(description)
            or description_end > start
            or (description_end != len(body) and not body[description_end:].startswith(", "))
        ):
            raise ValueError("local URL description must remain a preceding literal")
        replacement = "URL: " + format_field(prefix + description)
        offset = entry.start
        result[entry.line] = lines[entry.line][: offset + start] + replacement + lines[entry.line][offset + end :]
    return result


def pack_local_field_suffixes(frame: LayoutFrame) -> LocalFieldSuffixLayout:
    """Reuse exact local descriptions when complete URL wording becomes shorter.

    Native fields, generated scalar slots and the current public line must agree.
    Only compact full observations with one original root participate. Existing
    URL suffix declarations, table cells and packed chains keep their wording.
    Structured node records, bindings and their source accounting remain intact.
    """
    unchanged = LocalFieldSuffixLayout(frame.lines)
    if (
        not frame.context.get("compact")
        or frame.context.get("subtreeRef")
        or len(frame.roots) != 1
        or frame.roots[0]["role"] in _MENUS
    ):
        return unchanged
    root = frame.roots[0]["ref"]
    root_source = frame.field_prefix_sources.get(root)
    if root_source is None or root_source.scope[2] != root:
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
    # These entries are compared verbatim when final table-containment notes
    # confirm their public evidence. Keep that independent contract unchanged.
    table_entries = {grid.ref for grid in frame.table_sources}
    for grid in frame.table_sources:
        table_entries.update(column.ref for column in grid.columns)
        for row in grid.rows:
            table_entries.add(row.ref)
            table_entries.update(cell.ref for cell in row.cells)
        descendants = [nodes[grid.ref]] if grid.ref in nodes else []
        while descendants:
            item = descendants.pop()
            table_entries.add(item["ref"])
            descendants.extend(item["children"])
    result, replacements = list(frame.lines), []
    for ref, entry in unique_numbered_entries(frame.lines).items():
        source = frame.field_prefix_sources.get(ref)
        node, record = nodes.get(ref), records.get(ref)
        role_prefix = frame.node_prefixes.get(ref)
        span = frame.node_line_ranges.get(ref)
        if (
            source is None
            or node is None
            or record is None
            or not role_prefix
            or span is None
            or span[1] <= span[0]
            or occurrences[ref] != 1
            or record_counts[ref] != 1
            or source_counts[source.source] != 1
            or source.scope != root_source.scope
            or record.parent != parents[ref]
            or node["role"] in _MENUS
            or ref in table_entries
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
            or not url.endswith(description)
        ):
            continue
        prefix_value = url[: -len(description)]
        if not _plain(prefix_value):
            continue
        current_role = role_prefix
        if spelling := spellings.get(ref):
            if (
                spelling_counts[ref] != 1
                or spelling.source != source.source
                or spelling.scope != source.scope
                or (role_prefix != spelling.display_label and not role_prefix.startswith(spelling.display_label + " ("))
            ):
                continue
            current_role = spelling.alias + role_prefix[len(spelling.display_label) :]
        encoded_description, encoded_url = frame.format_field(description), frame.format_field(url)
        original = record.text.lstrip("\t")
        spans = []
        for body, current_prefix in ((original, role_prefix), (entry.body, current_role)):
            prefix = ref + " " + current_prefix + " "
            anchor = _literal_span(body, prefix, "Description", encoded_description)
            target = _literal_span(body, prefix, "URL", encoded_url)
            if anchor is None or target is None or anchor[1] > target[0]:
                break
            spans.append(target)
        if len(spans) != 2:
            continue
        statement = "URL: " + json.dumps(prefix_value, ensure_ascii=False) + " + Description"
        start, end = spans[1]
        if len(entry.body[start:end].encode("utf-8")) - len(statement.encode("utf-8")) < _MINIMUM_SAVING:
            continue
        offset = entry.start
        result[entry.line] = result[entry.line][: offset + start] + statement + result[entry.line][offset + end :]
        replacements.append(
            LocalFieldSuffixReplacement(
                ref,
                source.source,
                source.scope,
                ("url",),
                ("description",),
                url,
                description,
                prefix_value,
                encoded_url.startswith('"'),
                statement,
            )
        )
    if not replacements:
        return unchanged
    try:
        if expand_local_field_suffixes(result, frame.format_field) != frame.lines:
            return unchanged
    except (ValueError, TypeError, KeyError, UnicodeError):
        return unchanged
    return LocalFieldSuffixLayout(result, tuple(replacements))
