# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Immutable local layout evidence shared by the geometric stages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from computer_use.interaction.read.presentation.spatial.alignment import (
    AlignmentAnalysis,
    LayoutItem,
)
from computer_use.interaction.read.presentation.interface.geometric.partition import RegionPlan


@dataclass(frozen=True)
class RegionScopeEvidence:
    """Retain a scope's complete spatial forest independently of visible containers.

    Attributes:
        forest: Ordered segment trees, including horizontal and vertical splits.
            An unpositioned original unit occupies a separate leaf in this forest.
        source_paths: Original source references mapped to their leaf path. The
            first index selects a forest root; later indices follow its children.
            All sources of an already composed unit share the unit's path.
    """

    forest: tuple[RegionPlan, ...]
    source_paths: Mapping[str, tuple[int, ...]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "forest", tuple(self.forest))
        object.__setattr__(self, "source_paths", MappingProxyType(dict(self.source_paths)))


@dataclass(frozen=True)
class RegionLayoutEvidence:
    """Immutable regional boundaries consumed by later local layout stages.

    Scope keys describe display units at the moment partitioning was selected.
    Membership uses real source references, so grouping those units into rows or
    records later preserves the same regional constraints.

    Attributes:
        scopes: Region-partition evidence indexed by original layout scope.
    """

    scopes: Mapping[str, RegionScopeEvidence]

    def __post_init__(self) -> None:
        object.__setattr__(self, "scopes", MappingProxyType(dict(self.scopes)))


@dataclass(frozen=True)
class GeometricScopePlan:
    """Comparable units and the constraints of one independently arranged scope.

    Attributes:
        items: Whole display units, each retaining its original block identity.
        alignment: Candidate row bands and repeated edge/centre alignments.
        before: Confirmed native precedence between all direct children.
        keep_together: Members whose established relationship forbids a region cut.
        source_order: Every direct child, including units without usable bounds.
        unplaced: Existing children whose geometry cannot locate them. They retain
            their original parent and separate the independently arranged segments.
        segments: Contiguous measured subsets on either side of unplaced children.
            A segment may be reordered internally; its members never cross an
            unplaced child. Alignment remains available across the entire scope
            as evidence, independently of this permission to reorder.
    """

    items: tuple[LayoutItem, ...]
    alignment: AlignmentAnalysis
    before: tuple[tuple[str, str], ...] = ()
    keep_together: tuple[tuple[str, ...], ...] = ()
    source_order: tuple[str, ...] = ()
    unplaced: tuple[str, ...] = ()
    segments: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class GeometricLink:
    """A required association or a geometric candidate between whole units.

    Attributes:
        members: Existing unit keys in the scope that supplied the evidence.
        kind: Original association, shared row band or repeated column anchor.
        strength: Whether a partition must preserve membership or may compare it.
    """

    members: tuple[str, ...]
    kind: Literal["association", "row", "column"]
    strength: Literal["required", "candidate"]


@dataclass(frozen=True)
class GeometricRelationScope:
    """One immutable relationship graph shared by partition and layout selection.

    Source order and segmented membership identify the units measured together.
    Confirmed precedence and associations constrain selection; row and column
    links describe alternative visual explanations without changing source facts.

    Attributes:
        source_order: Complete input unit sequence, including unpositioned members.
        before: Confirmed logical precedence between original units.
        keep_together: Native associations a candidate partition must preserve.
        segments: Independently reorderable runs separated by local barriers.
        row_members: Units sharing each observed row band.
        anchors: Repeated column alignments expressed as source unit membership.
        links: Required associations and optional geometric layout evidence.
    """

    source_order: tuple[str, ...]
    before: tuple[tuple[str, str], ...]
    keep_together: tuple[tuple[str, ...], ...]
    segments: tuple[tuple[str, ...], ...]
    row_members: tuple[tuple[str, ...], ...]
    anchors: tuple[tuple[str, ...], ...]
    links: tuple[GeometricLink, ...]


@dataclass(frozen=True)
class GeometricRelationGraph:
    """Request-local relationship evidence, indexed by source-bound layout scope.

    Attributes:
        scopes: Frozen relationship records keyed by their existing owner blocks.
    """

    scopes: Mapping[str, GeometricRelationScope]

    def __post_init__(self) -> None:
        object.__setattr__(self, "scopes", MappingProxyType(dict(self.scopes)))
