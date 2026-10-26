"""Publish observation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.delivery.baseline.model import RESOURCE, BaselineDraft
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.delivery.baseline.model import BaselineStore
    from computer_use.pipeline.core import Control


def create(*, store: BaselineStore) -> Callable[[BaselineDraft, Control], None]:
    def publish(value: BaselineDraft, control: Control) -> None:
        """Store the prepared delivery baseline within the configured capacity limits.

        Skip a baseline larger than the byte limit; otherwise evict oldest entries until scope
        and byte capacity permit insertion. Update the retained-byte total with the stored
        baseline.
        """
        store.publish_baseline(value)

    return publish


stage = Pass.factory(
    name="publish_observation",
    factory=create,
    contract=Contract(BaselineDraft, type(None), RESOURCE, writes=("delivered baseline store",)),
)
