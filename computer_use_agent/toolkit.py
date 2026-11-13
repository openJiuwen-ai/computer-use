# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native openJiuwen tools backed by the existing Computer Use MCP service."""

import asyncio
import json
import logging
import time
from pathlib import Path
from uuid import uuid4

from computer_use_mcp.instructions import CODE_DESCRIPTION, DESCRIPTION, RESET_DESCRIPTION, TITLE_DESCRIPTION
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.foundation.tool.base import render_payload_text
from openjiuwen.core.foundation.tool.mcp.base import extract_mcp_tool_result_content
from openjiuwen.core.foundation.tool.schema import ToolOutput
from openjiuwen.core.runner.runner import Runner

from .activity import DesktopPause, build_idle_probe
from .compile.calc import ID as COMPILED_PROGRAM_ID
from .compile.compiler import Compiler, describe_result
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
route. It executes the chosen registered path itself and reports the outcome. If it chooses gui://fallback,
no GUI task has been executed; continue with computer_use. If the decision is refuse, explain the reason and ask
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

    def __init__(self, config: AgentConfig | None = None, *, on_event=None, stderr=None, compile_task=None):
        self.id = uuid4().hex
        self.config = config or AgentConfig()
        self.connection = McpConnection(self.config.mcp_config(), stderr=stderr)
        self.on_event = on_event
        self._closing = None
        self._desktop_checked = False
        self._desktop_check_lock = asyncio.Lock()
        self._pause = DesktopPause(build_idle_probe()) if self.config.pause_on_user_input else None
        self._route_depth = 0
        self._compile_task = compile_task
        self.compiler = (
            Compiler(
                Path(self.config.compile_dir or Path.home() / ".computer-use" / "agent" / "compile"),
                self._run_program_code,
                self.emit,
            )
            if self.config.compile_enabled
            else None
        )
        self.routing = self._build_router()
        self.tools = [
            DesktopTool(
                self,
                name="computer_use",
                description=DESCRIPTION,
                properties={
                    "title": {"type": "string", "description": TITLE_DESCRIPTION},
                    "code": {"type": "string", "description": CODE_DESCRIPTION},
                },
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
        if self.compiler is not None:
            self.tools.append(
                DesktopTool(
                    self,
                    name="run_compiled",
                    description="Run a compiled function whose offered description matches the entire current request.",
                    properties={
                        "program_id": {"type": "string", "description": "Program ID from the offered catalog."},
                        "parameters": {
                            "type": "object",
                            "description": "Current request values using the offered input schema. Use {} for no inputs.",
                        },
                        "reply_language": {
                            "type": "string",
                            "enum": ["zh", "en"],
                            "description": (
                                "For a complete matching task, use zh for Chinese or en for English. "
                                "The Agent returns the verified result in this language on success."
                            ),
                        },
                    },
                    required=["program_id", "parameters"],
                )
            )
            if self._compile_task is not None:
                self.tools.append(
                    DesktopTool(
                        self,
                        name="compile_task",
                        description="Compile the last completed desktop task in this conversation. Use only on explicit request.",
                        properties={},
                        required=[],
                    )
                )

    def _build_router(self) -> Router | None:
        routing = self.config.routing
        if not routing.enabled:
            return None
        registry = PathRegistry()
        if routing.paths_file:
            registry.load_file(routing.paths_file)
        store = PathMetricsStore(persistence_path=self._metrics_path())
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
            RouterConfig(policy=policy, weights=weights, global_input_allowed=self.config.allow_global_input),
            store=store,
            run_code=self._run_routed_code,
        )

    def _metrics_path(self):
        routing = self.config.routing
        if routing.metrics_file:
            return routing.metrics_file
        if not self.config.workspace:
            return None
        return Path(self.config.workspace) / "routing" / "metrics.jsonl"

    @property
    def system_prompt(self):
        return system_prompt(routing=self.routing is not None)

    def emit(self, kind, data):
        if self.on_event is not None:
            try:
                self.on_event(kind, data)
            except Exception:
                logger.exception("Computer Use observer failed after %s", kind)

    async def check_desktop(self):
        if not self.config.check_desktop or self._desktop_checked:
            return
        async with self._desktop_check_lock:
            if self._desktop_checked:
                return
            self.emit("desktop_check", {})
            async with asyncio.timeout(45):
                result = await self.connection.call(
                    "computer_use", {"code": "len(computer.list_apps())", "title": "Check desktop availability"}
                )
            if result.isError:
                detail = next((block.text for block in reversed(result.content) if block.type == "text"), "")
                raise RuntimeError(detail[-1000:] or "The backend could not list desktop applications.")
            self._desktop_checked = True

    async def call(self, name, inputs, *, _program=False):
        if name == "compile_task":
            self.emit("tool_call", {"name": name, "arguments": inputs})
            source = self.compiler.turn.get() if self.compiler is not None else None
            chinese = source is not None and any("\u4e00" <= char <= "\u9fff" for char in source.query)
            try:
                if source is None or not source.can_compile or self._compile_task is None:
                    raise ValueError("No completed desktop task was found in this conversation")
                result = await self._compile_task(conversation_id=source.conversation_id)
                text = describe_result(result, chinese=chinese)
                success = True
            except (OSError, ValueError, RuntimeError) as error:
                success, text = False, str(error)
            self.emit(
                "tool_result",
                {
                    "name": name,
                    "result": {
                        "isError": not success,
                        "content": [{"type": "text", "text": text}],
                    },
                },
            )
            return ToolOutput(success=success, data={"content": text}, error=None if success else text)
        if name == "run_compiled":
            self.emit("tool_call", {"name": name, "arguments": inputs})
            result = await self.run_program(inputs["program_id"], inputs["parameters"])
            success = result["status"] == "succeeded"
            text = result["detail"]
            answer = None
            if success and "value" in result:
                value = result["value"]
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            if "publication" in result:
                source = self.compiler.turn.get() if self.compiler is not None else None
                chinese = source is not None and any("\u4e00" <= char <= "\u9fff" for char in source.query)
                text += "\n" + describe_result(result["publication"], chinese=chinese)
            if success and inputs["program_id"] == COMPILED_PROGRAM_ID:
                text = "The saved workbook was independently verified. " + text
                answer = _compiled_answer(result["parameters"], inputs.get("reply_language"))
            if result["fallback_allowed"]:
                text += "\nThe program sent no desktop input. Continue with the regular task workflow."
            elif not success:
                text += "\nObserve the original target and determine the remaining work before making changes."
            self.emit(
                "tool_result",
                {"name": name, "result": {"isError": not success, "content": [{"type": "text", "text": text}]}},
            )
            return ToolOutput(
                success=success, data={"content": text, "program_result": result, "verified_answer": answer}
            )
        if name == "route_task":
            self.emit("tool_call", {"name": name, "arguments": inputs})
            output = await self.route_task(inputs)
            self.emit(
                "tool_result",
                {"name": name, "result": {"success": output.success, "content": output.data.get("content", "")}},
            )
            return output
        self.emit("tool_call", {"name": name, "arguments": inputs})
        pause = self._pause if name == "computer_use" else None
        if pause is not None:
            try:
                advisory = await pause.wait(self.emit)
            except asyncio.CancelledError:
                self.emit(
                    "tool_cancelled",
                    {"name": name, "effect": "none", "error": "Call cancelled before dispatch."},
                )
                raise
            if advisory is not None:
                result = {"isError": True, "content": [{"type": "text", "text": advisory}]}
                self.emit("tool_result", {"name": name, "result": result})
                return ToolOutput(
                    success=False,
                    data={"content": advisory, "execution_progress": {"completion": "not_started"}},
                    error=advisory,
                )
        started = time.monotonic()
        try:
            result = await self.connection.call(name, inputs)
        except asyncio.CancelledError:
            self.emit("tool_cancelled", {"name": name, "effect": "possible"})
            if not _program:
                self._record_gui_sample(name, inputs, started, None)
            raise
        except Exception as error:
            logger.exception("Computer Use tool transport failed")
            self.emit("tool_error", {"name": name, "error": str(error), "effect": "possible"})
            if not _program:
                self._record_gui_sample(name, inputs, started, None)
            raise
        completed = time.monotonic()
        if not _program:
            self._record_gui_sample(name, inputs, started, result.isError is False)
        raw = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        self.emit("tool_result", {"name": name, "result": raw})
        converted = extract_mcp_tool_result_content(result, include_image_content=True, tool_name=name)
        data = converted.data if hasattr(converted, "data") else {"content": converted or ""}
        images = [block for block in raw["content"] if block["type"] == "image"]
        for index, (item, block) in enumerate(zip(data.get("multimodal", ()), images), start=1):
            metadata = block.get("_meta", {}).get("computer-use", {})
            identifier = metadata.get("imageId", str(index))
            caption = metadata.get("caption", "")
            # The framework renders source_path beside each image, including
            # images forwarded in a separate multimodal message.
            label = f"Image {identifier}"
            after_action = metadata.get("capturedAfterAction")
            if type(after_action) is int and after_action > 0:
                label += f"\nCaptured after Action #{after_action}."
            item["source_path"] = label + (f"\n{caption}" if caption else "")
            scope = metadata.get("observationScope")
            if isinstance(scope, str) and scope:
                item["observation_scope"] = scope
        data = {**data, "content": render_payload_text(data) or "Execution returned no text."}
        data = {**data, "mcp_result": raw}
        tool_metadata = raw.get("_meta", {}).get("computer-use")
        progress = tool_metadata.get("progress") if isinstance(tool_metadata, dict) else None
        if isinstance(progress, dict) and type(progress.get("executionId")) is int:
            data["execution_progress"] = progress
            if pause is not None:
                pause.record_input(started, completed, progress)
        return ToolOutput(success=not result.isError, data=data, error=getattr(converted, "error", None))

    async def _run_program_code(self, title, code):
        return await self.call("computer_use", {"title": title, "code": code}, _program=True)

    async def run_program(self, program_id, parameters, *, version=None):
        if self.compiler is None:
            raise RuntimeError("Compile is disabled")
        return await self.compiler.run(program_id, parameters, version=version)

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


def _compiled_answer(parameters, language):
    file = Path(parameters["document"]).name
    sheet, cells, decimals = parameters["sheet"], parameters["range"], parameters["decimals"]
    if language == "zh":
        return (
            f"已回读 {file}，确认 {sheet} 工作表的 {cells} 已保存为显示 {decimals} 位小数。"
            "原值和范围外的数字格式均保留。"
        )
    if language == "en":
        return (
            f"Verified the saved workbook {file}. {sheet}!{cells} displays {decimals} decimal places. "
            "Cell values and other number formats were preserved."
        )
    return None


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
