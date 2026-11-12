# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Desktop startup, context and failure guidance adapted from agent-core."""

import json

from openjiuwen.core.runner.callback import AbortError
from openjiuwen.core.single_agent.rail.base import AgentRail, ToolCallInputs

from .history import compact_observations, compact_screenshots, remember_observations

_ADVISE_AFTER = 3


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

    async def after_tool_call(self, ctx):
        inputs = ctx.inputs
        if not isinstance(inputs, ToolCallInputs):
            return
        if inputs.tool_name != "computer_use":
            self._failed_scripts.clear()
            return
        data = getattr(inputs.tool_result, "data", None)
        if not isinstance(data, dict):
            self._failed_scripts.clear()
            return
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
        if ctx.context is None:
            return
        messages = list(ctx.context.get_messages())
        changed = compact_observations(messages)
        if self._image_scopes:
            self._image_scopes, images_changed = compact_screenshots(messages, self._image_scopes)
            changed |= images_changed
        if changed:
            ctx.context.set_messages(messages)
