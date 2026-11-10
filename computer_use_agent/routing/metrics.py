# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-path metrics with Bayesian success posterior, EMA latencies and JSONL persistence."""

from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .paths import OutcomeRecord, PathKind

DEFAULT_PRIORS = {
    PathKind.API: (6.0, 1.0),  # ~0.86 cold-start success
    PathKind.CLI: (4.0, 2.0),  # ~0.67
    PathKind.GUI: (3.0, 3.0),  # ~0.50
}
DEFAULT_EMA_ALPHA = 0.3
DEFAULT_TIME_PRIOR_S = {PathKind.API: 2.0, PathKind.CLI: 5.0, PathKind.GUI: 4.0}
DEFAULT_COST_PRIOR = {PathKind.API: 0.0005, PathKind.CLI: 0.0, PathKind.GUI: 0.002}


@dataclass(frozen=True, kw_only=True)
class MetricPriors:
    success_alpha_beta: dict[PathKind, tuple[float, float]] = field(default_factory=lambda: dict(DEFAULT_PRIORS))
    ema_alpha: float = DEFAULT_EMA_ALPHA
    time_prior_s: dict[PathKind, float] = field(default_factory=lambda: dict(DEFAULT_TIME_PRIOR_S))
    cost_prior: dict[PathKind, float] = field(default_factory=lambda: dict(DEFAULT_COST_PRIOR))


@dataclass
class PathMetrics:
    attempts: int = 0
    successes: int = 0
    ema_latency_s: float | None = None
    ema_cost: float | None = None
    total_cost: float = 0.0
    unknown_effects: int = 0


class PathMetricsStore:
    """Single owner of routing metrics; safe to share across tasks in one process."""

    def __init__(self, priors: MetricPriors | None = None, *, persistence_path: Path | str | None = None):
        self.priors = priors or MetricPriors()
        self._lock = threading.Lock()
        self._metrics: dict[str, PathMetrics] = {}
        self._kinds: dict[str, PathKind] = {}
        self._path = Path(persistence_path) if persistence_path else None
        if self._path and self._path.exists():
            self._load()

    def snapshot(self, path_id: str, kind: PathKind) -> PathMetrics:
        with self._lock:
            self._kinds.setdefault(path_id, kind)
            metrics = self._metrics.get(path_id)
            if metrics is None:
                metrics = PathMetrics()
                self._metrics[path_id] = metrics
                return PathMetrics()
            return PathMetrics(
                attempts=metrics.attempts,
                successes=metrics.successes,
                ema_latency_s=metrics.ema_latency_s,
                ema_cost=metrics.ema_cost,
                total_cost=metrics.total_cost,
                unknown_effects=metrics.unknown_effects,
            )

    def kinds(self) -> dict[str, PathKind]:
        with self._lock:
            return dict(self._kinds)

    def record(self, outcome: OutcomeRecord, kind: PathKind):
        with self._lock:
            self._kinds.setdefault(outcome.path_id, kind)
            metrics = self._metrics.setdefault(outcome.path_id, PathMetrics())
            metrics.attempts += 1
            if outcome.success is None:
                metrics.unknown_effects += 1
            else:
                metrics.successes += int(outcome.success)
            if outcome.latency_s > 0:
                alpha = self.priors.ema_alpha
                metrics.ema_latency_s = (
                    outcome.latency_s
                    if metrics.ema_latency_s is None
                    else (1 - alpha) * metrics.ema_latency_s + alpha * outcome.latency_s
                )
            if outcome.cost is not None:
                metrics.total_cost += outcome.cost
                alpha = self.priors.ema_alpha
                metrics.ema_cost = (
                    outcome.cost if metrics.ema_cost is None else (1 - alpha) * metrics.ema_cost + alpha * outcome.cost
                )
            line = json.dumps({"kind": str(kind), **outcome.__dict__}, ensure_ascii=False)
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")

    def posterior_success(self, path_id: str, kind: PathKind) -> float:
        alpha, beta = self.priors.success_alpha_beta.get(kind, (1.0, 1.0))
        snapshot = self.snapshot(path_id, kind)
        definitive = snapshot.attempts - snapshot.unknown_effects
        return (snapshot.successes + alpha) / (definitive + alpha + beta)

    def expected_latency_s(self, path_id: str, kind: PathKind, hint: float | None) -> float:
        snapshot = self.snapshot(path_id, kind)
        if snapshot.ema_latency_s is not None:
            return snapshot.ema_latency_s
        if hint is not None:
            return hint
        return self.priors.time_prior_s.get(kind, 5.0)

    def expected_cost(self, path_id: str, kind: PathKind, hint: float | None) -> float:
        snapshot = self.snapshot(path_id, kind)
        if snapshot.ema_cost is not None:
            return snapshot.ema_cost
        if hint is not None:
            return hint
        return self.priors.cost_prior.get(kind, 0.0)

    def exploration_bonus(self, path_id: str, kind: PathKind, exploration: float) -> float:
        """UCB1-style bonus so cold paths keep getting sampled."""
        snapshot = self.snapshot(path_id, kind)
        if snapshot.attempts == 0:
            return exploration
        total = sum(item.attempts for item in self._metrics.values())
        if total <= snapshot.attempts:
            return 0.0
        return exploration * math.sqrt(math.log(total) / snapshot.attempts)

    def _load(self):
        with self._lock:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                kind = PathKind(payload.pop("kind", str(PathKind.GUI)))
                outcome = OutcomeRecord(**payload)
                metrics = self._metrics.setdefault(outcome.path_id, PathMetrics())
                metrics.attempts += 1
                if outcome.success is None:
                    metrics.unknown_effects += 1
                else:
                    metrics.successes += int(outcome.success)
                if outcome.latency_s > 0:
                    alpha = self.priors.ema_alpha
                    metrics.ema_latency_s = (
                        outcome.latency_s
                        if metrics.ema_latency_s is None
                        else (1 - alpha) * metrics.ema_latency_s + alpha * outcome.latency_s
                    )
                if outcome.cost is not None:
                    metrics.total_cost += outcome.cost
                    alpha = self.priors.ema_alpha
                    metrics.ema_cost = (
                        outcome.cost
                        if metrics.ema_cost is None
                        else (1 - alpha) * metrics.ema_cost + alpha * outcome.cost
                    )
                self._kinds.setdefault(outcome.path_id, kind)

    def dump(self) -> dict[str, dict]:
        with self._lock:
            return {key: dict(vars(value)) for key, value in self._metrics.items()}


def merge_outcomes(store: PathMetricsStore, outcomes: Iterable[tuple[OutcomeRecord, PathKind]]):
    for outcome, kind in outcomes:
        store.record(outcome, kind)
