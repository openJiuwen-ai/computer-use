from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.model import NativeInvocation
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def select_unit_effect(value: NativeInvocation, control: Control) -> str:
    """Select the preparation or input-dispatch branch of this native unit."""
    return (
        "click"
        if value.unit.command.primitive.click is not None
        else "prepare"
        if value.unit.plan.prepare
        else "dispatch"
    )


stage = Pass.define(
    name="select_unit_effect",
    function=select_unit_effect,
    contract=Contract(NativeInvocation, str),
)
