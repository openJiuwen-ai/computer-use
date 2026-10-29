# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Local click records; native effects have one operation-owned journal."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SelectedClickPlan:
    contract: dict
    unit: object
    route_family: str
    delivery: str
    foreground: bool


@dataclass
class ClickAttemptLedger:
    """Append-only evidence around native calls, not an effect-success flag.

    XTest sync acknowledges a request, never the target widget's consumption.
    An entered call lacking a return remains possible even when cleanup returns.
    The operation owns this record and the retained step receipt owns its result.
    """

    preparation: list = field(default_factory=list)
    calls_entered: int = 0
    calls_returned: int = 0
    calls_pending: int = 0
    cleanup_entered: int = 0
    cleanup_returned: int = 0
    pointer_started: bool = False
    action_accepted: bool | None = None
    requested_complete: bool = False
    expected_requests: int = 0

    def event(self, phase, kind, detail, *, cleanup=False):
        if cleanup:
            if phase == "enter":
                self.cleanup_entered += 1
            else:
                self.cleanup_returned += 1
            return
        if phase == "enter":
            self.calls_entered += 1
            self.calls_pending += 1
            self.pointer_started = True
        else:
            self.calls_returned += 1
            self.calls_pending -= 1

    def action_entered(self):
        self.calls_entered += 1
        self.calls_pending += 1

    def action_returned(self, accepted):
        self.calls_returned += 1
        self.calls_pending -= 1
        self.action_accepted = bool(accepted)

    @property
    def dispatch(self):
        if not self.calls_entered:
            return "notDispatched"
        if self.calls_pending or self.action_accepted is False:
            return "unknown"
        matches_target = (
            True
            if (
                self.requested_complete
                or self.action_accepted is True
                or self.pointer_started
                and self.calls_returned == self.expected_requests
            )
            else False
        )
        if matches_target:
            return "dispatched"
        return "partial"
