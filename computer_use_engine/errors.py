# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public failure categories, execution facts and native protocol failures."""

from __future__ import annotations

from typing import Any, Mapping


def confirmed_no_requested_input(impact: Mapping[str, Any] | None) -> bool:
    """Require complete evidence that no attempt invoked the requested input."""
    attempts = (impact or {}).get("attempts", ())
    if not attempts:
        return False
    for attempt in attempts:
        if not attempt["dispatch"] == "notDispatched":
            return False
        actual = attempt.get("actual")
        if actual is None or not actual["coverage"] == "complete" or not actual["omitted_effects"] == 0:
            return False
        if not all(effect["stage"] != "input" or effect["state"] == "notInvoked" for effect in actual["effects"]):
            return False
    return True


def format_failure(failure: Mapping[str, Any], method: str | None = None) -> str:
    prefix = f"{method} [{failure['target']}]: " if method else ""
    impact = failure.get("impact")
    attempts = (impact or {}).get("attempts", ())
    receipt = failure.get("action_receipt")
    effect = failure["effect"]
    lines = [prefix + failure["detail"]]
    if effect == "possible" and receipt is None and failure.get("scope_cleanup") is None:
        if (
            failure.get("window_lifecycle") != "closed"
            and confirmed_no_requested_input(impact)
            and all(attempt["cleanup"] == "complete" for attempt in attempts)
        ):
            if any(
                event["stage"] in ("preparation", "cleanup") and event["state"] != "notInvoked"
                for attempt in attempts for event in attempt["actual"]["effects"]
            ):
                lines.append("Requested input was not dispatched.")
                effect += " (preparation or cleanup)"
    lines.append(f"Effect: {effect}")
    if receipt is not None:
        lines.append(f"Action [{receipt['status']}]: {receipt['target']}. {receipt['detail']}")
    if failure.get("scope_cleanup") == "unknown":
        lines.append("Scope cleanup: unknown.")
    for index, attempt in enumerate(attempts, start=1):
        lines.append(f"Attempt {index}: input={attempt['dispatch']}; cleanup={attempt['cleanup']}.")
        if attempt.get("preparation"):
            lines.append("Preparation: " + "; ".join(attempt["preparation"]))
        actual = attempt.get("actual")
        if actual:
            effects = [
                f"{effect['stage']}/{effect['device']}={effect['state']}"
                for effect in actual.get("effects", ())
                if effect["transport"] == "globalHID"
            ]
            if effects:
                lines.append("Global input: " + "; ".join(effects))
            restoration = actual.get("foreground_restoration")
            if actual.get("foreground_changed") == "yes":
                lines.append("Foreground changed: yes")
            if restoration and (
                restoration not in ("notUsed", "notNeeded") or actual.get("foreground_changed") == "yes"
            ):
                lines.append("Foreground restoration: " + restoration)
    return "\n".join(lines)


class ComputerUseError(Exception):
    """Operation failure with structured effects and related observations."""

    def __init__(self, failure: Mapping[str, Any], *, method: str | None = None, cause: BaseException | None = None):
        super().__init__(format_failure(failure, method))
        self.name = "ComputerUseError"
        self._method = method
        self.code = failure["code"]
        self.effect = failure["effect"]
        self.target = failure["target"]
        self.detail = failure["detail"]
        self._failure_context = None
        # A concrete state conflict can request a read suffix without changing
        # the public error category or replaying any input.
        self._observation_reason = None
        # A read-only suffix may enrich a write failure without changing its
        # identity, effect, recovery category, cause, or native failure context.
        self.observation = None
        self.observation_status = "not_requested"
        self.observation_error = None
        self.observed_menu_window = None
        self.window_lifecycle = "unknown"
        self.choices = ()
        self.impact = failure.get("impact")
        self.action_receipt = None
        self.scope_cleanup = None
        self._invoked_menu_target = None
        # Host-only feedback may identify this PNG and public window reference.
        self._pending_point_image = None
        if cause is not None:
            self.__cause__ = cause

    def __str__(self) -> str:
        """Render the failure's recorded effects and details."""
        return format_failure(vars(self), self._method)


def public_error_code(code: str) -> str:
    if code in {
        "stale_target",
        "outside_window_scope",
        "snapshot_expired",
        "ambiguous_target",
        "blocked_by_modal",
        "element_not_found",
        "observation_incomplete",
        "input_unavailable",
        "permission_required",
        "unsupported_operation",
        "desktop_unavailable",
        "invalid_request",
        "execution_failed",
        "execution_unknown",
    }:
        return code
    return {
        "session_mismatch": "stale_target",
        "version_mismatch": "invalid_request",
        "request_conflict": "invalid_request",
        "busy": "invalid_request",
        "request_expired": "execution_unknown",
        "in_progress": "execution_unknown",
    }.get(code, "execution_failed")
