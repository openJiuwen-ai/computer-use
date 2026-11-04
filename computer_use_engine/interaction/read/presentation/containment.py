# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compact repeated containment edges without changing their endpoints or kind."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import TypedDict
import re

from computer_use.interaction.read.facts.groups import reference_set
from computer_use.protocol.observation import Relation


_EDGE = re.compile(r"([0-9]+) contains ([0-9]+) \((shared|cycle|unread)\)")


class PublishedColumnRecord(TypedDict):
    """Detached serializable form of one verified table-column destination."""

    ref: str
    source: str
    table: str
    column: int
    targets: list[str]
    scope: list[str]
    property: str
    relation: dict[str, object]


@dataclass(frozen=True)
class PublishedTableColumn:
    """An original column whose ordered children are visible in one table.

    Attributes:
        ref: Independently numbered native column object.
        source: Original native column token; its binding remains unchanged.
        table: Number of the independently displayed table.
        column: One-based logical column in the public table.
        targets: Exact native children in their published row order.
        scope: Native session, observation and source root identities.
        property: Original relation key for the complete native children edge.
        relation: Original ordered native relation retained for destination accounting.
    """

    ref: str
    source: str
    table: str
    column: int
    targets: tuple[str, ...]
    scope: tuple[str, str, str]
    property: str
    relation: Relation

    def statement(self) -> str:
        """Describe the original edge using the actual public column order."""
        return f"{self.ref} contains Column {self.column} cells of table {self.table} (shared; displayed row order)"

    def to_record(self) -> PublishedColumnRecord:
        """Export detached JSON values for rendering diagnostics and replay."""
        return dict(
            ref=self.ref,
            source=self.source,
            table=self.table,
            column=self.column,
            targets=list(self.targets),
            scope=list(self.scope),
            property=self.property,
            relation=asdict(self.relation),
        )

    @classmethod
    def from_record(cls, value: PublishedColumnRecord) -> PublishedTableColumn:
        """Restore the typed internal contract from its detached render record."""
        return cls(
            value["ref"],
            value["source"],
            value["table"],
            value["column"],
            tuple(value["targets"]),
            tuple(value["scope"]),
            value["property"],
            Relation(**value["relation"]),
        )


def _ordered_refs(refs: Sequence[str]) -> str:
    """Use numeric ranges only when they retain the captured sequence."""
    numbers = [int(ref) for ref in refs]
    return reference_set(refs) if numbers == sorted(set(numbers)) else ", ".join(refs)


def containment_notes(
    annotations: Sequence[str],
    *,
    columns: Sequence[PublishedTableColumn] = (),
    covered: list[PublishedTableColumn] | None = None,
) -> list[str]:
    """Share identical child lists in generated containment diagnostics.

    Args:
        annotations: Generated graph notes using the current public references.
        columns: Exact ordered column edges already represented in this full view.
        covered: Optional destination ledger for references actually shortened here.

    Returns:
        Notes preserving every directed edge and its shared, cycle, or unread
        classification. Grouped parents each have exactly the listed children.
        Other notes retain their original position between edge blocks.
    """
    result: list[str] = []
    block: list[tuple[str, str, str, str]] = []
    counts = Counter(column.ref for column in columns)
    published = {column.ref: column for column in columns if counts[column.ref] == 1}

    def flush() -> None:
        if not block:
            return
        sources: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for parent, child, kind, original in block:
            sources.setdefault((parent, kind), []).append((child, original))
        groups: dict[tuple[str, tuple[str, ...]], list[tuple[str, list[str]]]] = {}
        for (parent, kind), edges in sources.items():
            targets = tuple(child for child, _ in edges)
            # Repeated edges can carry multiplicity; retain their literal notes.
            if len(set(targets)) != len(targets):
                result.extend(original for _, original in edges)
                continue
            groups.setdefault((kind, targets), []).append((parent, [line for _, line in edges]))
        for (kind, targets), entries in groups.items():
            originals = [line for _, lines in entries for line in lines]
            parents = [parent for parent, _ in entries]
            compact = (
                _ordered_refs(parents)
                + (" contains " if len(parents) == 1 else " contain ")
                + _ordered_refs(targets)
                + " ("
                + kind
                + ")"
            )
            previous = [compact] if len(compact) < len("\n".join(originals)) else originals
            column = published.get(parents[0]) if len(parents) == 1 else None
            if (
                column is not None
                and kind == "shared"
                and column.targets == targets
                and len(column.statement().encode("utf-8")) < len("\n".join(previous).encode("utf-8"))
            ):
                result.append(column.statement())
                if covered is not None:
                    covered.append(column)
            else:
                result.extend(previous)
        block.clear()

    for annotation in annotations:
        match = _EDGE.fullmatch(annotation)
        if match is None:
            flush()
            result.append(annotation)
        else:
            block.append((*match.groups(), annotation))
    flush()
    return result
