# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Sequence

    from computer_use.interaction.read.presentation.actions.catalogue import ActionCatalogue
    from computer_use.interaction.read.presentation.interface.model import InterfaceDocument
    from computer_use.interaction.read.presentation.records import FormatField, NodeView, RenderedTree
    from computer_use.interaction.read.presentation.rendering.layout.field_prefixes import (
        FieldPrefixReplacement,
        NativeFieldSource,
    )
    from computer_use.interaction.read.presentation.rendering.layout.local_field_suffixes import (
        LocalFieldSuffixReplacement,
    )
    from computer_use.interaction.read.presentation.rendering.layout.role_labels import (
        NativeRoleSource,
        RoleLabelReplacement,
    )
    from computer_use.interaction.read.presentation.rendering.layout.shared_field_descriptions import (
        SharedDescriptionReplacement,
    )
    from computer_use.interaction.read.presentation.rendering.layout.table_containment import NativeTableGrid
    from computer_use.interaction.read.presentation.rendering.layout.text_slices import TextSliceSources
    from computer_use.interaction.read.presentation.rendering.output import RenderedNode
    from computer_use.protocol.records import Record
from collections.abc import Callable
from dataclasses import dataclass, field
from types import MappingProxyType


@dataclass(frozen=True)
class ChildCoverageHint:
    """One generated zero-expansion hint with its unchanged source identity.

    Attributes:
        ref: Element whose direct children have not been expanded.
        total: Reported count of direct children.
        partial_reason: Exact source reason, used to separate different coverage states.
        scope: Original window, sheet, menu, or root containing the source.
        line_index: Position of the generated hint before text layout.
        text: Exact generated line, checked before replacing it with a shared declaration.
    """

    ref: str
    total: int
    partial_reason: str
    scope: str
    line_index: int
    text: str


@dataclass(frozen=True)
class UnexpandedMenuSummary:
    """Generated zero-expansion coverage retained beside the rendered menu.

    Attributes:
        entries: Exact menu references and their reported direct-child totals.
        text: Complete generated summary line that must remain in the output.
    """

    entries: tuple[tuple[str, int], ...]
    text: str


@dataclass(frozen=True)
class RowCollection:
    """Complete native row membership used only to lay out an outline as a table.

    Attributes:
        rows: Ordered row references reported by the original collection.
        children: Complete direct children of the same original collection.
        cells: Complete original direct children of each individually proven row.
    """

    rows: tuple[str, ...]
    children: tuple[str, ...]
    cells: Mapping[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "cells", MappingProxyType({ref: tuple(cells) for ref, cells in self.cells.items()}))


@dataclass(frozen=True)
class NativeIdentifier:
    """Complete identifier text and its exact native observation scope.

    Attributes:
        value: Unmodified identifier returned for the source object.
        source: Driver-owned object token; display references remain separate.
        scope: Session, observation, and original window or root reference.
    """

    value: str
    source: str
    scope: tuple[str, str, str]


@dataclass(frozen=True)
class TableLayoutEvidence:
    """A generated table introduction with its original object scope and rows."""

    table: str
    layout: tuple[tuple[str, str], ...]
    introduction: str
    heading: str
    rows: tuple[str, ...]


@dataclass(frozen=True)
class LayoutFrame:
    """Rendered lines and their pure layout continuation; source bindings stay fixed.

    Attributes:
        lines: Rendered observation lines, in their current display order.
        roots: Ordered roots in this observed or projected view.
        context: Native window or application context used to resolve menu sources.
        format_field: Pure formatter for one text field.
        records: Layout-stage diagnostics in execution order.
        finalize: Pure renderer continuation called after the line-layout stages complete.
        action_sources: Complete native catalogues keyed by their unchanged display references.
        child_hints: Proven zero-expansion hints available for compact full sharing.
        rendered_nodes: Unpacked node records retained for individual diff delivery.
        node_line_ranges: Generated line spans for each unpacked node, before layout.
        node_prefixes: Exact role/state prefixes generated for each unpacked node.
        unexpanded_menus: Generated menu coverage required by any shared menu prefix.
        row_collections: Proven native row membership for outline-only table layout.
        identifier_sources: Complete native identifier evidence for final ID packing.
        text_slice_sources: Native text fields and containment for exact shared text slices.
        table_sources: Complete native rows, cells and independent column child sequences.
        field_prefix_sources: Complete original captions and URLs in their native scopes.
        field_prefix_replacements: Exact fields represented by public prefix declarations.
        local_field_suffix_replacements: Complete URLs represented by their own literal descriptions.
        shared_description_replacements: Complete descriptions published for explicit same-root members.
        role_sources: Complete original role descriptions and their native scopes.
        role_label_replacements: Exact role slots represented by public spelling declarations.
        table_layouts: Exact table introductions and ordered rows produced by this layout.
        interface: Frozen source-bound layout choices, prepared before text generation.
        node_action_lines: Exact complete first lines and producer-generated alternatives
            without their own action slot; scoped action declarations retain that information.
    """

    lines: list[str]
    roots: Sequence[NodeView]
    context: Record
    format_field: FormatField
    records: tuple[Record, ...] = ()
    finalize: Callable[[list[str], list[Record]], RenderedTree] | None = None
    action_sources: Mapping[str, ActionCatalogue] = field(default_factory=dict)
    child_hints: tuple[ChildCoverageHint, ...] = ()
    rendered_nodes: tuple[RenderedNode, ...] = ()
    node_line_ranges: Mapping[str, tuple[int, int]] = field(default_factory=dict)
    node_prefixes: Mapping[str, str] = field(default_factory=dict)
    unexpanded_menus: tuple[UnexpandedMenuSummary, ...] = ()
    row_collections: Mapping[str, RowCollection] = field(default_factory=dict)
    identifier_sources: Mapping[str, NativeIdentifier] = field(default_factory=dict)
    text_slice_sources: TextSliceSources | None = None
    table_sources: tuple[NativeTableGrid, ...] = ()
    field_prefix_sources: Mapping[str, NativeFieldSource] = field(default_factory=dict)
    field_prefix_replacements: tuple[FieldPrefixReplacement, ...] = ()
    local_field_suffix_replacements: tuple[LocalFieldSuffixReplacement, ...] = ()
    shared_description_replacements: tuple[SharedDescriptionReplacement, ...] = ()
    role_sources: Mapping[str, NativeRoleSource] = field(default_factory=dict)
    role_label_replacements: tuple[RoleLabelReplacement, ...] = ()
    table_layouts: tuple[TableLayoutEvidence, ...] = ()
    interface: InterfaceDocument | None = None
    node_action_lines: Mapping[str, tuple[str, str]] = field(default_factory=dict)
