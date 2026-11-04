# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Prove ordered column containment against the table actually published."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.containment import PublishedTableColumn
from computer_use.interaction.read.presentation.rendering.layout.leaf_headers import expand_leaf_headers
from computer_use.interaction.read.presentation.rendering.layout.multi_column_records import expand_multi_column_records
from computer_use.interaction.read.presentation.rendering.layout.record_lines import point_position
from computer_use.interaction.read.presentation.rendering.layout.field_prefixes import expand_field_prefixes
from computer_use.interaction.read.presentation.rendering.layout.role_labels import expand_role_labels
from computer_use.interaction.read.presentation.rendering.layout.input_fields import expand_input_fields
from computer_use.interaction.read.presentation.rendering.layout.table_entries import expand_table_entries
from computer_use.protocol.observation import NativeObservation, Relation

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame


@dataclass(frozen=True)
class TableCell:
    """An original cell and its explicitly reported one-based logical position."""

    ref: str
    row: int
    column: int


@dataclass(frozen=True)
class TableRow:
    """One complete native row, with its original ordered cells."""

    ref: str
    cells: tuple[TableCell, ...]


@dataclass(frozen=True)
class NativeTableGrid:
    """Local native relationships used solely to describe a published table.

    Attributes:
        ref: Table or outline reference in the original observation.
        scope: Session, observation and original window/root identities.
        rows: Complete native row order and each row's complete cell order.
        columns: Independent native columns with exactly matching child sequences.
    """

    ref: str
    scope: tuple[str, str, str]
    rows: tuple[TableRow, ...]
    columns: tuple[PublishedTableColumn, ...]


def capture_table_grids(
    observation: NativeObservation, refs: Mapping[str, str], *, source_scopes: Mapping[str, str]
) -> tuple[NativeTableGrid, ...]:
    """Capture complete local table edges without requiring an expanded ancestor chain.

    Every consumed role, position and relation must be complete. A collection-wide
    node limit does not invalidate a separately complete local edge. Explicit edge
    positions, totals and cell spans are required; incomplete tables retain lists.
    """
    counts = Counter(refs.values())
    aliases = {token: ref for ref, token in refs.items() if counts[token] == 1}
    issues: dict[str, list[tuple[str | int, ...]]] = {}
    for issue in observation.issues:
        if not issue.path or issue.path == ("nodes",) and issue.state != "partial":
            return ()
        if len(issue.path) >= 2 and issue.path[0] == "nodes":
            issues.setdefault(issue.path[1], []).append(issue.path[2:])

    def clean(token: str, path: tuple[str, ...]) -> bool:
        return not any(path[: len(issue)] == issue or issue[: len(path)] == path for issue in issues.get(token, ()))

    def relation(token: str, kind: str) -> Relation | None:
        node = observation.nodes.get(token)
        if node is None:
            return None
        choices = [(key, value) for key, value in node.relations.items() if value.kind == kind]
        if len(choices) != 1:
            return None
        key, value = choices[0]
        if (
            not clean(token, ("relations", key))
            or value.evidence not in ("reported", "verified")
            or value.enumeration != "enumerated"
            or value.offset not in (None, 0)
            or type(value.total) is not int
            or value.total != len(value.targets)
            or value.positions != tuple(range(len(value.targets)))
            or len(set(value.targets)) != len(value.targets)
            or any(target not in aliases or target not in observation.nodes for target in value.targets)
        ):
            return None
        return value

    def scope(token: str) -> str | None:
        return source_scopes.get(aliases.get(token))

    def parent(token: str, expected: str) -> bool:
        value = relation(token, "parent")
        return value is not None and value.targets == (expected,)

    result = []
    for token, table in observation.nodes.items():
        if (
            table.role not in ("table", "tree")
            or token not in aliases
            or scope(token) is None
            or not clean(token, ("role",))
        ):
            continue
        children, rows = relation(token, "children"), relation(token, "rows")
        if children is None or rows is None or not rows.targets:
            continue
        if tuple(child for child in children.targets if observation.nodes[child].role == "row") != rows.targets:
            continue
        native_rows: list[TableRow] = []
        for row_token in rows.targets:
            row = observation.nodes[row_token]
            members = relation(row_token, "children")
            if (
                row.role != "row"
                or not clean(row_token, ("role",))
                or scope(row_token) != scope(token)
                or not parent(row_token, token)
                or members is None
                or not members.targets
            ):
                break
            cells: list[TableCell] = []
            for position, cell_token in enumerate(members.targets):
                cell = observation.nodes[cell_token]
                structure = cell.structure
                fields = ("row_index", "column_index", "row_span", "column_span")
                if (
                    cell.role not in ("cell", "column_header")
                    or not clean(cell_token, ("role",))
                    or structure is None
                    or any(not clean(cell_token, ("structure", key)) for key in fields)
                    or type(structure.row_index) is not int
                    or structure.row_index < 0
                    or type(structure.column_index) is not int
                    or structure.column_index != position
                    or type(structure.row_span) is not int
                    or structure.row_span != 1
                    or type(structure.column_span) is not int
                    or structure.column_span != 1
                    or scope(cell_token) != scope(token)
                    or not parent(cell_token, row_token)
                ):
                    break
                cells.append(TableCell(aliases[cell_token], structure.row_index + 1, position + 1))
            if len(cells) != len(members.targets) or len({cell.row for cell in cells}) != 1:
                break
            native_rows.append(TableRow(aliases[row_token], tuple(cells)))
        if (
            len(native_rows) != len(rows.targets)
            or len({len(row.cells) for row in native_rows}) != 1
            or len({row.cells[0].row for row in native_rows}) != len(native_rows)
        ):
            continue
        root_scope = (observation.session, observation.id, scope(token))
        columns = []
        for column_token in children.targets:
            column = observation.nodes[column_token]
            structure = column.structure
            if (
                column.role != "column"
                or not clean(column_token, ("role",))
                or structure is None
                or not clean(column_token, ("structure", "index"))
                or type(structure.index) is not int
                or not 0 <= structure.index < len(native_rows[0].cells)
                or scope(column_token) != scope(token)
                or not parent(column_token, token)
            ):
                continue
            members = relation(column_token, "children")
            targets = tuple(row.cells[structure.index].ref for row in native_rows)
            if members is None or tuple(aliases[child] for child in members.targets) != targets:
                continue
            columns.append(
                PublishedTableColumn(
                    aliases[column_token],
                    column_token,
                    aliases[token],
                    structure.index + 1,
                    targets,
                    root_scope,
                    next(key for key, value in column.relations.items() if value.kind == "children"),
                    members,
                )
            )
        if columns:
            result.append(NativeTableGrid(aliases[token], root_scope, tuple(native_rows), tuple(columns)))
    return tuple(result)


_NODE = re.compile(r"^(\t*)([0-9]+) (.+)\Z")


def published_table_columns(frame: LayoutFrame) -> tuple[PublishedTableColumn, ...]:
    """Confirm that all row/cell references are present in the final public layout.

    Existing table and leaf-heading syntax is expanded only for verification. Each
    generated table, row, cell and independent column must still match its original
    record, parent, scope and order. No element is inferred from its label or position.
    """
    if (
        not frame.context.get("compact")
        or frame.context.get("subtreeRef")
        or not frame.table_sources
        or len(frame.roots) != 1
        or frame.roots[0]["role"] in ("menu", "menuBar", "menuBarItem")
    ):
        return ()
    # Restore later public spellings in reverse layout order before comparing
    # the delivered table with the original, unchanged node records.
    try:
        field_lines = expand_field_prefixes(expand_role_labels(expand_input_fields(expand_table_entries(frame.lines))))
    except ValueError:
        return ()
    expanded = expand_leaf_headers(expand_multi_column_records(field_lines))
    if expanded == expand_leaf_headers(field_lines):
        return ()
    nodes, parents, occurrences = {}, {}, Counter()
    pending = [(frame.roots[0], None)]
    while pending:
        node, parent = pending.pop()
        ref = node["ref"]
        nodes[ref], parents[ref] = node, parent
        occurrences[ref] += 1
        pending.extend((child, ref) for child in reversed(node["children"]))
    records = {record.ref: record for record in frame.rendered_nodes}
    counts = Counter(record.ref for record in frame.rendered_nodes)
    public: dict[str, list[tuple[int, str]]] = {}
    original_lines = set(field_lines)
    for index, line in enumerate(expanded):
        match = _NODE.fullmatch(line)
        if match is not None:
            public.setdefault(match[2], []).append((index, line))

    def displayed(ref: str, parent: str | None, layout: tuple[tuple[str, str], ...]) -> tuple[int, str] | None:
        record, node = records.get(ref), nodes.get(ref)
        matches = public.get(ref, ())
        if (
            record is None
            or node is None
            or counts[ref] != 1
            or occurrences[ref] != 1
            or len(matches) != 1
            or record.parent != parent
            or parents[ref] != parent
            or record.layout != layout
            or record.partial
            or record.hints
            or record.related
            or "\n" in record.text
            or matches[0][1].lstrip("\t") != record.text.lstrip("\t")
        ):
            return None
        return matches[0]

    published = []
    for source in frame.table_sources:
        table, record = nodes.get(source.ref), records.get(source.ref)
        if (
            table is None
            or record is None
            or table["role"] not in ("table", "outline")
            or source.scope[2] != frame.roots[0]["ref"]
        ):
            continue
        title = displayed(source.ref, record.parent, record.layout)
        row_refs = tuple(child["ref"] for child in table["children"] if child["role"] == "row")
        if title is None or row_refs != tuple(row.ref for row in source.rows):
            continue
        indent = len(title[1]) - len(title[1].lstrip("\t"))
        previous = title[0]
        packed_rows = 0
        for row in source.rows:
            line = displayed(row.ref, source.ref, record.layout)
            node = nodes.get(row.ref)
            if (
                line is None
                or node is None
                or node["role"] != "row"
                or line[0] <= previous
                or len(line[1]) - len(line[1].lstrip("\t")) != indent + 1
                or tuple(child["ref"] for child in node["children"]) != tuple(cell.ref for cell in row.cells)
            ):
                break
            packed_rows += line[1] not in original_lines
            previous = line[0]
            for cell in row.cells:
                item = displayed(cell.ref, row.ref, record.layout)
                child = nodes.get(cell.ref)
                coordinates = f"Row: {cell.row}, Column: {cell.column}"
                if (
                    item is None
                    or child is None
                    or child["role"] not in ("cell", "columnHeader")
                    or item[0] <= previous
                    or len(item[1]) - len(item[1].lstrip("\t")) != indent + 2
                    or point_position(child, "rowRange") != cell.row
                    or point_position(child, "columnRange") != cell.column
                    or not re.search(r"(?: |, )" + re.escape(coordinates) + r"(?:, |$)", item[1])
                ):
                    break
                previous = item[0]
            else:
                continue
            break
        else:
            if packed_rows >= 3:
                for column in source.columns:
                    item = displayed(column.ref, source.ref, record.layout)
                    if (
                        item is not None
                        and nodes[column.ref]["role"] == "column"
                        and len(item[1]) - len(item[1].lstrip("\t")) == indent + 1
                    ):
                        published.append(column)
    return tuple(published)
