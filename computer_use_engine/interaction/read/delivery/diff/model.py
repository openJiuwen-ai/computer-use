from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.pipeline import Effect

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.delta import DeltaPlan
    from computer_use.protocol.records import Record

PURE = {Effect.PURE}


@dataclass(frozen=True)
class DeliverySelection:
    """Current observation and the preceding baseline used to choose full or diff text.

    Attributes:
        observation: Current observation eligible for full or diff delivery.
        previous: Previously delivered baseline for the same observation stream, if present.
    """

    observation: Record
    previous: Record | None


@dataclass(frozen=True)
class PlannedDelivery:
    """Current observation and its text-free delivery decision.

    Attributes:
        observation: Captured state, immutable display document, and native bindings.
        plan: Complete or incremental delivery scope selected before text generation.
    """

    observation: Record
    plan: DeltaPlan
