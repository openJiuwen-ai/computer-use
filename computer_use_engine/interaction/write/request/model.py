# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from computer_use.interaction.requests import ExecuteAction

if TYPE_CHECKING:
    from computer_use.interaction.records import Remaining
    from computer_use.native.contracts import Intent, OperationTarget
    from computer_use.protocol.records import Record
    from computer_use.session.state import AppState
    from computer_use.types import Observation, ObservationError, WindowInfo


@dataclass(frozen=True)
class ActionPlan:
    """Controller action paired with its App reconciliation and call-budget requirements.

    Attributes:
        request: Original API operation and target scope.
        state: App state needed for keyboard input or element-path reconciliation.
        reconcile: Whether to refresh the App's selected working-window scope before execution.
        remaining: Borrowed budget callback supplied by the reconciliation call scope.
        target: Native target used when the action reaches the write engine.
        mechanism: Lowered mechanism intent, while request retains the public operation.
    """

    request: ExecuteAction
    state: AppState | None = None
    reconcile: bool = False
    remaining: Remaining | None = None
    target: OperationTarget | None = None
    mechanism: Intent | None = None


@dataclass(frozen=True)
class ObservedAction:
    """Keep the authoritative write receipt separate from its optional read result."""

    receipt: Record | None
    observation: Observation | None = None
    observation_status: Literal["available", "partial", "unavailable", "not_requested"] = "unavailable"
    observation_error: ObservationError | None = None
    window_lifecycle: Literal["observed", "closed", "unknown", "not_applicable"] = "unknown"
    choices: tuple[WindowInfo, ...] = ()
