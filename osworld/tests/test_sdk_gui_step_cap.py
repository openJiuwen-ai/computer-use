# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The GUI step cap counts what the leaderboard counts: inputs sent to the VM, not tool calls.

This repo's agent sends any number of inputs from one computer_use script; the SDK returns one action receipt per
input and none for a read. Counting calls instead would let a script of ten clicks cost one step and make every read
cost one (the fork agent counted its click/type_text/... tools and left reads free).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

cap = pytest.importorskip("osworld.rails.gui_step_cap_rail")


def _result(receipts):
    raw = {"content": []}
    if receipts is not None:
        raw["_meta"] = {"computer-use": {"receipts": receipts}}
    return SimpleNamespace(data={"content": "ok", "mcp_result": raw})


class _Ctx:
    def __init__(self, tool_name, receipts):
        self.inputs = SimpleNamespace(tool_name=tool_name, tool_result=_result(receipts))
        self.finished = None

    def request_force_finish(self, result):
        self.finished = result


def _after(rail, tool_name, receipts):
    ctx = _Ctx(tool_name, receipts)
    asyncio.run(rail.after_tool_call(ctx))
    return ctx


def test_each_input_a_script_sends_is_one_step():
    rail = cap.GuiStepCapRail(limit=100, log=lambda _: None)
    _after(rail, "computer_use", [{"status": "dispatched"}] * 3)
    assert rail.steps == 3


def test_reads_and_other_tools_are_free():
    rail = cap.GuiStepCapRail(limit=100, log=lambda _: None)
    _after(rail, "computer_use", [])  # app.read(): no receipt
    _after(rail, "computer_use", None)
    _after(rail, "inspect_file", [{"status": "dispatched"}])  # not an input to the VM's GUI
    assert rail.steps == 0


def test_the_run_stops_once_the_budget_is_spent():
    # chrome b070486d ran 213 GUI actions on a site it could not reach; a capped run is graded as it stands.
    rail = cap.GuiStepCapRail(limit=4, log=lambda _: None)
    assert _after(rail, "computer_use", [{}] * 3).finished is None
    stopped = _after(rail, "computer_use", [{}] * 2)  # a script cannot stop half-way: 5 >= 4 after this call
    assert stopped.finished == {"output": "Stopped at the 4-step GUI action limit.", "result_type": "answer"}


def test_the_budget_notice_is_shown_for_one_model_call():
    # calc 51719eea ran into the cap without knowing there was one.
    added = []

    class Context:
        def __init__(self):
            self.messages = []

        async def add_messages(self, message):
            self.messages.append(message)
            added.append(message.content)

        def get_messages(self):
            return self.messages

        def pop_messages(self, n):
            del self.messages[-n:]

    rail = cap.GuiStepCapRail(limit=100, notice=True, log=lambda _: None)
    rail.steps = 7
    ctx = SimpleNamespace(context=Context())
    asyncio.run(rail.before_model_call(ctx))
    assert "7 of 100 GUI actions used; 93 left." in added[0]
    asyncio.run(rail.after_model_call(ctx))
    assert ctx.context.messages == []


def test_the_prompt_explains_the_budget_only_when_there_is_one(tmp_path, monkeypatch):
    vm_agent = pytest.importorskip("osworld.vm_agent")
    monkeypatch.setenv("OPENJIUWEN_BENCHMARK_INTEGRITY_MODE", "off")
    capped = vm_agent.osworld_parts("Do it.", str(tmp_path), routing=False, log=lambda _: None, max_gui_steps=100)
    assert capped["system_prompt"].endswith(cap.BUDGET_PROMPT.format(limit=100))
    assert any(isinstance(r, cap.GuiStepCapRail) for r in capped["rails"])
    uncapped = vm_agent.osworld_parts("Do it.", str(tmp_path), routing=False, log=lambda _: None)
    assert "GUI actions for this task" not in uncapped["system_prompt"]
    assert not any(isinstance(r, cap.GuiStepCapRail) for r in uncapped["rails"])


def test_the_budget_prompt_names_only_this_agents_tools():
    assert "computer_use" in cap.BUDGET_PROMPT
    for gone in ("get_window_state", "list_windows", "launch_app", "bring_to_front", "bash"):
        assert gone not in cap.BUDGET_PROMPT
