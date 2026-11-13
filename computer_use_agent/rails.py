# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Desktop startup, context and failure guidance adapted from agent-core."""

import json
import logging

from openjiuwen.core.runner.callback import AbortError
from openjiuwen.core.single_agent.rail.base import AgentRail, ToolCallInputs

from .history import compact_observations, compact_screenshots, remember_observations

_ADVISE_AFTER = 3
logger = logging.getLogger(__name__)


class ComputerUseRail(AgentRail):
    """Retain desktop evidence and guide recovery from actual SDK results."""

    def __init__(self, toolkit):
        super().__init__()
        self._toolkit = toolkit
        self._image_scopes = {}
        self._failed_scripts = {}

    async def before_invoke(self, ctx):
        self._failed_scripts.clear()
        try:
            await self._toolkit.check_desktop()
        except Exception as error:
            detail = str(error) or type(error).__name__
            raise AbortError(
                f"Desktop startup check failed. Check the native backend and desktop session. {detail}"
            ) from error
        compiler = self._toolkit.compiler
        query = ctx.inputs.query
        if compiler is not None and isinstance(query, str) and query.strip():
            source = compiler.begin(ctx.inputs.invocation_id, ctx.inputs.conversation_id, query)
            try:
                request = query.casefold()
                wants_draft = "草稿" in request or "draft" in request or (
                    "编译" in request and "函数" in request or "compiled" in request and "function" in request
                )
                source.offered = compiler.catalog(include_drafts=wants_draft)
            except (OSError, KeyError, TypeError, ValueError):
                logger.warning("Published Compile revision is unavailable", exc_info=True)
            source.can_compile = compiler.latest_source(conversation_id=source.conversation_id) is not None
            ctx.extra["compile_turn_token"] = compiler.turn.set(source)

    async def after_invoke(self, ctx):
        token = ctx.extra.pop("compile_turn_token", None)
        if token is not None:
            compiler = self._toolkit.compiler
            source = compiler.turn.get()
            compiler.turn.reset(token)
            compiler.finish(source, ctx.inputs.result)

    async def after_tool_call(self, ctx):
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return
        # Tool callbacks inherit the active turn from the outer DeepAgent.
        compiler = self._toolkit.compiler
        source = compiler.turn.get() if compiler is not None else None
        if source is not None and inputs.tool_name in ("computer_use", "route_task", "run_compiled", "compile_task"):
            source.offered = None
            source.can_compile = False
        if source is not None and inputs.tool_name in ("compile_task", "run_compiled"):
            result = inputs.tool_result
            data = getattr(result, "data", None)
            if isinstance(data, dict):
                answer = data.get("content" if inputs.tool_name == "compile_task" else "verified_answer")
                if answer:
                    ctx.request_force_finish({"result_type": "answer", "output": answer})
        if inputs.tool_name != "computer_use":
            self._failed_scripts.clear()
            return
        data = getattr(inputs.tool_result, "data", None)
        if not isinstance(data, dict):
            self._failed_scripts.clear()
            return
        if source is not None:
            self._toolkit.compiler.record(source, inputs.tool_args, data)
        for item in data.get("multimodal", ()):
            scope = item.get("observation_scope")
            if scope:
                label = f"Image loaded from {item['source']}: {item['source_path']}"
                self._image_scopes[label] = (scope, item["data_url"])

        note = self._failure_advisory(inputs, data.get("execution_progress") or {})
        if note and inputs.tool_msg is not None and isinstance(inputs.tool_msg.content, str):
            inputs.tool_msg.content += "\n\n" + note
        raw = data.get("mcp_result")
        if isinstance(raw, dict):
            remember_observations(inputs.tool_msg, raw)

    def _failure_advisory(self, inputs, progress):
        failure = progress.get("failure") or {}
        if progress.get("completion") == "unknown" or failure.get("effect") in ("possible", "partial"):
            self._failed_scripts.clear()
            return "Observe the affected state before retrying. This call may already have changed it."
        if failure.get("receipt") or progress.get("receipts"):
            self._failed_scripts.clear()
            return None
        arguments = inputs.tool_args
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                return None
        code = arguments.get("code") if isinstance(arguments, dict) else None
        error = getattr(inputs.tool_result, "error", None)
        if not isinstance(code, str):
            return None
        previous, count = self._failed_scripts.pop(code, (None, 0))
        finished_with_error = progress.get("completion") == "finished" and progress.get("isError")
        if finished_with_error and failure.get("effect") == "none" and isinstance(error, str):
            identity = (failure.get("code"), failure.get("target"), error)
            count = count + 1 if identity == previous else 1
            self._failed_scripts[code] = (identity, count)
            if count % _ADVISE_AFTER == 0:
                return (
                    f"This exact computer_use script returned the same no-effect failure {count} times this turn. "
                    "Use the available evidence to change the approach before retrying."
                )
        return None

    async def before_model_call(self, ctx):
        if self._toolkit.compiler is not None and ctx.inputs.tools:
            source = self._toolkit.compiler.turn.get()
            offered = []
            for tool in ctx.inputs.tools:
                if tool.name == "compile_task" and (source is None or not source.can_compile):
                    continue
                if tool.name == "run_compiled":
                    if source is None or not source.offered:
                        continue
                    tool = tool.model_copy(
                        update={
                            "description": (
                                "Call this first when the entire request matches an entry in the catalog. "
                                "Supply the current task's inputs. Set reply_language for the Calc profile. "
                                "Use a ready_to_validate entry only when the user explicitly asks to run the "
                                "draft on a new task. Never use it to re-run the original task as a test.\n"
                                + json.dumps(source.offered, ensure_ascii=False)
                            )
                        }
                    )
                offered.append(tool)
            ctx.inputs.tools = offered
        if ctx.context is None:
            return
        messages = list(ctx.context.get_messages())
        changed = compact_observations(messages)
        if self._image_scopes:
            self._image_scopes, images_changed = compact_screenshots(messages, self._image_scopes)
            changed |= images_changed
        if changed:
            ctx.context.set_messages(messages)
