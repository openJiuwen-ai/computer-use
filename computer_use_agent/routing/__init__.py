# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Multi-path task routing for the Computer Use agent (GUI / CLI / API)."""

from .metrics import MetricPriors, PathMetrics, PathMetricsStore
from .paths import (
    GUI_FALLBACK_ID,
    ApiSpec,
    CliSpec,
    ExecutionStatus,
    FailureClass,
    Granularity,
    OutcomeRecord,
    PathKind,
    RiskClass,
    RouteExecution,
    RoutePlan,
    RouteResult,
    SdkSpec,
    TaskPath,
    VerifySpec,
    Verdict,
    gui_fallback_path,
)
from .registry import PathRegistry
from .router import Router, RouterConfig, RoutingPolicy, render_plan
from .scorer import ScoreWeights, score_path
from .verifiers import verify

__all__ = [
    "ApiSpec",
    "CliSpec",
    "ExecutionStatus",
    "FailureClass",
    "GUI_FALLBACK_ID",
    "Granularity",
    "MetricPriors",
    "OutcomeRecord",
    "PathKind",
    "PathMetrics",
    "PathMetricsStore",
    "PathRegistry",
    "RiskClass",
    "RouteExecution",
    "RoutePlan",
    "RouteResult",
    "Router",
    "RouterConfig",
    "RoutingPolicy",
    "SdkSpec",
    "ScoreWeights",
    "TaskPath",
    "VerifySpec",
    "Verdict",
    "gui_fallback_path",
    "render_plan",
    "score_path",
    "verify",
]
