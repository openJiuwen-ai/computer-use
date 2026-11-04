# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Local native precedence constraints and deterministic topological ordering."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import heapq

from computer_use.interaction.read.presentation.interface.model import InterfaceRelation


def stable_order(keys: Sequence[str], edges: Sequence[tuple[str, str]]) -> tuple[str, ...] | None:
    """Apply precedence edges, retaining captured order for unconstrained members.

    Returns:
        A complete order, or None when the supplied constraints contain a cycle.
    """
    positions = {key: index for index, key in enumerate(keys)}
    outgoing: dict[str, set[str]] = {key: set() for key in keys}
    incoming = dict.fromkeys(keys, 0)
    for before, after in edges:
        if before == after or after in outgoing[before]:
            continue
        outgoing[before].add(after)
        incoming[after] += 1
    ready = [(positions[key], key) for key in keys if incoming[key] == 0]
    heapq.heapify(ready)
    result = []
    while ready:
        _, key = heapq.heappop(ready)
        result.append(key)
        for target in outgoing[key]:
            incoming[target] -= 1
            if incoming[target] == 0:
                heapq.heappush(ready, (positions[target], target))
    return tuple(result) if len(result) == len(keys) else None


def native_edges(
    keys: Sequence[str], owners: Mapping[str, str], relations: Sequence[InterfaceRelation]
) -> tuple[tuple[str, str], ...] | None:
    """Resolve complete native flow edges between existing local reading units.

    Every target follows or precedes the declaring object. Targets in the same
    relation keep their captured relative order unless another edge constrains
    them. Partial, candidate or cyclic declarations preserve the original order.
    """
    edges: list[tuple[str, str]] = []
    for relation in relations:
        if relation.kind not in ("flows_to", "flows_from") or relation.source not in owners:
            continue
        if not relation.complete or not relation.confirmed:
            return None
        for target in relation.targets:
            if target not in owners:
                continue
            before, after = owners[relation.source], owners[target]
            if relation.kind == "flows_from":
                before, after = after, before
            if before != after and (before, after) not in edges:
                edges.append((before, after))
    result = tuple(edges)
    return result if stable_order(keys, result) is not None else None


def add_compatible_edge(edges: dict[str, set[str]], before: str, after: str) -> None:
    """Add a weaker geometry edge only when it preserves all prior constraints."""
    pending = [after]
    seen = set()
    while pending:
        key = pending.pop()
        if key == before:
            return
        if key not in seen:
            seen.add(key)
            pending.extend(edges[key])
    edges[before].add(after)
