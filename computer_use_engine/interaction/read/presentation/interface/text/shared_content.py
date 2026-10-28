# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Serialize already-verified field references at the final text boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING
from computer_use.interaction.read.presentation.rendering.formatting import quoted

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.interface.sharing.text_slices.model import TextSlicePlan
    from computer_use.protocol.records import Record


def slice_statement(plan: TextSlicePlan) -> str:
    """Show original content excerpts and their same-observation recovery route."""
    anchor = "element " + plan.anchor.ref + "." + plan.anchor.slot.capitalize()
    if plan.mode == "same":
        return "same text as " + anchor
    excerpts = " … ".join(quoted(fragment.text) for fragment in plan.fragments)
    return f"excerpt {excerpts} [part of {anchor}; full: state.read_details(element={plan.source.ref})]"


def slice_destination(plan: TextSlicePlan) -> Record:
    """Retain both source identities and literal values for destination accounting."""
    return dict(
        ref=plan.source.ref,
        native=plan.source.native_token,
        field=plan.source.slot,
        path=list(plan.source.native_path),
        value=plan.source.value,
        anchor=plan.anchor.ref,
        anchorNative=plan.anchor.native_token,
        anchorField=plan.anchor.slot,
        anchorPath=list(plan.anchor.native_path),
        start=plan.start_utf16,
        end=plan.end_utf16,
        unit="utf16_code_unit",
        scope=list(plan.source.scope),
        bare=plan.source.atom_slot.label is None,
        mode=plan.mode,
        fragments=[dict(start=fragment.start, end=fragment.end, text=fragment.text) for fragment in plan.fragments],
        statement=slice_statement(plan),
    )
