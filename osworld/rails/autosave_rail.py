# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Remind the model to save after a run of GUI edits in one window.

run_task_sdk.py scores the VM as it stands when the budget ends, so an application
full of unsaved edits scores zero. This rail counts GUI edits per target window
and, once ``every`` of them have landed in one window without a save shortcut
in between, adds a transient reminder (see transient.py) before the next model
call. The counter resets on the reminder as well as on a save, so an ignored
reminder repeats ``every`` edits later.

It nudges rather than pressing Ctrl+S itself: a blind keystroke is the wrong
thing in too many places (in GIMP Ctrl+S opens the .xcf save dialog and the
task wants an export; in Chrome it opens Save Page; inside a dialog it does
nothing or the wrong thing), and it would leave a modal on screen the model
does not know about. The model sees the reminder, picks the app's own save,
and sees the result on the next read.

What counts: this repo's agent acts through computer_use, whose result carries
one action receipt per input sent, with its kind (intent) and target window.
The edits are the kinds the fork agent counted as its click, type_text,
press_key, hotkey and drag tools: click and action (an accessibility press),
insert, replace and paste, key, drag. Scrolling, focusing, opening a menu and
launching are not edits; reads send nothing. Receipts do not name the key, so a
save shortcut is found in the script's press_key("...") calls; a call that saves
resets every window it sent input to. Shell work (subprocess) produces no
reminder: it writes to disk directly.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail

from osworld.rails.gui_step_cap_rail import ACTION_TOOL, receipts
from osworld.rails.transient import add_reminder, strip_reminders

logger = logging.getLogger(__name__)

EDIT_INTENTS = frozenset({"click", "action", "insert", "replace", "paste", "key", "drag"})
_CTRL = {"ctrl", "control"}
_PRESS_KEY = re.compile(r"press_key\(\s*(['\"])(.+?)\1")


def _code(args: Any) -> str:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return ""
    code = args.get("code") if isinstance(args, dict) else None
    return code if isinstance(code, str) else ""


def is_save_shortcut(key: str) -> bool:
    """Ctrl+S, Ctrl+Shift+S (save as) or Shift+Ctrl+E (GIMP export), written as press_key takes it ('ctrl+s')."""
    keys = {k.strip().lower() for k in key.split("+")}
    if not keys & _CTRL:
        return False
    return "s" in keys or ("e" in keys and "shift" in keys)


def saves(code: str) -> bool:
    """Whether a computer_use script presses a save shortcut."""
    return any(is_save_shortcut(m.group(2)) for m in _PRESS_KEY.finditer(code))


class AutosaveReminderRail(AgentRail):
    """After ``every`` GUI edits in one window with no save, remind the model to save."""

    priority = 30

    def __init__(self, every: int = 6, log: Callable[[str], None] = logger.info) -> None:
        super().__init__()
        self._every = max(1, int(every))
        self._log = log
        self._edits: dict[str, int] = {}   # window -> edits since its last save/reminder
        self.events: list[str] = []

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        if (getattr(ctx.inputs, "tool_name", "") or "") != ACTION_TOOL:
            return
        sent = [r for r in receipts(getattr(ctx.inputs, "tool_result", None)) if r.get("target")]
        if not sent:
            return
        if saves(_code(getattr(ctx.inputs, "tool_args", None))):
            for receipt in sent:
                self._edits[str(receipt["target"])] = 0
            return
        for receipt in sent:
            if receipt.get("intent") in EDIT_INTENTS:
                window = str(receipt["target"])
                self._edits[window] = self._edits.get(window, 0) + 1

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        context = ctx.context
        if context is None:
            return
        due = [(n, w) for w, n in self._edits.items() if n >= self._every]
        if not due:
            return
        count, window = max(due)
        self._edits[window] = 0
        self._note(f"{count} GUI edits in window {window} since its last save; reminding the model")
        await add_reminder(context, self.reminder(count, window))

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        if ctx.context is not None:
            strip_reminders(ctx.context)

    @staticmethod
    def reminder(count: int, window: str) -> str:
        return (
            "\n\n## Autosave reminder\n"
            f"You have made {count} GUI edits in window {window} since it was last saved. If that "
            "application holds unsaved work, save it now the way that app expects (Ctrl+S for "
            "documents; in GIMP use File > Export As or Overwrite, since Ctrl+S there writes .xcf), "
            "confirm from the screen that the save took effect, then continue. If nothing there "
            "needs saving, continue with the task."
        )

    def _note(self, message: str) -> None:
        self.events.append(message)
        self._log(f"[AutosaveReminderRail] {message}")
