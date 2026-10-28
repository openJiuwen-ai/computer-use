# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure native-table planning over immutable fields, extents and source relationships."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from types import MappingProxyType

from computer_use.interaction.read.presentation.interface.collections.model import (
    CollectionDocument,
    CollectionEvidence,
    CollectionRejection,
    CollectionRequest,
    TableCellPlan,
    TableColumnPlan,
    TablePlan,
    TableRowPlan,
)
from computer_use.interaction.read.presentation.interface.collections.sharing import passive_cell_chain, share_prefixes
from computer_use.interaction.read.presentation.interface.collections.outline import select_outline_layout
from computer_use.protocol.observation import NativeNode


def _nonnegative(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


class _Source:
    """Narrow captured-fact queries used only by native collection planning."""

    def __init__(self, request: CollectionRequest) -> None:
        self.request = request
        counts = Counter(request.refs.values())
        self.aliases = {token: ref for ref, token in request.refs.items() if counts[token] == 1}
        self.nodes = request.interface.projected_nodes
        self.atoms = request.atoms.atoms

    def native(self, ref: str) -> NativeNode | None:
        token = self.request.refs.get(ref)
        return self.request.native.nodes.get(token) if self.aliases.get(token) == ref else None

    def clean(self, ref: str, *field: str) -> bool:
        """Reject only issues intersecting the particular native fact being consumed."""
        token = self.request.refs.get(ref)
        if token is None:
            return False
        path = ("nodes", token, *field)
        return not any(
            (issue.path[: len(path)] == path or path[: len(issue.path)] == issue.path)
            and not (issue.path == ("nodes",) and issue.state == "partial")
            for issue in self.request.native.issues
        )

    def relation(self, ref: str, kind: str, *, allow_prefix: bool = False) -> tuple[str, ...] | None:
        """Translate an exact edge, optionally retaining its confirmed initial page.

        A partial initial page establishes the listed members, while its total
        and continuation remain on the original atom. Other query failures and
        missing aliases still prevent use of the relationship.
        """
        native = self.native(ref)
        if native is None:
            return None
        choices = [(key, relation) for key, relation in native.relations.items() if relation.kind == kind]
        if len(choices) != 1:
            return None
        key, edge = choices[0]
        prefix = ("nodes", self.request.refs.get(ref), "relations", key)
        clean = self.clean(ref, "relations", key)
        if allow_prefix:
            clean = not any(
                (issue.path[: len(prefix)] == prefix or prefix[: len(issue.path)] == issue.path)
                and not (issue.path == ("nodes",) and issue.state == "partial")
                and not (issue.path == prefix and issue.state == "partial")
                for issue in self.request.native.issues
            )
        if (
            not clean
            or edge.evidence not in ("reported", "verified")
            or edge.enumeration != "enumerated"
            or edge.offset not in (None, 0)
            or (
                edge.total is None or edge.total < len(edge.targets)
                if allow_prefix
                else edge.total != len(edge.targets)
            )
            or edge.positions != tuple(range(len(edge.targets)))
            or len(set(edge.targets)) != len(edge.targets)
            or any(token not in self.aliases for token in edge.targets)
        ):
            return None
        return tuple(self.aliases[token] for token in edge.targets)

    def scope(self, ref: str) -> str | None:
        return self.request.interface.source.scopes.get(ref)

    def children_match(self, ref: str, *, allow_prefix: bool = False) -> bool:
        node = self.nodes[ref]
        children = tuple(node.get("children", ()))
        return (
            self.relation(ref, "children", allow_prefix=allow_prefix) == children
            and self.atoms[ref].children == children
        )

    def actions(self, refs: tuple[str, ...]) -> tuple[str, ...]:
        """Keep executable owners from atoms, including original folded text members."""
        return tuple(dict.fromkeys(action.source_ref for ref in refs for action in self.atoms[ref].actions))

    def descendants(self, ref: str) -> tuple[str, ...] | None:
        """Retain the complete projected subtree without changing native ownership."""
        result: list[str] = []
        seen = {ref}
        pending = [(child, ref) for child in reversed(self.atoms[ref].children)]
        while pending:
            child, parent = pending.pop()
            if (
                child in seen
                or child not in self.atoms
                or child not in self.nodes
                or self.atoms[child].parent != parent
                or self.scope(child) != self.scope(ref)
                or self.request.interface.projected_parents.get(child) != parent
            ):
                return None
            seen.add(child)
            result.append(child)
            pending.extend((member, child) for member in reversed(self.atoms[child].children))
        return tuple(result)

    def unread_structure(self, ref: str) -> bool:
        """Find an explicit collection gap in the row's retained local subtree.

        A timeout or unexpanded native object permits formatting the already
        complete preceding rows. Conflicting extents and ordinary attribute
        errors retain the general tree route for the collection.
        """
        tokens = set()
        seen = set()
        pending = [ref]
        while pending:
            current = pending.pop()
            if current in seen or current not in self.atoms:
                continue
            seen.add(current)
            tokens.add(self.request.refs.get(current))
            pending.extend(self.atoms[current].children)
        return any(
            issue.state in ("timeout", "not_read")
            and len(issue.path) >= 2
            and issue.path[0] == "nodes"
            and issue.path[1] in tokens
            and (len(issue.path) == 2 or issue.path[2] in ("role", "structure", "relations"))
            for issue in self.request.native.issues
        )


def _cell(source: _Source, ref: str, row_ref: str) -> TableCellPlan | None:
    native = source.native(ref)
    if (
        native is None
        or native.role not in ("cell", "column_header")
        or not source.clean(ref, "role")
        or native.structure is None
        or source.atoms[ref].parent != row_ref
        or source.scope(ref) != source.scope(row_ref)
    ):
        return None
    structure = native.structure
    fields = ("row_index", "column_index", "row_span", "column_span")
    if any(not source.clean(ref, "structure", field) for field in fields):
        return None
    row, column = _nonnegative(structure.row_index), _nonnegative(structure.column_index)
    row_span, column_span = _nonnegative(structure.row_span), _nonnegative(structure.column_span)
    if row is None or column is None or not row_span or not column_span:
        return None
    projected = source.nodes[ref]
    for key, position, span in (("rowRange", row, row_span), ("columnRange", column, column_span)):
        value = projected.get(key)
        if not isinstance(value, Mapping) or value.get("location") != position or value.get("length") != span:
            return None
    descendants = source.descendants(ref)
    if descendants is None:
        return None
    members = (ref, *descendants)
    return TableCellPlan(
        ref,
        row,
        column,
        row_span,
        column_span,
        source.atoms[ref].children,
        descendants,
        source.actions(members),
        any(source.atoms[member].coverage.partial for member in members),
        passive_cell_chain(source.request, ref, descendants),
    )


def _row(source: _Source, ref: str, table_ref: str) -> TableRowPlan | None:
    native = source.native(ref)
    if (
        native is None
        or native.role != "row"
        or not source.clean(ref, "role")
        or source.atoms[ref].parent != table_ref
        or source.scope(ref) != source.scope(table_ref)
        or not source.children_match(ref)
    ):
        return None
    children = source.atoms[ref].children
    if not children or any(child not in source.atoms for child in children):
        return None
    cells = tuple(_cell(source, child, ref) for child in children)
    if any(cell is None for cell in cells):
        return None
    if len({cell.row for cell in cells}) != 1 or tuple(cell.column for cell in cells) != tuple(
        sorted(cell.column for cell in cells)
    ):
        return None
    structure = native.structure
    index = _nonnegative(structure.index) if structure and source.clean(ref, "structure", "index") else None
    level = _nonnegative(structure.level) if structure and source.clean(ref, "structure", "level") else None
    return TableRowPlan(
        ref,
        index,
        level,
        cells,
        source.actions((ref,)),
        source.atoms[ref].coverage.partial or any(cell.partial for cell in cells),
    )


def _compact_cells(source: _Source, rows: tuple[TableRowPlan, ...]) -> bool:
    """Select compact rows only for an aligned matrix of short single-cell branches."""
    columns = tuple(cell.column for cell in rows[0].cells)
    if any(
        tuple(cell.column for cell in row.cells) != columns
        or any(cell.row_span != 1 or cell.column_span != 1 for cell in row.cells)
        for row in rows
    ):
        return False
    for row in rows:
        total = 0
        for cell in row.cells:
            if cell.partial or len(cell.descendant_refs) > 6:
                return False
            size = 0
            for ref in (cell.ref, *cell.descendant_refs):
                atom = source.atoms[ref]
                for slot in atom.slots:
                    if isinstance(slot.value, str):
                        if "\n" in slot.value or "\r" in slot.value:
                            return False
                        size += len(slot.value)
                size += len(atom.actions) * 24 + len(atom.states) * 12 + 12
            if size > 320:
                return False
            total += size
        if total > 640:
            return False
    return True


def _plan(source: _Source, evidence: CollectionEvidence, ref: str) -> tuple[TablePlan | None, str]:
    native = source.native(ref)
    if (
        native is None
        or native.role not in ("table", "tree")
        or not source.clean(ref, "role")
        or source.scope(ref) is None
    ):
        return None, "logical table identity or source scope is unavailable"
    if not source.children_match(ref, allow_prefix=True):
        return None, "captured native children differ from the projected table"
    children = source.atoms[ref].children
    row_refs = tuple(child for child in children if source.nodes[child]["role"] == "row")
    if not row_refs:
        return None, "no captured native rows"
    if native.role == "tree":
        proof = evidence.outlines.get(ref)
        # A collection-wide node limit does not contradict separately complete
        # local rows/children. The shared legacy proof rejects that broad issue;
        # retain the same exact-edge proof using this request's local issue scope.
        local_rows = source.relation(ref, "rows", allow_prefix=True)
        original_rows = tuple(
            child for child in children if source.native(child) is not None and source.native(child).role == "row"
        )
        if (
            (proof is None and (local_rows != row_refs or original_rows != row_refs))
            or proof is not None
            and (proof.rows != row_refs or proof.children != children)
        ):
            return None, "outline row membership is unconfirmed"
    complete_rows = []
    for row_ref in row_refs:
        row = _row(source, row_ref, ref)
        if row is None:
            if not source.unread_structure(row_ref):
                return None, "a captured row has conflicting or unavailable cell structure"
            break
        if row.partial and complete_rows and not complete_rows[0].partial:
            break
        complete_rows.append(row)
    rows = tuple(complete_rows)
    if not rows:
        return None, "no complete initial row has available cell structure"
    planned_row_refs = tuple(row.ref for row in rows)
    starts = tuple(row.cells[0].row for row in rows)
    if len(set(starts)) != len(starts):
        return None, "multiple original rows claim the same logical row"
    consumed = tuple(
        member
        for row in rows
        for member in (row.ref, *(member for cell in row.cells for member in (cell.ref, *cell.descendant_refs)))
    )
    if len(set(consumed)) != len(consumed):
        return None, "row content has repeated projected identities"
    cells = [cell for row in rows for cell in row.cells]
    for index, left in enumerate(cells):
        for right in cells[index + 1 :]:
            if (
                left.row < right.row + right.row_span
                and right.row < left.row + left.row_span
                and left.column < right.column + right.column_span
                and right.column < left.column + left.column_span
            ):
                return None, "reported logical cell extents overlap"
    grids = [grid for grid in evidence.grids if grid.ref == ref]
    columns = []
    if len(grids) == 1 and tuple(row.ref for row in grids[0].rows) == planned_row_refs:
        for column in grids[0].columns:
            if column.ref in source.atoms:
                columns.append(
                    TableColumnPlan(column.ref, column.column - 1, column.targets, source.actions((column.ref,)))
                )
    structure = native.structure
    row_count = _nonnegative(structure.row_count) if structure and source.clean(ref, "structure", "row_count") else None
    column_count = (
        _nonnegative(structure.column_count) if structure and source.clean(ref, "structure", "column_count") else None
    )
    if (
        row_count is not None
        and any(cell.row + cell.row_span > row_count for cell in cells)
        or column_count is not None
        and any(cell.column + cell.column_span > column_count for cell in cells)
    ):
        return None, "cell extents exceed the reported logical table size"
    partial = (
        source.atoms[ref].coverage.partial
        or len(rows) < len(row_refs)
        or any(row.partial for row in rows)
        or row_count is not None
        and len(rows) < row_count
    )
    plan = TablePlan(
        ref,
        "outline" if native.role == "tree" else "table",
        (source.request.native.session, source.request.native.id, source.scope(ref)),
        rows,
        tuple(columns),
        tuple(child for child in children if child not in planned_row_refs),
        consumed,
        source.actions((ref, *consumed)),
        row_count,
        column_count,
        partial,
        "table" if not any(row.partial for row in rows) and _compact_cells(source, rows) else "rows",
    )
    return select_outline_layout(share_prefixes(plan, source.request), source.request), ""


def plan_collections(evidence: CollectionEvidence) -> CollectionDocument:
    """Build deterministic native table plans, keeping unproven collections as local trees."""
    source = _Source(evidence.request)
    tables = {}
    rejected = []
    visual_patterns = {
        plan.owner_ref for plan in evidence.request.interface.geometric_patterns if plan.kind in ("grid", "cards")
    }
    for ref in evidence.request.interface.target_refs:
        if ref in visual_patterns:
            continue
        if source.nodes[ref]["role"] not in ("table", "outline") or ref not in source.atoms:
            continue
        plan, reason = _plan(source, evidence, ref)
        if plan is None:
            rejected.append(CollectionRejection(ref, reason))
        else:
            tables[ref] = plan
    return CollectionDocument(MappingProxyType(tables), tuple(rejected))
