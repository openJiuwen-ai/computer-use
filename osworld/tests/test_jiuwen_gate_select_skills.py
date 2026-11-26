# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The gate loads skills with select_skills (all of them when none of its names is a skill), writes
three-section notes, and its notes are shown again before every model call.
"""
from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace

import pytest

gate = pytest.importorskip("osworld.gate")
notes_rail = pytest.importorskip("osworld.rails.gate_notes_rail")

KNOWN = [{"name": n, "description": n, "directory": f"/skills/{n}"} for n in ("chrome", "gimp", "os")]


@pytest.fixture(autouse=True)
def fake_skills(monkeypatch):
    # skills_section, which select_skills returns, lives in osworld/tools/osworld_tools.py.
    from osworld.tools import osworld_tools
    for module in (gate, osworld_tools):
        monkeypatch.setattr(module, "list_skills", lambda: KNOWN)
        monkeypatch.setattr(module, "skill_body", lambda path, version: f"BODY of {path}")


def _select(selected):
    return gate.make_select_skills_tool(selected, "v1")


def test_select_skills_loads_the_named_skills_and_ignores_unknown_names():
    selected = []
    out = asyncio.run(_select(selected).invoke({"names": "gimp, photoshop"}))
    assert selected == ["gimp"] and f"BODY of {os.path.join('/skills/gimp', 'SKILL.md')}" in str(out)


def test_select_skills_falls_back_to_every_skill_when_none_matches():
    selected = []
    out = asyncio.run(_select(selected).invoke({"names": "photoshop"}))
    assert selected == ["chrome", "gimp", "os"] and "every skill is loaded" in str(out)


def test_a_second_call_only_adds():
    selected = ["gimp"]
    asyncio.run(_select(selected).invoke({"names": "gimp os"}))
    assert selected == ["gimp", "os"]


THREE = ("VERDICT: INFEASIBLE\nSKILLS: gimp\n### Skill basis\n- [gimp] \"GIMP 2.10 has no batch feature\"\n"
         "### Probe evidence\n- gimp --version: 2.10.30\n### Feasibility\nGIMP 2.10 has no CMYK mode.")


def test_three_section_notes_drive_the_binding_verdict():
    result = gate.parse_gate_output(THREE)
    assert result.binding_infeasible and result.infeasible_reason == "GIMP 2.10 has no CMYK mode."
    assert "Milestones" not in gate.GATE_PROMPT and "### Feasibility" in gate.GATE_PROMPT


def test_run_gate_records_the_selected_skills_or_all(monkeypatch, tmp_path):
    class _Answer:
        type = "answer"
        payload = {"output": "VERDICT: FEASIBLE\n### Skill basis\n- none\n### Probe evidence\n- ok\n"
                             "### Feasibility\nok"}

    class _Agent:
        async def stream(self, inputs):
            yield _Answer()

    monkeypatch.setattr(gate, "create_deep_agent", lambda *a, **k: _Agent())
    monkeypatch.setattr(gate, "dump_trajectory", lambda *a, **k: None)
    result = asyncio.run(gate.run_gate(object(), "Do X.", tools=[], rails=[], env_block="", workspace=str(tmp_path),
                                       results_dir=str(tmp_path), session_prefix="t", log=lambda _m: None))
    # The gate never called select_skills and wrote no SKILLS line: every skill goes to the doer.
    assert result.skills == ["chrome", "gimp", "os"]
    with open(tmp_path / "gate.json", encoding="utf-8") as f:
        assert json.load(f)["skills"] == ["chrome", "gimp", "os"]


def test_gate_notes_are_added_before_each_model_call_and_removed_after():
    messages = []

    class _Ctx:
        async def add_messages(self, m):
            messages.append(m)

        @staticmethod
        def get_messages():
            return messages

        @staticmethod
        def pop_messages(n):
            del messages[-n:]

    rail = notes_rail.GateNotesRail("### Skill basis\n- [gimp] \"x\"")
    cb = SimpleNamespace(context=_Ctx())
    asyncio.run(rail.before_model_call(cb))
    assert len(messages) == 1 and "Skill basis" in messages[0].content and "binding" in messages[0].content
    asyncio.run(rail.after_model_call(cb))
    assert messages == []


def test_the_snapshot_carries_the_vm_date():
    from osworld.env_snapshot import SNAPSHOT_CMD, format_snapshot, parse_sections
    assert "@@DATE@@" in SNAPSHOT_CMD
    block = format_snapshot(parse_sections("@@OS@@\nUbuntu\n@@DATE@@\nFriday, October 23, 2026\n"))
    assert "Today (the VM's clock):\nFriday, October 23, 2026" in block


class _RecordingEnv:
    def __init__(self):
        self.commands = []

    async def run_command(self, command, timeout_sec=20):
        self.commands.append(command)
        return SimpleNamespace(stdout="PNG image data", stderr="")


def test_file_type_leaves_tilde_to_the_guest_shell():
    env = _RecordingEnv()
    asyncio.run(gate.make_file_type_tool(env).invoke({"path": "~/Desktop/a b.png"}))
    assert env.commands[0].startswith("file -b -- \"$HOME\"/'Desktop/a b.png';")
    asyncio.run(gate.make_file_type_tool(env).invoke({"path": "/home/user/x.png"}))
    assert "file -b -- /home/user/x.png;" in env.commands[1]
