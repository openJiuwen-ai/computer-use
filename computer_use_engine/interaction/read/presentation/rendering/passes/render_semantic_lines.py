# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Render semantic lines."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.actions.catalogue import capture_catalogues
from computer_use.interaction.read.presentation.actions.control_raises import capture_control_raises
from computer_use.interaction.read.presentation.actions.menu_mechanisms import capture_menu_mechanisms
from computer_use.interaction.read.presentation.model import PURE
from computer_use.interaction.read.presentation.rendering.formatting import rendered_actions, render_projection_lines
from computer_use.interaction.read.presentation.rendering.layout.field_prefixes import capture_field_prefix_sources
from computer_use.interaction.read.presentation.rendering.layout.identifier_prefixes import capture_identifiers
from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.interaction.read.presentation.rendering.layout.role_labels import capture_role_sources
from computer_use.interaction.read.presentation.rendering.layout.row_collections import capture_row_collections
from computer_use.interaction.read.presentation.rendering.layout.table_containment import capture_table_grids
from computer_use.interaction.read.presentation.rendering.layout.text_slices import capture_text_slice_sources
from computer_use.interaction.read.presentation.rendering.native_descriptions import (
    capture_common_role_aliases,
    capture_native_description_fields,
)
from computer_use.interaction.read.presentation.rendering.native_urls import capture_native_url_sources
from computer_use.interaction.read.presentation.selection_summary import selection_summary_plan
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.presentation.interface.disclosure.model import DisclosurePlan
    from computer_use.interaction.read.presentation.interface.model import InterfaceDocument
    from computer_use.interaction.read.presentation.ir import ProjectionResult
    from computer_use.interaction.read.presentation.model import BranchFacts
    from computer_use.pipeline.core import Control


def create(
    *, branch: BranchFacts, interface: InterfaceDocument | None = None, disclosure: DisclosurePlan | None = None
) -> Callable[[ProjectionResult, Control], LayoutFrame]:
    def render(projection: ProjectionResult, control: Control) -> LayoutFrame:
        """Render the branch's compressed source projection into semantic lines and field-emission
        records.
        """
        snapshot = branch.request.snapshot
        # Snapshot assembly always publishes the same source-derived summary plan.
        # Direct formatters have no such delivery promise and retain inline ranges.
        selections = (
            selection_summary_plan(snapshot.facts, snapshot.refs, roots=snapshot.roots)
            if branch.request.compact
            else None
        )
        # Compact observations retain independent identifiers in captured details.
        # Full native diagnostics and standalone formatters keep their inline IDs.
        options = dict(defer_identifiers=branch.request.compact)
        if disclosure is not None:
            options["disclosure_plan"] = disclosure
        menu_mechanisms = ()
        control_raises = ()
        if branch.request.compact:
            options["native_url_sources"] = capture_native_url_sources(snapshot, projection)
        # Explicit element reads opt out of whole-view description sharing.
        if (
            branch.request.compact
            and branch.request.public_api
            and branch.request.share_descriptions
            and not branch.context.get("subtreeRef")
        ):
            aliases = capture_common_role_aliases(snapshot, projection)
            options["native_role_aliases"] = aliases
            options["native_description_fields"] = {
                **(capture_native_description_fields(snapshot, projection) or {}),
                **{alias.fact: alias.description for alias in aliases.values()},
            }
            options["compact_multiline_values"] = True
            options["concise_menu_summary"] = True
            menu_mechanisms = capture_menu_mechanisms(snapshot, projection)
            options["menu_mechanisms"] = menu_mechanisms
            control_raises = capture_control_raises(snapshot, projection)
            options["control_raises"] = control_raises
        if selections is not None:
            options["selection_summaries"] = selections
        frame = render_projection_lines(
            projection["nativeNodes"][branch.root["ref"]], branch.context, projection, **options
        )
        if branch.request.compact:
            snapshot = branch.request.snapshot
            frame = replace(
                frame,
                action_sources=capture_catalogues(
                    snapshot.native,
                    snapshot.refs,
                    branch.native_nodes,
                    frame.roots,
                    [(owner, projection["nativeNodes"][ref]) for ref, owner in projection["foldedInto"].items()],
                    lambda node: rendered_actions(
                        node, True, menu_mechanisms=menu_mechanisms, control_raises=control_raises
                    ),
                    source_scopes=projection.source.scopes,
                    menu_mechanisms=menu_mechanisms,
                    control_raises=control_raises,
                ),
                row_collections=capture_row_collections(snapshot.native, snapshot.refs),
                identifier_sources=capture_identifiers(
                    snapshot.native, snapshot.refs, source_scopes=projection.source.scopes
                ),
                text_slice_sources=capture_text_slice_sources(
                    snapshot.native,
                    snapshot.refs,
                    source_scopes=projection.source.scopes,
                    value_fields=snapshot.value_fields,
                ),
                table_sources=capture_table_grids(
                    snapshot.native, snapshot.refs, source_scopes=projection.source.scopes
                ),
                field_prefix_sources=capture_field_prefix_sources(
                    snapshot.native, snapshot.refs, source_scopes=projection.source.scopes
                ),
                role_sources=capture_role_sources(
                    snapshot.native, snapshot.refs, source_scopes=projection.source.scopes
                ),
            )
        return replace(frame, interface=interface)

    return render


stage = Pass.factory(
    name="render_semantic_lines",
    factory=create,
    contract=Contract(
        dict,
        LayoutFrame,
        PURE,
        requires=("final forest", "source facts"),
        writes=("text", "actual field records"),
    ),
)
