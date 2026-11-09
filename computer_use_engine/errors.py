# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public failure categories, execution facts and native protocol failures."""

from __future__ import annotations

from typing import Any, Mapping


def format_failure(failure: Mapping[str, Any], method: str | None = None) -> str:
    prefix = f"{method} [{failure['target']}]: " if method else ""
    lines = [prefix + failure["detail"], f"Effect: {failure['effect']}"]
    impact = failure.get("impact")
    for index, attempt in enumerate(impact.get("attempts", ()) if impact else (), start=1):
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
        # A read-only suffix may enrich a write failure without changing its
        # identity, effect, recovery category, cause, or native failure context.
        self.observation = None
        self.observation_status = "not_requested"
        self.observation_error = None
        self.window_lifecycle = "unknown"
        self.choices = ()
        self.impact = failure.get("impact")
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
