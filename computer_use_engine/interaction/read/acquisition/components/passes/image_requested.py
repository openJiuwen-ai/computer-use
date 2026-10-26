"""Image requested."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.components.model import PURE, ComponentLease
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(value: ComponentLease, control: Control) -> bool:
    """Select image collection when the component request includes pixels."""
    return value.request.include_image


stage = Pass.define(
    name="image_requested",
    function=execute,
    contract=Contract(ComponentLease, bool, PURE),
)
