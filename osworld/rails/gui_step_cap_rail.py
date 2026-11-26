# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Stop the agent after ``limit`` GUI actions: the leaderboard's 100-step budget.

The agentic-framework entries on OSWorld-Verified (Pointer, Intelligence-Indeed)
count one step per GUI action sent to the VM; reads, shell and file tools are
free. The runners had no cap at all, so chrome b070486d (gate1021f, 2026-10-21)
ran 213 GUI actions and 45+ min on a site it could not reach. At ``limit`` this
rail force-finishes the run; the parent then scores the VM as it stands, which is
what a capped leaderboard run does.

What counts: this repo's agent acts through computer_use, one Python script per
call that may send any number of inputs. The SDK returns one action receipt for
every input it sends (click, typing, a key, a scroll, a drag), and none for a
read, so each receipt is one GUI action -- what the fork agent counted as one
call of its click/type_text/press_key/... tools. A script cannot be stopped
half-way, so the run stops after the call that reaches the limit.

With ``notice`` the model is also told, before every model call, how many GUI
actions it has used and how many are left. calc 51719eea ran into the cap in both
arms of round 3 without knowing there was one; Anthropic's reference OSWorld agent
puts "You have a maximum of N steps" in its prompt.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail

from osworld.rails.transient import add_reminder, strip_reminders

logger = logging.getLogger(__name__)

ACTION_TOOL = "computer_use"

BUDGET_PROMPT = (
    "\n\nYou have a budget of {limit} GUI actions for this task: each input your computer_use code sends -- every "
    "click, type_text, paste, press_key, scroll and drag -- counts as one. Looking at the screen (app.read, "
    "list_apps), reading files, inspect_file, read_skill and shell commands do not count. When the budget is used "
    "up the run stops and the computer is graded as it stands, so plan the shortest sequence of actions that "
    "reaches and saves the result."
)


def receipts(tool_result: Any) -> list[dict]:
    """The action receipts of one computer_use result: one per input the SDK sent, none for a read."""
    data = getattr(tool_result, "data", None)
    raw = data.get("mcp_result") if isinstance(data, dict) else None
    meta = raw.get("_meta") if isinstance(raw, dict) else None
    computer_use = meta.get("computer-use") if isinstance(meta, dict) else None
    found = computer_use.get("receipts") if isinstance(computer_use, dict) else None
    return [r for r in found if isinstance(r, dict)] if isinstance(found, list) else []


def action_receipts(tool_result: Any) -> int:
    """How many inputs one computer_use call sent."""
    return len(receipts(tool_result))


class GuiStepCapRail(AgentRail):
    """Force-finish the run once ``limit`` GUI actions have been taken; optionally show the count."""

    priority = 90  # ahead of the reminder rails, so a capped run stops before more are queued

    def __init__(self, limit: int = 100, notice: bool = False, log: Callable[[str], None] = logger.info) -> None:
        super().__init__()
        self._limit = max(1, int(limit))
        self._notice = notice
        self._log = log
        self.steps = 0

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        if (getattr(ctx.inputs, "tool_name", "") or "") != ACTION_TOOL:
            return
        sent = action_receipts(getattr(ctx.inputs, "tool_result", None))
        if not sent:
            return
        self.steps += sent
        if self.steps >= self._limit:
            self._log(f"[GuiStepCapRail] {self.steps} GUI actions: step limit reached, stopping the run")
            ctx.request_force_finish({"output": f"Stopped at the {self._limit}-step GUI action limit.",
                                      "result_type": "answer"})

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if self._notice and ctx.context is not None:
            await add_reminder(ctx.context, self.reminder(self.steps, self._limit))

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        if self._notice and ctx.context is not None:
            strip_reminders(ctx.context)

    @staticmethod
    def reminder(used: int, limit: int) -> str:
        return f"\n\n## GUI action budget\n{used} of {limit} GUI actions used; {max(0, limit - used)} left."
