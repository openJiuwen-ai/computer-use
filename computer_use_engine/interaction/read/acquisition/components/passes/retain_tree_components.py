"""Retain tree components."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.components.model import (
    PURE,
    ComponentLease,
    ComponentParts,
)
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(value: ComponentLease, control: Control) -> ComponentParts:
    """Return the collected tree components when the image branch is not requested."""
    return value.parts


stage = Pass.define(
    name="retain_tree_components",
    function=execute,
    contract=Contract(ComponentLease, ComponentParts, PURE),
)
