# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select equivalent coverage from captured facts and structural ancestor paths."""

from __future__ import annotations

from collections import defaultdict
from types import MappingProxyType

from computer_use.interaction.read.presentation.interface.coverage.model import (
    CoveragePlan,
    CoverageRequest,
    SharedCoverage,
)


_BOUNDARIES = frozenset(("window", "sheet", "menuBar", "menu"))


def _source_scope(request: CoverageRequest, ref: str) -> str | None:
    source = request.interface.source
    current: str | None = ref
    while current is not None:
        if source.native_nodes[current]["role"] in _BOUNDARIES:
            return current
        current = source.parents.get(current)
    return source.scopes.get(ref)


def _empty_initial(request: CoverageRequest, ref: str) -> bool:
    """Require the original and projected coverage to describe the same empty first page."""
    coverage = request.atoms.atoms[ref].coverage
    if (
        coverage.unexpanded_menu
        or type(coverage.start) is not int
        or coverage.start != 0
        or type(coverage.end) is not int
        or coverage.end != 0
        or coverage.read not in (None, 0)
        or type(coverage.total) is not int
        or coverage.total <= 0
        or coverage.next_offset is not None
        or coverage.omissions
        or not coverage.reason
    ):
        return False
    for node in (request.interface.source.native_nodes[ref], request.interface.projected_nodes[ref]):
        if (
            type(node.get("childrenStart")) is not int
            or node.get("childrenStart") != 0
            or type(node.get("childrenEnd")) is not int
            or node.get("childrenEnd") != 0
            or node.get("childCount") != coverage.total
            or node.get("children")
            or node.get("partialReason") != coverage.reason
            or node.get("childOmissions")
            or "nextChildOffset" in node
            or node.get("childrenRead", 0) != 0
        ):
            return False
    return True


def plan_coverage(request: CoverageRequest) -> CoveragePlan:
    """Place beneficial equal hints at their nearest common displayed ancestor.

    Cursor-bearing, omitted and noninitial pages stay attached to their own target.
    Source scopes and displayed window/menu scopes independently limit sharing.
    """
    document = request.interface
    paths: dict[str, tuple[str, ...]] = {}
    display_scopes: dict[str, str] = {}
    pending = [(key, (), key) for key in reversed(document.roots)]
    while pending:
        key, path, scope = pending.pop()
        block = document.blocks[key]
        if block.primary_ref is not None and document.projected_nodes[block.primary_ref]["role"] in _BOUNDARIES:
            scope = key
        current = (*path, key)
        for ref in block.source_refs:
            paths[ref], display_scopes[ref] = current, scope
        pending.extend((child, current, scope) for child in reversed(block.children))
    candidates: dict[tuple[str, str, int, str], list[str]] = defaultdict(list)
    for ref in document.target_refs:
        if not _empty_initial(request, ref):
            continue
        scope = _source_scope(request, ref)
        if scope is None:
            continue
        coverage = request.atoms.atoms[ref].coverage
        candidates[scope, display_scopes[ref], coverage.total, coverage.reason].append(ref)
    groups: dict[str, list[SharedCoverage]] = defaultdict(list)
    omitted: set[str] = set()
    for (scope, _, total, reason), refs in candidates.items():
        if len(refs) < 2:
            continue
        common = []
        for level in zip(*(paths[ref] for ref in refs)):
            if len(set(level)) != 1:
                break
            common.append(level[0])
        if not common:
            continue
        # This upper bound assumes no numeric range abbreviation in the shared
        # ref list. It keeps unusually long identifiers from increasing output.
        separate_cost = len(refs) * (42 + len(str(total)))
        shared_cost = 75 + len(str(total)) + len(reason) + sum(len(ref) + 2 for ref in refs)
        if shared_cost >= separate_cost:
            continue
        groups[common[-1]].append(SharedCoverage(tuple(refs), total, reason, scope))
        omitted.update(refs)
    return CoveragePlan(MappingProxyType({key: tuple(value) for key, value in groups.items()}), frozenset(omitted))
