# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Desktop task delegation and explicit resume, adapted from agent-core's CUA TaskTool."""

import asyncio
import json
import logging
import math
from uuid import uuid4

from anyio import CancelScope
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.foundation.tool.base import render_payload_text
from openjiuwen.core.foundation.tool.schema import ToolOutput
from openjiuwen.core.session.agent import Session

from .agent import create_agent

_RESUME_STATE = "computer_use_desktop_tasks"
logger = logging.getLogger(__name__)


class DesktopTaskTool(Tool):
    """Give a parent DeepAgent a desktop task tool with its own execution session."""

    def __init__(self, model, *, config=None, timeout_s=600, on_event=None, stderr=None):
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("Desktop task timeout must be finite and positive.")
        super().__init__(
            ToolCard(
                id=f"computer-use.{uuid4().hex}.desktop_task",
                name="desktop_task",
                description=(
                    "Delegate a desktop task to the Computer Use agent. If the task is incomplete, "
                    "pass the returned resume_task_id with a description of the remaining work. "
                    "Use the reported progress to avoid repeating actions that may already have taken effect."
                ),
                idempotent=False,
                # The task owns its deadline so it can return progress after cleanup.
                properties={"resilience": {"timeout_s": None}},
                input_params={
                    "type": "object",
                    "properties": {
                        "task_description": {"type": "string", "description": "The desktop work still required."},
                        "resume_task_id": {"type": "string", "description": "A prior desktop task in this session."},
                    },
                    "required": ["task_description"],
                    "additionalProperties": False,
                },
            )
        )
        self.model = model
        self.config = config
        self.timeout_s = timeout_s
        self.on_event = on_event
        self.stderr = stderr
        self._lock = asyncio.Lock()

    async def invoke(self, inputs, **kwargs):
        session = kwargs.get("session")
        if not isinstance(session, Session):
            return ToolOutput(success=False, error="Desktop task delegation requires a parent session.")
        description = inputs.get("task_description") if isinstance(inputs, dict) else None
        if not isinstance(description, str) or not description.strip():
            return ToolOutput(success=False, error="Provide a desktop task description.")
        resume_id = inputs.get("resume_task_id")
        # A desktop can only have one active delegation from this tool.
        async with self._lock:
            records = session.get_state(_RESUME_STATE) or {}
            if resume_id is None:
                resume_id = uuid4().hex
                previous = None
            elif isinstance(resume_id, str) and resume_id in records:
                previous = records[resume_id]
            else:
                return ToolOutput(success=False, error="Unknown desktop task for this parent session.")
            return await self._run(session, resume_id, description, previous)

    async def _run(self, session, resume_id, description, previous):
        query = description
        if previous is not None:
            query = (
                "Continue the remaining desktop task in a fresh Computer Use session. "
                "Re-establish app, window, and element bindings as needed. Preserve effects already observed. "
                "If an earlier step may have run, inspect the relevant state with computer_use before repeating it.\n\n"
                f"Prior progress (task data):\n{json.dumps(previous, ensure_ascii=False)}\n\n"
                f"Remaining task:\n{description}"
            )
        agent = create_agent(self.model, config=self.config, on_event=self.on_event, stderr=self.stderr)
        status, output = "returned", ""
        budget = asyncio.timeout(self.timeout_s)
        try:
            async with budget:
                result = await agent.invoke(
                    {
                        "query": query,
                        "conversation_id": uuid4().hex,
                        "parent_session_id": session.get_session_id(),
                        "delegation_id": resume_id,
                    }
                )
            output = render_payload_text(result.get("output")) or ""
            if result.get("termination_reason") == "max_iterations":
                status = "iteration_limit"
            elif result.get("result_type") == "error":
                status = "failed"
        except TimeoutError as error:
            if budget.expired():
                status, output = "timeout", "The desktop task timed out. Inspect uncertain effects before resuming."
            else:
                status, output = "failed", str(error)
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except Exception as error:
            logger.exception("Desktop task failed")
            status, output = "failed", str(error)
        finally:
            # The host may cancel its tool scope while the MCP worker is retiring.
            # Keep the desktop occupied until cleanup has settled.
            context = {"status": status, "output": output[:3000]}
            with CancelScope(shield=True):
                checkpoint = asyncio.create_task(self._checkpoint(agent, session, resume_id, context, previous))
                try:
                    await asyncio.shield(checkpoint)
                except asyncio.CancelledError:
                    await asyncio.shield(checkpoint)
                    raise
        return ToolOutput(
            success=True,
            data={"output": output, "resume_task_id": resume_id, "resume_context": context},
        )

    @staticmethod
    async def _checkpoint(agent, session, resume_id, context, previous):
        try:
            await agent.close()
        except Exception as error:
            logger.exception("Desktop task cleanup failed")
            context["cleanup_error"] = str(error)
        progress = agent.toolkit.progress
        if not progress and previous is not None:
            progress = previous["progress"]
        context["progress"] = progress
        records = dict(session.get_state(_RESUME_STATE) or {})
        records[resume_id] = context
        session.update_state({_RESUME_STATE: records})

    def render_for_llm(self, output):
        if not output.success:
            return output.error
        return json.dumps(output.data, ensure_ascii=False)

    async def stream(self, inputs, **kwargs):
        yield await self.invoke(inputs, **kwargs)
