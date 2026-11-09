# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Serializable program evidence; contains no live SDK/native target bindings."""

from dataclasses import dataclass, replace
from time import monotonic
from typing import Literal


@dataclass(frozen=True)
class Check:
    condition: str
    value: Literal["TRUE", "FALSE", "UNKNOWN"]
    scope: str
    evidence: dict
    observed_at_s: float

    def __post_init__(self):
        if self.value not in ("TRUE", "FALSE", "UNKNOWN") or not self.condition or not self.scope:
            raise ValueError("Checks require a condition, scope and three-valued result")


@dataclass(frozen=True)
class ProgramResult:
    execution_id: str
    program: dict
    scope: str
    expected_sha256: str
    acceptance_condition: str
    status: Literal[
        "awaiting_acceptance", "succeeded", "failed", "unconfirmed", "refused", "stopped", "cancelled", "timeout"
    ]
    checks: tuple[Check, ...]
    steps: tuple[dict, ...]
    confirmed_progress: tuple[str, ...]
    failure: dict | None
    # Host acceptance is part of the same process/call deadline. This value is
    # evidence, not a portable resume token; results can never resume execution.
    deadline_at_s: float
    returned_at_s: float
    duration_s: float
    observation_required: bool
    acceptance: Check | None = None


def conclude(result: ProgramResult, acceptance: Check) -> ProgramResult:
    """Attach independent host acceptance without executing any more actions.

    A failed/cancelled execution cannot be rescued by a coincidentally correct
    artifact. UNKNOWN never becomes success. Final evaluators are not consulted
    by the program's action selection or any repair/generation process.
    """
    if result.acceptance is not None:
        raise ValueError("An execution can be concluded only once")
    if (
        acceptance.condition != result.acceptance_condition
        or acceptance.scope != result.scope
        or acceptance.evidence.get("expected_sha256") != result.expected_sha256
    ):
        raise ValueError("Acceptance does not cover the requested program outcome")
    if (
        acceptance.evidence.get("execution_id") != result.execution_id
        or not result.returned_at_s <= acceptance.observed_at_s <= monotonic()
    ):
        raise ValueError("Acceptance does not cover the requested program outcome")
    status = result.status
    if status == "awaiting_acceptance":
        status = (
            "timeout"
            if monotonic() > result.deadline_at_s
            else {"TRUE": "succeeded", "FALSE": "failed", "UNKNOWN": "unconfirmed"}[acceptance.value]
        )
    return replace(
        result,
        status=status,
        acceptance=acceptance,
        observation_required=status != "succeeded",
        duration_s=result.duration_s + monotonic() - result.returned_at_s,
    )
