# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Typed contracts for multi-path task routing.

A TaskPath is one concrete way to accomplish a task. Engine routes (api and
gui kinds) execute SDK code templates through the single computer_use session;
external routes call an HTTP endpoint or a subprocess. All paths share the
same metric and verification contracts.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal


class PathKind(StrEnum):
    API = "api"
    CLI = "cli"
    GUI = "gui"


class RiskClass(StrEnum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class FailureClass(StrEnum):
    PRECONDITION = "precondition"
    VALIDATION = "validation"
    TRANSPORT = "transport"
    EXECUTION = "execution"
    VERIFICATION = "verification"
    REFUSED = "refused"
    CANCELLED = "cancelled"


class ExecutionStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EFFECT_UNKNOWN = "effect_unknown"


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class Granularity(StrEnum):
    ROUTE = "route"
    OPERATION = "operation"


@dataclass(frozen=True, kw_only=True)
class ApiSpec:
    method: str = "GET"
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    timeout_s: float = 30.0
    time_hint: float | None = None
    cost_hint: float | None = None


@dataclass(frozen=True, kw_only=True)
class CliSpec:
    argv: tuple[str, ...]
    timeout_s: float = 60.0
    binary: str | None = None
    time_hint: float | None = None
    cost_hint: float | None = None


@dataclass(frozen=True, kw_only=True)
class SdkSpec:
    code_template: str
    time_hint: float | None = None
    cost_hint: float | None = None


@dataclass(frozen=True, kw_only=True)
class VerifySpec:
    """Outcome contract; evidence kinds map onto executor outputs."""

    kind: Literal["http_status", "exit_code", "output_contains", "output_regex", "json_field"]
    expect: int | str = ""
    field: str | None = None


@dataclass(frozen=True, kw_only=True)
class TaskPath:
    id: str
    kind: PathKind
    app: str
    intent: str
    arguments: dict[str, Any] = field(default_factory=dict)  # JSON Schema for route inputs
    risk: RiskClass = RiskClass.READ
    keywords: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()
    granularity: Granularity = Granularity.ROUTE
    api: ApiSpec | None = None
    cli: CliSpec | None = None
    sdk: SdkSpec | None = None
    verify: VerifySpec | None = None
    enabled: bool = True

    def __post_init__(self):
        provided = [spec is not None for spec in (self.api, self.cli, self.sdk)]
        if sum(provided) != 1:
            raise ValueError(f"TaskPath {self.id!r} needs exactly one of api/cli/sdk spec")


GUI_FALLBACK_ID = "gui://fallback"


def gui_fallback_path() -> TaskPath:
    return TaskPath(
        id=GUI_FALLBACK_ID,
        kind=PathKind.GUI,
        app="*",
        intent="Operate the UI directly with computer_use (coordinates, screenshots, keyboard).",
        risk=RiskClass.WRITE,
        granularity=Granularity.OPERATION,
        sdk=SdkSpec(code_template=""),
    )


@dataclass(frozen=True, kw_only=True)
class OutcomeRecord:
    path_id: str
    task: str
    started_at: float = field(default_factory=time.time)
    latency_s: float = 0.0
    success: bool | None = None  # None: effect unknown, excluded from the success posterior
    failure: FailureClass | None = None
    cost: float | None = None
    detail: str = ""


@dataclass(frozen=True, kw_only=True)
class RouteCandidate:
    path: TaskPath
    score: float
    eligible: bool
    reasons: tuple[str, ...] = ()
    breakdown: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class SubtaskPlan:
    index: int
    goal: str
    plan: "RoutePlan"


@dataclass(frozen=True, kw_only=True)
class RoutePlan:
    task: str
    decision: Literal["execute", "decompose", "refuse"]
    chosen: RouteCandidate | None = None
    candidates: tuple[RouteCandidate, ...] = ()
    subtasks: tuple[SubtaskPlan, ...] = ()
    reason: str = ""


@dataclass(frozen=True, kw_only=True)
class RouteExecution:
    path_id: str
    status: ExecutionStatus
    dispatched: bool
    latency_s: float
    cost: float | None = None
    output: str = ""
    error: str = ""
    verdict: Verdict = Verdict.UNKNOWN
    detail: str = ""


@dataclass(frozen=True, kw_only=True)
class RouteResult:
    plan: RoutePlan
    executions: tuple[RouteExecution, ...] = ()
    success: bool | None = None  # None: refused or effect unknown
    summary: str = ""
