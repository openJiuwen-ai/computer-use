# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The autosave reminder: an app full of unsaved edits scores zero when the run ends.

The fork agent's scenarios (test_jiuwen_rails.py), on this repo's agent: edits are the action receipts of
computer_use results, and a save is a press_key("ctrl+s") (or Ctrl+Shift+S, Shift+Ctrl+E) in the script.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

autosave = pytest.importorskip("osworld.rails.autosave_rail")
AutosaveReminderRail = autosave.AutosaveReminderRail


class FakeContext:
    def __init__(self):
        self.messages = []

    async def add_messages(self, message):
        self.messages.append(message)

    def get_messages(self):
        return list(self.messages)

    def pop_messages(self, n):
        del self.messages[-n:]


def _result(receipts):
    return SimpleNamespace(data={"mcp_result": {"_meta": {"computer-use": {"receipts": receipts}}}})


async def call(rail, code, *receipts, tool="computer_use"):
    inputs = SimpleNamespace(tool_name=tool, tool_args=json.dumps({"code": code}), tool_result=_result(list(receipts)))
    await rail.after_tool_call(SimpleNamespace(inputs=inputs))


async def model_call(rails, context):
    """The reminders the model would see on one call; they are gone afterwards."""
    ctx = SimpleNamespace(context=context)
    for rail in rails:
        await rail.before_model_call(ctx)
    seen = [m.content for m in context.messages]
    for rail in rails:
        await rail.after_model_call(ctx)
    return seen


def edit(intent="click", window="Window 0"):
    return {"status": "dispatched", "intent": intent, "target": window}


def test_autosave_reminds_after_n_edits_in_one_window_then_resets():
    rail = AutosaveReminderRail(every=3, log=lambda _: None)
    context = FakeContext()

    async def scenario():
        await call(rail, "app.element('Bold').click()\napp.element('Italic').click()", edit(), edit())
        early = await model_call([rail], context)
        await call(rail, "app.type_text('hello')", edit("insert"))
        due = await model_call([rail], context)
        again = await model_call([rail], context)
        return early, due, again

    early, due, again = asyncio.run(scenario())
    assert early == []                                        # two edits: not yet
    assert len(due) == 1 and "3 GUI edits in window Window 0" in due[0]
    assert again == []                                        # counter restarted after the reminder
    assert context.messages == []


def test_autosave_counter_resets_on_a_save_shortcut():
    rail = AutosaveReminderRail(every=2, log=lambda _: None)
    context = FakeContext()

    async def scenario():
        await call(rail, "app.element('Cell').click()", edit())
        await call(rail, "app.press_key('ctrl+s')", edit("key"))
        after_save = await model_call([rail], context)
        await call(rail, "app.press_key('Return')", edit("key"))
        await call(rail, "app.press_key('shift+ctrl+e')", edit("key"))  # GIMP export
        after_export = await model_call([rail], context)
        return after_save, after_export

    after_save, after_export = asyncio.run(scenario())
    assert after_save == [] and after_export == []


def test_autosave_ignores_shell_work_reads_and_scrolling():
    """Task 054 style: shell and file work, reads, no edits in an app window."""
    rail = AutosaveReminderRail(every=2, log=lambda _: None)
    context = FakeContext()

    async def scenario():
        for _ in range(5):
            await call(rail, "import subprocess\nsubprocess.run(['ls'])")       # no receipt
            await call(rail, "obs = app.read()")                                 # no receipt
            await call(rail, "print(1)", tool="inspect_file")
            await call(rail, "app.scroll('down', at=p)", edit("scroll"))
            await call(rail, "app.element('x').click()", {"status": "dispatched", "intent": "click"})  # no window
        return await model_call([rail], context)

    assert asyncio.run(scenario()) == []


def test_two_reminder_rails_both_get_seen_and_both_get_stripped():
    """after_model_call hooks run in priority order; the first must clear both reminders."""
    first = AutosaveReminderRail(every=1, log=lambda _: None)
    second = AutosaveReminderRail(every=1, log=lambda _: None)
    context = FakeContext()

    async def scenario():
        for rail in (first, second):
            await call(rail, "app.element('x').click()", edit())
        return await model_call([first, second], context)

    assert len(asyncio.run(scenario())) == 2
    assert context.messages == []


@pytest.mark.parametrize("key, saves", [("ctrl+s", True), ("ctrl+shift+s", True), ("shift+ctrl+e", True),
                                        ("Control+S", True), ("ctrl+e", False), ("s", False), ("ctrl+c", False)])
def test_save_shortcuts(key, saves):
    assert autosave.is_save_shortcut(key) is saves


def test_the_agent_gets_the_reminder_by_default(tmp_path, monkeypatch):
    vm_agent = pytest.importorskip("osworld.vm_agent")
    monkeypatch.setenv("OPENJIUWEN_BENCHMARK_INTEGRITY_MODE", "off")
    rails = vm_agent.osworld_parts("Do it.", str(tmp_path), routing=False, log=lambda _: None,
                                   autosave_every=6)["rails"]
    assert any(isinstance(r, AutosaveReminderRail) for r in rails)
