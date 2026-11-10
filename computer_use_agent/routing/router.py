# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Task-level routing: discover, filter, score, decide (execute/decompose/refuse), execute, verify, record."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .executors import CodeRunner, PathArgumentError, execute_api, execute_cli, execute_sdk, validate_arguments
from .metrics import MetricPriors, PathMetricsStore
from .paths import (
    ExecutionStatus,
    FailureClass,
    OutcomeRecord,
    RouteCandidate,
    RouteExecution,
    RoutePlan,
    RouteResult,
    SubtaskPlan,
    TaskPath,
    Verdict,
    gui_fallback_path,
)
from .registry import PathRegistry
from .scorer import ScoreWeights, score_path
from .verifiers import verify

ConfirmCallback = Callable[[TaskPath, dict], Awaitable[bool]]
PreconditionProbe = Callable[[str], Awaitable[bool] | bool]


@dataclass(frozen=True, kw_only=True)
class RoutingPolicy:
    min_confidence: float = 0.45
    refuse_floor: float = 0.2  # refuse only when even the GUI fallback posterior stays below this
    refuse_after_attempts: int = 5
    allow_destructive: bool = False
    max_subtasks: int = 8
    task_keywords: int = 6


@dataclass(frozen=True, kw_only=True)
class RouterConfig:
    policy: RoutingPolicy = field(default_factory=RoutingPolicy)
    weights: ScoreWeights = field(default_factory=ScoreWeights)
    priors: MetricPriors = field(default_factory=MetricPriors)


class Router:
    """Owns candidate selection and execution; metrics are owned by PathMetricsStore."""

    def __init__(
        self,
        registry: PathRegistry | None = None,
        config: RouterConfig | None = None,
        *,
        store: PathMetricsStore | None = None,
        run_code: CodeRunner | None = None,
        confirm: ConfirmCallback | None = None,
        probe: PreconditionProbe | None = None,
    ):
        self.registry = registry or PathRegistry()
        self.config = config or RouterConfig()
        self.store = store or PathMetricsStore(self.config.priors)
        self._run_code = run_code
        self._confirm = confirm
        self._probe = probe

    async def plan(
        self,
        task: str,
        *,
        app: str | None = None,
        arguments: dict | None = None,
        subtasks: list[str] | None = None,
    ) -> RoutePlan:
        if subtasks:
            return await self._plan_decomposition(task, app=app, subtasks=subtasks)
        candidates = await self._candidates(task, app=app, arguments=arguments)
        eligible = [candidate for candidate in candidates if candidate.eligible]
        if not eligible:
            return RoutePlan(task=task, decision="refuse", candidates=tuple(candidates), reason="no eligible path")
        chosen = max(eligible, key=lambda candidate: candidate.score)
        if chosen.score >= self.config.policy.min_confidence:
            return RoutePlan(task=task, decision="execute", chosen=chosen, candidates=tuple(candidates))
        fallback = next((c for c in eligible if c.path.id == gui_fallback_path().id), None)
        if fallback is not None and await self._fallback_unproven(fallback):
            return RoutePlan(
                task=task,
                decision="refuse",
                candidates=tuple(candidates),
                reason=(
                    f"best path {chosen.path.id} score {chosen.score:.2f} below confidence "
                    f"{self.config.policy.min_confidence} and GUI fallback success rate is unproven "
                    f"({fallback.breakdown.get('success', 0):.2f} over "
                    f"{self.store.snapshot(fallback.path.id, fallback.path.kind).attempts} attempts)"
                ),
            )
        if fallback is not None:
            return RoutePlan(
                task=task,
                decision="execute",
                chosen=fallback,
                candidates=tuple(candidates),
                reason=f"no path reaches confidence {self.config.policy.min_confidence}; using GUI fallback",
            )
        return RoutePlan(
            task=task,
            decision="refuse",
            candidates=tuple(candidates),
            reason=f"no path reaches confidence {self.config.policy.min_confidence} and GUI fallback is unavailable",
        )

    async def execute_plan(self, plan: RoutePlan, arguments: dict | None = None) -> RouteResult:
        if plan.decision == "refuse":
            return RouteResult(plan=plan, success=False, summary=plan.reason or "refused")
        if plan.decision == "decompose":
            executions: list[RouteExecution] = []
            for subtask in plan.subtasks:
                result = await self.execute_plan(subtask.plan, arguments)
                executions.extend(result.executions)
                if result.success is not True:
                    summary = f"subtask {subtask.index} ({subtask.goal}) did not succeed: {result.summary}"
                    return RouteResult(plan=plan, executions=tuple(executions), success=result.success, summary=summary)
            return RouteResult(plan=plan, executions=tuple(executions), success=True, summary="all subtasks succeeded")
        chosen = plan.chosen
        if chosen is None:
            return RouteResult(plan=plan, success=False, summary="execute decision without a chosen path")
        if chosen.path.id == gui_fallback_path().id:
            return RouteResult(
                plan=plan,
                success=None,
                summary="gui fallback: no registered path justified itself; continue with computer_use",
            )
        validation_error = False
        try:
            execution = await self._dispatch(chosen.path, arguments or {})
        except PathArgumentError as error:
            validation_error = True
            execution = RouteExecution(
                path_id=chosen.path.id,
                status=ExecutionStatus.FAILED,
                dispatched=False,
                latency_s=0.0,
                error=str(error),
            )
        verdict, verdict_detail = await verify(chosen.path.verify, _evidence(execution))
        if validation_error:
            success = None  # caller error; must not degrade the path's success posterior
        elif execution.status is ExecutionStatus.COMPLETED:
            success: bool | None = verdict is Verdict.PASS
        elif execution.status is ExecutionStatus.EFFECT_UNKNOWN:
            success = None
            verdict = Verdict.UNKNOWN
        else:
            success = False
        execution = RouteExecution(
            **{**execution.__dict__, "verdict": verdict, "detail": f"{execution.detail} {verdict_detail}".strip()}
        )
        failure = None
        if validation_error:
            failure = FailureClass.VALIDATION
        elif success is False:
            failure = FailureClass.VERIFICATION if verdict is Verdict.FAIL else FailureClass.EXECUTION
        self.store.record(
            OutcomeRecord(
                path_id=chosen.path.id,
                task=plan.task,
                latency_s=execution.latency_s,
                success=success,
                failure=failure,
                cost=execution.cost,
                detail=execution.error or execution.detail[:300],
            ),
            chosen.path.kind,
        )
        summary = _summarize(execution, success)
        return RouteResult(plan=plan, executions=(execution,), success=success, summary=summary)

    async def route(
        self,
        task: str,
        *,
        app: str | None = None,
        arguments: dict | None = None,
        subtasks: list[str] | None = None,
    ) -> RouteResult:
        """Plan and, when the plan decides execution, run it in one call."""

        plan = await self.plan(task, app=app, arguments=arguments, subtasks=subtasks)
        if plan.decision == "refuse":
            return RouteResult(plan=plan, success=False, summary=plan.reason)
        return await self.execute_plan(plan, arguments)

    async def _plan_decomposition(self, task: str, *, app: str | None, subtasks: list[str]) -> RoutePlan:
        if not subtasks or len(subtasks) > self.config.policy.max_subtasks:
            return RoutePlan(
                task=task,
                decision="refuse",
                reason=f"subtasks must number 1..{self.config.policy.max_subtasks}",
            )
        plans = []
        for index, goal in enumerate(subtasks):
            sub_plan = await self.plan(goal, app=app)
            if sub_plan.decision == "refuse":
                return RoutePlan(
                    task=task,
                    decision="refuse",
                    reason=f"subtask {index} ({goal}) has no route: {sub_plan.reason}",
                )
            plans.append(SubtaskPlan(index=index, goal=goal, plan=sub_plan))
        return RoutePlan(task=task, decision="decompose", subtasks=tuple(plans), reason=f"{len(plans)} subtasks routed")

    async def _candidates(self, task: str, *, app: str | None, arguments: dict | None) -> list[RouteCandidate]:
        paths = self.registry.query(app=app)
        keywords = tuple(
            word.strip("，。,.!?？!") for word in task.split()[: self.config.policy.task_keywords] if word.strip()
        )
        if keywords:
            narrowed = self.registry.query(app=app, keywords=keywords)
            if narrowed:
                paths = narrowed
        fallback = gui_fallback_path()
        paths.append(fallback)
        candidates = []
        for path in paths:
            eligible, reason = await self._eligible(path, arguments or {})
            breakdown = score_path(path, self.store, self.config.weights).as_dict()
            reasons = () if eligible else (reason,)
            if eligible:
                candidates.append(
                    RouteCandidate(path=path, score=breakdown["score"], eligible=True, breakdown=breakdown)
                )
            else:
                candidates.append(
                    RouteCandidate(
                        path=path, score=breakdown["score"], eligible=False, reasons=reasons, breakdown=breakdown
                    )
                )
        return candidates

    async def _eligible(self, path: TaskPath, arguments: dict) -> tuple[bool, str]:
        ok, reason = self.registry.check_preconditions(path)
        if not ok:
            return False, reason
        for precondition in path.preconditions:
            if self._probe is not None:
                try:
                    if not await _maybe_await(self._probe(precondition)):
                        return False, f"precondition failed: {precondition}"
                except Exception:
                    return False, f"precondition probe error: {precondition}"
        try:
            validate_arguments(path, arguments)
        except PathArgumentError as error:
            return False, str(error)
        if path.risk.value == "destructive":
            if not self.config.policy.allow_destructive and self._confirm is None:
                return False, "destructive path needs confirmation"
            if self._confirm is not None and not await _maybe_await(self._confirm(path, arguments)):
                return False, "destructive path denied by confirmation"
        return True, ""

    async def _fallback_unproven(self, fallback: RouteCandidate) -> bool:
        attempts = self.store.snapshot(fallback.path.id, fallback.path.kind).attempts
        return (
            attempts >= self.config.policy.refuse_after_attempts
            and fallback.breakdown.get("success", 1.0) < self.config.policy.refuse_floor
        )

    async def _dispatch(self, path: TaskPath, arguments: dict) -> RouteExecution:
        if path.api is not None:
            return await execute_api(path, arguments)
        if path.cli is not None:
            return await execute_cli(path, arguments)
        if self._run_code is None:
            return RouteExecution(
                path_id=path.id,
                status=ExecutionStatus.FAILED,
                dispatched=False,
                latency_s=0.0,
                error="no code runner available for sdk/gui path",
            )
        return await execute_sdk(path, arguments, self._run_code)


def _evidence(execution: RouteExecution) -> dict:
    try:
        return json.loads(execution.detail) if execution.detail else {}
    except ValueError:
        return {"output": execution.output}


def _summarize(execution: RouteExecution, success: bool | None) -> str:
    if success is None:
        return f"{execution.path_id}: effect unknown ({execution.error or 'no response'})"
    if success:
        return f"{execution.path_id}: success in {execution.latency_s:.1f}s"
    return f"{execution.path_id}: failed ({execution.error or execution.detail})"


async def _maybe_await(value):
    if asyncio.iscoroutine(value) or isinstance(value, Awaitable):
        return await value
    return value


def render_plan(plan: RoutePlan) -> str:
    """Model-facing rendering of a plan with metric evidence."""

    lines = [f"task: {plan.task}", f"decision: {plan.decision}"]
    if plan.reason:
        lines.append(f"reason: {plan.reason}")
    for candidate in sorted(plan.candidates, key=lambda item: item.score, reverse=True):
        marker = "*" if plan.chosen is candidate else "-"
        state = "eligible" if candidate.eligible else f"ineligible ({', '.join(candidate.reasons)})"
        breakdown = candidate.breakdown
        lines.append(
            f"{marker} {candidate.path.id} [{candidate.path.kind}] {state} score={candidate.score:.2f} "
            f"success~{breakdown.get('success', 0):.2f} time~{breakdown.get('expected_latency_s', 0):.1f}s "
            f"cost~{breakdown.get('expected_cost', 0)}"
        )
    for subtask in plan.subtasks:
        lines.append(f"  subtask {subtask.index}: {subtask.goal} -> {subtask.plan.decision}")
    if plan.decision == "execute" and plan.chosen is not None:
        lines.append(f"next: execute {plan.chosen.path.id} (result included when already executed)")
    elif plan.decision == "refuse":
        lines.append("next: explain the reason to the user and ask how to proceed; do not force GUI operations")
    return "\n".join(lines)
