"""Select full or diff."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.delivery.diff.model import PURE, DeliverySelection
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def select_full_or_diff(value: DeliverySelection, control: Control) -> bool:
    """Choose full text for explicit full reads, non-window views, or a missing compatible
    baseline.
    """
    observation, previous = value.observation, value.previous
    return bool(
        observation["full"] or observation["view"] != "window" or previous is None or previous["view"] != "window"
    )


stage = Pass.define(
    name="select_full_or_diff",
    function=select_full_or_diff,
    contract=Contract(DeliverySelection, bool, PURE),
)
