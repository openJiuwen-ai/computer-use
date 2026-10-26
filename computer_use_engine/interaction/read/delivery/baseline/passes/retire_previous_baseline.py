"""Retire previous baseline."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.delivery.baseline.model import RESOURCE, BaselineInput
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.delivery.baseline.model import BaselineStore
    from computer_use.pipeline.core import Control


def create(*, store: BaselineStore) -> Callable[[BaselineInput, Control], BaselineInput]:
    def retire_previous(value: BaselineInput, control: Control) -> BaselineInput:
        """Remove the stream's previous baseline and subtract its retained-byte accounting."""
        store.retire_baseline(value.key)
        return value

    return retire_previous


stage = Pass.factory(
    name="retire_previous_baseline",
    factory=create,
    contract=Contract(BaselineInput, BaselineInput, RESOURCE, writes=("delivered baseline store",)),
)
