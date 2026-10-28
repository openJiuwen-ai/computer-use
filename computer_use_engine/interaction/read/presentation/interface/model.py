# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Structured interface blocks with source identities separate from layout identities."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, TYPE_CHECKING

from computer_use.interaction.read.presentation.ir import PresentedValue, ProjectionInput, ProjectionResult

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.interface.atoms.model import AtomDocument
    from computer_use.interaction.read.presentation.interface.geometric.source import PreparedGeometricSources
    from computer_use.interaction.read.presentation.interface.geometric.model import (
        GeometricScopePlan,
        RegionLayoutEvidence,
        GeometricRelationGraph,
    )
    from computer_use.interaction.read.presentation.interface.geometric.patterns.model import PatternPlan
    from computer_use.interaction.read.presentation.preparation import CapturedSnapshot

BlockKind = Literal["tree", "window", "region", "control", "field", "record", "collection", "table", "content"]
LayoutKind = Literal[
    "tree",
    "inline",
    "rows",
    "records",
    "record-blocks",
    "record-path",
    "fields",
    "chain",
    "compact-list",
    "visual-row",
    "visual-grid",
    "field-inline",
    "control-unit",
    "source-only",
    "compound-control",
]


@dataclass(frozen=True)
class InterfaceBlock:
    """One reading unit, retaining every real member under its original reference.

    Attributes:
        key: Internal layout identity; it is never an element identifier.
        kind: Semantic reading unit chosen from source facts or explicit visual evidence.
        primary_ref: Real object naming this block, or None for a synthetic grouping.
        source_refs: Real sources directly represented here, excluding child blocks.
        children: Ordered block identities whose sources remain independently addressable.
        layout: Chosen structure to format after delivery selection.
        origin: Native structure or geometry-backed display grouping.
        label_ref: Separately retained label used to explain a field's visual association.
        position: Existing relative region annotation, never raw coordinates.
        shared_prefix_ref: A real member supplying a shared role/state prefix.
        columns: Column descriptions for a record, or internal slots for a visual grid.
            A visual grid retains full independent units without inventing headings.
        column_prefix_refs: Per-column real source supplying a uniform role/state prefix.
        region_role: Source-supported navigation or toolbar role; other regions stay neutral.
        field_target_ref: Original input target of a composed field, retained by its own child block.
        member_roles: Source references and their evidenced roles within a composed field.
        heading_style: Plain complete label caption or the full label atom.
        visual_unit: Evidence identifies one visual control composed of several sources.
        column_slots: Increasing logical columns paired with the row's real children.
            None retains an independent item between its observed neighbours.
        column_alignments: Geometry-supported alignment of each compact display column.
    """

    key: str
    kind: BlockKind
    primary_ref: str | None
    source_refs: tuple[str, ...]
    children: tuple[str, ...] = ()
    layout: LayoutKind = "tree"
    origin: Literal["native", "visual"] = "native"
    label_ref: str | None = None
    position: str | None = None
    shared_prefix_ref: str | None = None
    columns: tuple[str, ...] = ()
    column_prefix_refs: tuple[str | None, ...] = ()
    region_role: Literal["navigation", "toolbar", "region"] | None = None
    field_target_ref: str | None = None
    member_roles: tuple[tuple[str, Literal["label", "control", "help", "error", "content"]], ...] = ()
    heading_style: Literal["atom", "caption"] = "atom"
    visual_unit: bool = False
    column_slots: tuple[int | None, ...] = ()
    column_alignments: tuple[Literal["left", "right", "center"], ...] = ()


@dataclass(frozen=True)
class NodeEvidence:
    """Local facts constraining interface organization for one projected source."""

    scope: str
    independent: bool
    boundary: bool
    incomplete: bool
    hidden: bool


@dataclass(frozen=True)
class InterfaceRelation:
    """One captured relation with unchanged order, identity and coverage.

    Attributes:
        source: Original display reference of the object declaring this relation.
        property: Native property name retained for source accounting.
        kind: Normalized relation kind; presentation never infers a stronger relation.
        targets: Captured target references in their original order.
        complete: Whether the complete ordered relation was acquired.
        confirmed: Whether acquisition confirmed the relation rather than proposing it.
    """

    source: str
    property: str
    kind: str
    targets: tuple[str, ...]
    complete: bool
    confirmed: bool


@dataclass(frozen=True)
class InterfaceRequest:
    """A source projection, typed content candidates and whole-view display policy.

    Captured facts provide original parent relationships and folded source fields.
    Their query scope can exceed the members permitted in this projection's layout.
    """

    projection: ProjectionResult
    enabled: bool = True
    relations: tuple[InterfaceRelation, ...] = ()
    atoms: AtomDocument | None = None
    captured: CapturedSnapshot | None = None


@dataclass(frozen=True)
class InterfaceDraft:
    """Private structured state passed between the pure interface stages.

    The source and projected fields are immutable. Stages replace only their block
    organization or evidence map; no native service or session state enters this IR.
    """

    source: ProjectionInput
    projected_nodes: Mapping[str, Mapping[str, Any]]
    projected_parents: Mapping[str, str]
    order: tuple[str, ...]
    root_refs: tuple[str, ...]
    provenance: Mapping[tuple[str, str], PresentedValue]
    folded_refs: Mapping[str, str]
    enabled: bool
    evidence: Mapping[str, NodeEvidence] = field(default_factory=lambda: MappingProxyType({}))
    roots: tuple[str, ...] = ()
    blocks: Mapping[str, InterfaceBlock] = field(default_factory=lambda: MappingProxyType({}))
    decisions: tuple[str, ...] = ()
    relations: tuple[InterfaceRelation, ...] = ()
    atoms: AtomDocument | None = None
    geometric_sources: PreparedGeometricSources | None = None
    geometric_alignment: Mapping[str, GeometricScopePlan] = field(default_factory=lambda: MappingProxyType({}))
    geometric_regions: RegionLayoutEvidence | None = None
    geometric_relations: GeometricRelationGraph | None = None
    geometric_patterns: tuple[PatternPlan, ...] = ()


@dataclass(frozen=True)
class InterfaceDocument:
    """Frozen layout to render while retaining the exact projected and native facts.

    Attributes:
        source: Original immutable source graph and geometry evidence.
        projected_nodes: Per-reference projected fields, with children stored as references.
        projected_parents: Original projected hierarchy before interface organization.
        provenance: Existing field composition evidence, unchanged by the interface stages.
        roots: Ordered top-level block keys.
        blocks: Immutable reading blocks; synthetic keys cannot be used for input.
        ref_locations: Unique main presentation block for each projected source.
        target_refs: Every projected real reference, in its original projection order.
        root_refs: Original projection roots, used for scope and diagnostic fallback.
        folded_refs: Existing source folds from compression, without new execution aliases.
        decisions: Applied structural transformations for diagnostics and acceptance.
        geometric_sources: Complete captured facts and original ancestry for the
            final layout, with its allowed member range kept separate.
        geometric_patterns: Selected local collection layouts. Native row/cell
            identities and unselected or unread members remain explicit.
    """

    source: ProjectionInput
    projected_nodes: Mapping[str, Mapping[str, Any]]
    projected_parents: Mapping[str, str]
    provenance: Mapping[tuple[str, str], PresentedValue]
    roots: tuple[str, ...]
    blocks: Mapping[str, InterfaceBlock]
    ref_locations: Mapping[str, str]
    target_refs: tuple[str, ...]
    root_refs: tuple[str, ...]
    folded_refs: Mapping[str, str]
    decisions: tuple[str, ...] = ()
    format_version: str = "interface-2"
    relations: tuple[InterfaceRelation, ...] = ()
    geometric_sources: PreparedGeometricSources | None = None
    geometric_patterns: tuple[PatternPlan, ...] = ()
