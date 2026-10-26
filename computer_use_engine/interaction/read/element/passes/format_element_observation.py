"""Resolve the captured window title before semantic rendering."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.element.model import ElementReadPlan
from computer_use.interaction.read.presentation.content import json_value, template
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.publication.model import PublicationRequest
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def header(plan: ElementReadPlan, control: Control) -> ElementReadPlan:
    """Create the full-subtree publication header using the element's observed window and app."""
    target, state = plan.selected["binding"], plan.state
    title = next(
        (item["title"] for item in (state.directory or {}).get("surfaces", []) if item["surface"] == target.surface),
        None,
    )
    context = template("Window: {}", json_value(title)) if title is not None else "Application UI root"
    prefix = DisplayDocument.join(
        (template("{}, App: {}.", context, json_value(state.name)), "Tree: full (element subtree)", "")
    )
    return replace(
        plan,
        publication=PublicationRequest(plan.request.owner, state, prefix, plan.tree, target, full=True, view="subtree"),
    )


stage = Pass.define(
    name="format_element_observation",
    function=header,
    contract=Contract(ElementReadPlan, ElementReadPlan),
)
