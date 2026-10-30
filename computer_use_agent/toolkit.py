# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native openJiuwen tools backed by the existing Computer Use MCP service."""

import asyncio
import logging
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

logger = logging.getLogger(__name__)


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
        self.gaze_tool = None
        self.tools = [
            DesktopTool(
                self,
                name="computer_use",
                description=DESCRIPTION,
                properties={
                    "title": {"type": "string", "description": "Describe this call's intent in the user's language."},
                    "code": {"type": "string", "description": "Python code with top-level await."},
                },
                required=["code"],
            ),
            DesktopTool(self, name="computer_use_reset", description=RESET_DESCRIPTION, properties={}, required=[]),
        ]
        if self.config.gaze_connection_file is not None:
            from .gaze import GazeReadTool

            self.gaze_tool = GazeReadTool(self, self.config.gaze_connection_file)
            self.tools.append(self.gaze_tool)

    @property
    def system_prompt(self):
        return system_prompt()

    def emit(self, kind, data):
        if self.on_event is not None:
            try:
                self.on_event(kind, data)
            except Exception:
                logger.exception("Computer Use observer failed after %s", kind)

    async def call(self, name, inputs):
        self.emit("tool_call", {"name": name, "arguments": inputs})
        try:
            result = await self.connection.call(name, inputs)
        except asyncio.CancelledError:
            self.emit("tool_cancelled", {"name": name, "effect": "possible"})
            raise
        except Exception as error:
            logger.exception("Computer Use tool transport failed")
            self.emit("tool_error", {"name": name, "error": str(error), "effect": "possible"})
            raise
        raw = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        self.emit("tool_result", {"name": name, "result": raw})
        converted = extract_mcp_tool_result_content(result, include_image_content=True, tool_name=name)
        data = converted.data if hasattr(converted, "data") else {"content": converted or ""}
        data = {**data, "content": render_payload_text(data) or "Execution returned no text."}
        data = {**data, "mcp_result": raw}
        return ToolOutput(success=not result.isError, data=data, error=getattr(converted, "error", None))

    async def reset(self):
        return await self.call("computer_use_reset", {})

    async def close(self):
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        await asyncio.shield(self._closing)

    async def _close(self):
        try:
            if self.gaze_tool is not None:
                await self.gaze_tool.close()
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
