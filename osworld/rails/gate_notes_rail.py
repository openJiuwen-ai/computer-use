# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Show the pre-task check's notes before every model call, not only in the first message.

Intelligence-Indeed's executor gets the gate's notes ("Skill basis quotes are
binding") in every turn; ours got them once, in the query, which is many turns
back after a long run. This adds them as a transient reminder before each model
call and strips it after, so the context keeps one copy, at the end, next to the
latest screenshot.
"""
from __future__ import annotations

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail

from osworld.rails.transient import add_reminder, strip_reminders

GATE_NOTES_HEADER = ("\n\n## Notes from the pre-task check (repeated every step)\n"
                     "The rules quoted under Skill basis are binding.\n\n")


class GateNotesRail(AgentRail):
    priority = 80

    def __init__(self, notes: str) -> None:
        super().__init__()
        self._text = GATE_NOTES_HEADER + notes.strip()

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if ctx.context is not None:
            await add_reminder(ctx.context, self._text)

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        if ctx.context is not None:
            strip_reminders(ctx.context)
