# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native openJiuwen tools backed by the existing Computer Use MCP service."""

import asyncio
import logging
import time
from uuid import uuid4

from computer_use_mcp.server import DESCRIPTION, RESET_DESCRIPTION
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.foundation.tool.base import render_payload_text
from openjiuwen.core.foundation.tool.mcp.base import extract_mcp_tool_result_content
from openjiuwen.core.foundation.tool.schema import ToolOutput
from openjiuwen.core.runner.runner import Runner

from .config import AgentConfig
from .connection import McpConnection
from .prompt import system_prompt
from .routing import (
    GUI_FALLBACK_ID,
    OutcomeRecord,
    PathKind,
    PathMetricsStore,
    PathRegistry,
    Router,
    RouterConfig,
    RoutingPolicy,
    ScoreWeights,
    render_plan,
)
from .routing.paths import ExecutionStatus

logger = logging.getLogger(__name__)

ROUTE_TASK_DESCRIPTION = """Rank every known way to accomplish a task — registered app API routes, shell CLI
routes, and direct GUI operation — by learned success rate, expected time and cost, then act on the best
eligible path. Call this before manipulating the UI when the task might be achievable through an API or shell
route. It executes the chosen path itself when the decision is execute, and reports the outcome. If the
decision is gui_fallback, continue with computer_use. If the decision is refuse, explain the reason and ask
the user instead of forcing operations. Pass subtasks to split the task into independently routed subtasks.
Never retry a route whose effect is unknown."""


class DesktopTool(Tool):
    def __init__(self, toolkit, *, name, description, properties, required):
        super().__init__(
            ToolCard(
                id=f"{toolkit.id}.{name}",
                name=name,
                description=description,
                idempotent=False,
                input_params={
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            )
        )
        self.toolkit = toolkit

    async def invoke(self, inputs, **kwargs):
        return await self.toolkit.call(self.card.name, inputs)

    async def stream(self, inputs, **kwargs):
        yield await self.toolkit.call(self.card.name, inputs)

    def render_for_llm(self, output):
        return output.data["content"]


class ComputerUseToolkit:
    """Pass tools to create_deep_agent; retain the toolkit for the conversation."""

    def __init__(self, config: AgentConfig | None = None, *, on_event=None, stderr=None):
        self.id = uuid4().hex
        self.config = config or AgentConfig()
        self.connection = McpConnection(self.config.mcp_config(), stderr=stderr)
        self.on_event = on_event
        self._closing = None
        self._route_depth = 0
        self.routing = self._build_router()
        properties = {
            "title": {"type": "string", "description": "Describe this call's intent in the user's language."},
            "code": {"type": "string", "description": "Python code with top-level await."},
        }
        self.tools = [
            DesktopTool(
                self,
                name="computer_use",
                description=DESCRIPTION,
                properties=properties,
                required=["code"],
            ),
            DesktopTool(self, name="computer_use_reset", description=RESET_DESCRIPTION, properties={}, required=[]),
        ]
        if self.routing is not None:
            self.tools.append(
                DesktopTool(
                    self,
                    name="route_task",
                    description=ROUTE_TASK_DESCRIPTION,
                    properties={
                        "task": {"type": "string", "description": "The task to route, in the user's language."},
                        "app": {"type": "string", "description": "Optional target app hint."},
                        "arguments": {
                            "type": "object",
                            "description": "Optional path arguments matching the chosen path's schema.",
                        },
                        "subtasks": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional decomposition; each subtask is routed independently.",
                        },
                    },
                    required=["task"],
                )
            )

    def _build_router(self) -> Router | None:
        routing = self.config.routing
        if not routing.enabled:
            return None
        registry = PathRegistry()
        if routing.paths_file:
            registry.load_file(routing.paths_file)
        store = PathMetricsStore(persistence_path=routing.metrics_file) if routing.metrics_file else PathMetricsStore()
        policy = RoutingPolicy(
            min_confidence=routing.min_confidence,
            refuse_floor=routing.refuse_floor,
            refuse_after_attempts=routing.refuse_after_attempts,
            allow_destructive=routing.allow_destructive,
        )
        weights = ScoreWeights(
            time=routing.time_weight,
            cost=routing.cost_weight,
            exploration=routing.exploration,
            time_scale_s=routing.time_scale_s,
            cost_scale=routing.cost_scale,
            gui_ops_estimate=routing.gui_ops_estimate,
        )
        return Router(
            registry,
            RouterConfig(policy=policy, weights=weights),
            store=store,
            run_code=self._run_routed_code,
        )

    @property
    def system_prompt(self):
        return system_prompt(routing=self.routing is not None)

    def emit(self, kind, data):
        if self.on_event is not None:
            try:
                self.on_event(kind, data)
            except Exception:
                logger.exception("Computer Use observer failed after %s", kind)

    async def call(self, name, inputs):
        if name == "route_task":
            self.emit("tool_call", {"name": name, "arguments": inputs})
            output = await self.route_task(inputs)
            self.emit(
                "tool_result",
                {"name": name, "result": {"success": output.success, "content": output.data.get("content", "")}},
            )
            return output
        started = time.monotonic()
        self.emit("tool_call", {"name": name, "arguments": inputs})
        try:
            result = await self.connection.call(name, inputs)
        except asyncio.CancelledError:
            self.emit("tool_cancelled", {"name": name, "effect": "possible"})
            self._record_gui_sample(name, inputs, started, None)
            raise
        except Exception as error:
            logger.exception("Computer Use tool transport failed")
            self.emit("tool_error", {"name": name, "error": str(error), "effect": "possible"})
            self._record_gui_sample(name, inputs, started, None)
            raise
        self._record_gui_sample(name, inputs, started, result.isError is False)
        raw = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        self.emit("tool_result", {"name": name, "result": raw})
        converted = extract_mcp_tool_result_content(result, include_image_content=True, tool_name=name)
        data = converted.data if hasattr(converted, "data") else {"content": converted or ""}
        data = {**data, "content": render_payload_text(data) or "Execution returned no text."}
        data = {**data, "mcp_result": raw}
        return ToolOutput(success=not result.isError, data=data, error=getattr(converted, "error", None))

    def _record_gui_sample(self, name, inputs, started, success):
        routing = self.routing
        if routing is None or name != "computer_use" or self._route_depth > 0:
            return
        if not self.config.routing.auto_record_gui:
            return
        routing.store.record(
            OutcomeRecord(
                path_id=GUI_FALLBACK_ID,
                task=str(inputs.get("title") or "")[:200],
                latency_s=time.monotonic() - started,
                success=success,
                detail="operation-level sample of free computer_use",
            ),
            PathKind.GUI,
        )

    async def _run_routed_code(self, code):
        self._route_depth += 1
        try:
            output = await self.call("computer_use", {"title": "Routed path execution", "code": code})
        finally:
            self._route_depth -= 1
        return output.success, output.data.get("content", "")

    async def route_task(self, inputs):
        router = self.routing
        task = str(inputs.get("task") or "")
        if router is None:
            return ToolOutput(success=False, data={"content": "Routing is disabled."}, error="routing disabled")
        subtasks = inputs.get("subtasks")
        if subtasks is not None:
            subtasks = [str(item) for item in subtasks]
        try:
            result = await router.route(
                task,
                app=inputs.get("app"),
                arguments=inputs.get("arguments"),
                subtasks=subtasks,
            )
        except Exception as error:
            logger.exception("Routing failed")
            return ToolOutput(success=False, data={"content": f"Routing failed: {error}"}, error=str(error))
        lines = [render_plan(result.plan)]
        for execution in result.executions:
            state = execution.status
            lines.append(
                f"execution {execution.path_id}: {state}, verdict={execution.verdict}, "
                f"latency={execution.latency_s:.1f}s"
            )
            if execution.error:
                lines.append(f"  error: {execution.error}")
            if execution.output:
                lines.append(f"  output: {execution.output[:2000]}")
            if execution.status is ExecutionStatus.EFFECT_UNKNOWN:
                lines.append("  effect unknown: do not retry blindly; observe before deciding")
        lines.append(f"result: {result.summary or result.plan.reason}")
        success = result.success is True or (result.plan.decision == "execute" and result.success is None)
        return ToolOutput(
            success=success,
            data={"content": "\n".join(lines), "route": _route_payload(result)},
            error=None if success else result.summary,
        )

    async def reset(self):
        return await self.call("computer_use_reset", {})

    async def close(self):
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        await asyncio.shield(self._closing)

    async def _close(self):
        try:
            await self.connection.cancel_current()
            if self.connection.session is not None:
                async with asyncio.timeout(10):
                    await self.reset()
        finally:
            try:
                await self.connection.close()
            finally:
                for tool in self.tools:
                    if Runner.resource_mgr.get_tool(tool.card.id) is not None:
                        Runner.resource_mgr.remove_tool(tool.card.id)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        try:
            await self.close()
        except Exception:
            if exc_type is None:
                raise
            logger.exception("Computer Use cleanup failed while handling %s", exc_type.__name__)


def _route_payload(result):
    plan = result.plan

    def candidate_payload(candidate):
        return {
            "id": candidate.path.id,
            "kind": str(candidate.path.kind),
            "eligible": candidate.eligible,
            "score": candidate.score,
            "reasons": list(candidate.reasons),
            "breakdown": candidate.breakdown,
        }

    return {
        "decision": plan.decision,
        "reason": plan.reason,
        "chosen": plan.chosen.path.id if plan.chosen else None,
        "candidates": [candidate_payload(candidate) for candidate in plan.candidates],
        "subtasks": [
            {"index": subtask.index, "goal": subtask.goal, "decision": subtask.plan.decision}
            for subtask in plan.subtasks
        ],
        "success": result.success,
        "summary": result.summary,
        "executions": [
            {
                "path_id": execution.path_id,
                "status": str(execution.status),
                "verdict": str(execution.verdict),
                "latency_s": execution.latency_s,
                "error": execution.error,
                "output": execution.output[:2000],
            }
            for execution in result.executions
        ],
    }
