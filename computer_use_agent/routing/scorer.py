# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure scoring: expected utility per path from success posterior, latency, cost and exploration."""

from __future__ import annotations

from dataclasses import dataclass

from .metrics import PathMetricsStore
from .paths import TaskPath


@dataclass(frozen=True, kw_only=True)
class ScoreWeights:
    time: float = 0.3
    cost: float = 0.2
    exploration: float = 0.3
    time_scale_s: float = 60.0
    cost_scale: float = 1.0
    gui_ops_estimate: int = 8  # tasks routed to the operation-granularity GUI fallback


@dataclass(frozen=True, kw_only=True)
class ScoreBreakdown:
    score: float
    success: float
    time_penalty: float
    cost_penalty: float
    exploration_bonus: float
    expected_latency_s: float
    expected_cost: float

    def as_dict(self) -> dict[str, float]:
        return {
            "score": round(self.score, 4),
            "success": round(self.success, 4),
            "time_penalty": round(self.time_penalty, 4),
            "cost_penalty": round(self.cost_penalty, 4),
            "exploration_bonus": round(self.exploration_bonus, 4),
            "expected_latency_s": round(self.expected_latency_s, 3),
            "expected_cost": round(self.expected_cost, 6),
        }


def score_path(path: TaskPath, store: PathMetricsStore, weights: ScoreWeights) -> ScoreBreakdown:
    from .paths import Granularity

    success = store.posterior_success(path.id, path.kind)
    hint_time = (
        path.api.time_hint if path.api else path.cli.time_hint if path.cli else path.sdk.time_hint if path.sdk else None
    )
    hint_cost = (
        path.api.cost_hint if path.api else path.cli.cost_hint if path.cli else path.sdk.cost_hint if path.sdk else None
    )
    latency = store.expected_latency_s(path.id, path.kind, hint_time)
    if path.granularity is Granularity.OPERATION and weights.gui_ops_estimate > 1:
        latency *= weights.gui_ops_estimate
    cost = store.expected_cost(path.id, path.kind, hint_cost)
    time_penalty = weights.time * min(latency / weights.time_scale_s, 1.0)
    cost_penalty = weights.cost * min(cost / weights.cost_scale, 1.0) if weights.cost_scale > 0 else 0.0
    bonus = store.exploration_bonus(path.id, path.kind, weights.exploration)
    return ScoreBreakdown(
        score=success - time_penalty - cost_penalty + bonus,
        success=success,
        time_penalty=time_penalty,
        cost_penalty=cost_penalty,
        exploration_bonus=bonus,
        expected_latency_s=latency,
        expected_cost=cost,
    )
