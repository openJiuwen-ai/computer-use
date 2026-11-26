# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""osworld/vm_agent.py: this repo's agent gets the OSWorld tools, rails and prompt, nothing else.

Runs with this repo's [agent] extra on Python 3.12 (the VM's agent environment), e.g.
    uv run --isolated -p 3.12 --extra agent --with pytest --with opentelemetry-exporter-otlp-proto-common \
        pytest osworld/tests/test_sdk_vm_agent.py
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

vm_agent = pytest.importorskip("osworld.vm_agent")
from openjiuwen.core.single_agent.rail.base import ToolCallInputs  # noqa: E402

from computer_use_agent.prompt import system_prompt  # noqa: E402
from osworld.benchmark_integrity import BENCHMARK_INTEGRITY_MODE_ENV, BENCHMARK_INTEGRITY_PROMPT  # noqa: E402
from osworld.rails.benchmark_integrity_rail import BenchmarkIntegrityRail  # noqa: E402
from osworld.rails.trajectory_rail import TrajectoryStreamRail  # noqa: E402
from osworld.tools.infeasible import MARKER_NAME  # noqa: E402


def test_the_agent_can_declare_a_task_infeasible(tmp_path, monkeypatch):
    # OSWorld scores an infeasible task 1 only through FAIL; without this tool every such task scores 0.
    monkeypatch.delenv(BENCHMARK_INTEGRITY_MODE_ENV, raising=False)
    parts = vm_agent.osworld_parts("Do it.", str(tmp_path), routing=False)
    tool = next(t for t in parts["tools"] if t.card.name == "declare_infeasible")
    out = asyncio.run(tool.invoke({"reason": "GIMP 2.10 has no CMYK mode"}))
    assert out.success
    # The runner reads this file and records FAIL before grading.
    assert json.loads((tmp_path / MARKER_NAME).read_text(encoding="utf-8")) == {"reason": "GIMP 2.10 has no CMYK mode"}


def test_the_integrity_guard_is_on_by_default_and_in_the_prompt(tmp_path, monkeypatch):
    monkeypatch.delenv(BENCHMARK_INTEGRITY_MODE_ENV, raising=False)
    monkeypatch.setenv("OSWORLD_CLIENT_PASSWORD", "password")
    parts = vm_agent.osworld_parts("Do it.", str(tmp_path), routing=False)
    assert any(isinstance(r, BenchmarkIntegrityRail) for r in parts["rails"])
    prompt = parts["system_prompt"]
    # The agent's own prompt stays first and whole; the OSWorld text only follows it.
    assert prompt.startswith(system_prompt(routing=False))
    assert BENCHMARK_INTEGRITY_PROMPT in prompt
    assert "sudo -S" in prompt and "'password'" in prompt


def test_turning_the_guard_off_removes_the_rail_and_its_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv(BENCHMARK_INTEGRITY_MODE_ENV, "off")
    parts = vm_agent.osworld_parts("Do it.", str(tmp_path), routing=False)
    assert not any(isinstance(r, BenchmarkIntegrityRail) for r in parts["rails"])
    assert BENCHMARK_INTEGRITY_PROMPT not in parts["system_prompt"]


def test_every_run_streams_its_message_history(tmp_path, monkeypatch):
    # trajectory.jsonl is how a run is read afterwards (the fork agent's runs used this format); a run that
    # hangs or is killed still leaves every finished step on disk.
    monkeypatch.setenv(BENCHMARK_INTEGRITY_MODE_ENV, "off")
    rail = vm_agent.osworld_parts("Do it.", str(tmp_path), routing=False)["rails"][0]
    assert isinstance(rail, TrajectoryStreamRail)
    assert rail._dest_path == str(tmp_path / "trajectory.jsonl")


def _call(rail, tool_name, args):
    inputs = ToolCallInputs(tool_name=tool_name, tool_args=json.dumps(args),
                            tool_call=SimpleNamespace(id="call-1"))
    ctx = SimpleNamespace(inputs=inputs, extra={})
    asyncio.run(rail.before_tool_call(ctx))
    return ctx, inputs


def test_a_vm_restart_through_computer_use_code_is_blocked(tmp_path):
    # os 4783cc41 rebooted the VM from a script and the run could not be graded; this agent's scripts run through
    # computer_use, so its code is checked like a bash command.
    rail = BenchmarkIntegrityRail("Do it.", str(tmp_path))
    ctx, inputs = _call(rail, "computer_use", {"code": "import subprocess\nsubprocess.run(['sudo', 'reboot'])"})
    assert ctx.extra.get("_skip_tool") is True
    assert "restart or stop the VM" in inputs.tool_msg.content


def test_ordinary_computer_use_code_runs(tmp_path):
    # The guard must not refuse normal work (a false refusal is a defect too).
    rail = BenchmarkIntegrityRail("Do it.", str(tmp_path))
    ctx, _ = _call(rail, "computer_use", {"code": "app = computer.app('gedit')\napp.type_text('hello')"})
    assert not ctx.extra.get("_skip_tool")


def test_computer_use_code_that_reads_the_benchmark_is_blocked(tmp_path):
    rail = BenchmarkIntegrityRail("Do it.", str(tmp_path))
    code = "import subprocess\nsubprocess.run(['git', 'clone', 'https://github.com/xlang-ai/OSWorld'])"
    ctx, _ = _call(rail, "computer_use", {"code": code})
    assert ctx.extra.get("_skip_tool") is True


# --- skills ---------------------------------------------------------------------------------------------------

SNAPSHOT = "OS: Ubuntu 22.04\nOpen windows:\nUntitled 1 - LibreOffice Calc\n~/Desktop:\nreport.xlsx\n"


def test_the_skills_of_the_apps_the_task_names_or_has_open_are_in_the_prompt(tmp_path, monkeypatch):
    # Without injection the fork agent read a skill in about 40% of runs, so its facts often never arrived.
    monkeypatch.setenv(BENCHMARK_INTEGRITY_MODE_ENV, "off")
    parts = vm_agent.osworld_parts("Turn my image into CMYK mode within GIMP.", str(tmp_path), routing=False,
                                   env_block=SNAPSHOT, log=lambda _: None)
    prompt = parts["system_prompt"]
    assert "### Skill: gimp" in prompt  # named by the task
    assert "### Skill: libreoffice-calc" in prompt  # open on screen
    assert "### Skill: chrome" not in prompt
    # The fork's order: the snapshot before the skills it selected them from.
    assert prompt.index("Open windows:") < prompt.index("### Skill: gimp")


def test_a_task_naming_no_app_gets_the_os_skill(tmp_path, monkeypatch):
    monkeypatch.setenv(BENCHMARK_INTEGRITY_MODE_ENV, "off")
    parts = vm_agent.osworld_parts("Set the time zone to UTC.", str(tmp_path), routing=False, log=lambda _: None)
    assert "### Skill: os" in parts["system_prompt"]


def test_the_skill_tools_are_there(tmp_path, monkeypatch):
    monkeypatch.setenv(BENCHMARK_INTEGRITY_MODE_ENV, "off")
    tools = vm_agent.osworld_parts("Do it.", str(tmp_path), routing=False, log=lambda _: None)["tools"]
    names = {t.card.name for t in tools}
    assert {"declare_infeasible", "inspect_file", "read_skill", "list_skill"} <= names
    # close_window needs the fork's cua screenshots; this agent closes windows through computer_use.
    assert "close_window" not in names


def test_inspect_file_runs_its_inspector_in_this_vm(monkeypatch):
    sent = {}

    class Response:
        status_code, content = 200, b"{}"

        @staticmethod
        def json():
            return {"status": "success", "output": "ok", "error": ""}

    def post(url, timeout, **kwargs):
        sent.update(url=url, code=kwargs["json"]["code"])
        return Response()

    import requests
    monkeypatch.setattr(requests, "post", post)
    assert vm_agent.GuestPython().run_python_script("print(1)")["output"] == "ok"
    assert sent == {"url": "http://localhost:5000/run_python", "code": "print(1)"}


def test_no_skill_names_a_tool_this_agent_lacks():
    # A skill that says "call get_window_state" or "run it with bash" sends the agent to a tool that does not exist.
    import pathlib
    import re
    gone = re.compile(r"get_window_state|list_windows|launch_app|bring_to_front|run_in_background|`bash`|via bash")
    skills = pathlib.Path(vm_agent.__file__).parent / "skills"
    offenders = []
    for p in skills.glob("*/SKILL.md"):
        for line in p.read_text(encoding="utf-8").splitlines():
            if gone.search(line):
                offenders.append(f"{p.parent.name}: {line.strip()}")
    assert offenders == []


# Moved from the fork agent's tests (test_jiuwen_round9_switches.py), against the same function.
@pytest.mark.parametrize("instruction, windows, expected", [
    ("Make the title bold.", "report.docx - LibreOffice Writer", ["libreoffice-writer"]),
    ("Use GIMP to flip the image.", "", ["gimp"]),
    ("Change the background of every slide.", "deck.pptx - LibreOffice Impress", ["libreoffice-impress"]),
    ("Export the open spreadsheet to the email draft.", "a.xlsx - LibreOffice Calc\nWrite: (no subject) - Thunderbird",
     ["libreoffice-calc", "thunderbird"]),
    ("Rename every file in ~/photos to lowercase.", "Terminal", ["os"]),
])
def test_skills_come_from_the_instruction_and_open_windows(instruction, windows, expected):
    assert vm_agent.task_skills(instruction, windows) == expected


def test_a_lowercase_common_word_does_not_pick_a_skill():
    # "writer" / "impress" as plain words are not the LibreOffice apps.
    assert vm_agent.task_skills("Impressive: the writer wants a summary in a text file.", "") == ["os"]


@pytest.mark.parametrize("command, blocked", [
    ("sudo reboot", True), ("echo pw | sudo -S shutdown -r now", True), ("systemctl poweroff", True),
    ("pkill -f python3", True), ("init 6", True),
    ("grep -r shutdown_timeout ~/.config", False), ("python3 -c 'print(1)'", False), ("ls /boot", False),
])
def test_commands_that_restart_the_vm_are_blocked(command, blocked):
    # os 4783cc41 (fin1023) rebooted the VM and the guest server never came back.
    from osworld.benchmark_integrity import vm_restart_violation
    assert vm_restart_violation(command) is blocked


# --- task rules and the two generic rails ---------------------------------------------------------------------

def test_the_task_rules_follow_the_sudo_note_in_the_fork_order(tmp_path, monkeypatch):
    # The fork agent's prompt: VM note, sudo note, TASK_RULES, V1_RULES, integrity, snapshot, skills.
    from osworld.osworld_rules import TASK_RULES, V1_RULES
    monkeypatch.delenv(BENCHMARK_INTEGRITY_MODE_ENV, raising=False)
    monkeypatch.setenv("OSWORLD_CLIENT_PASSWORD", "password")
    prompt = vm_agent.osworld_parts("Do it.", str(tmp_path), routing=False, env_block=SNAPSHOT,
                                    log=lambda _: None)["system_prompt"]
    parts = ("sudo -S", TASK_RULES, V1_RULES, BENCHMARK_INTEGRITY_PROMPT, "Open windows:")
    order = [prompt.index(part) for part in parts]
    assert order == sorted(order)


def test_no_rule_names_a_tool_this_agent_lacks():
    # "run those commands with bash" sent the fork agent to its bash tool; this agent has none.
    import re
    from osworld.osworld_rules import TASK_RULES, V1_RULES
    gone = re.compile(r"\b(bash|run_in_background|get_window_state|list_windows|launch_app|bring_to_front)\b")
    assert not gone.search(TASK_RULES) and not gone.search(V1_RULES)


def test_request_size_is_always_logged_and_the_fallback_only_when_there_is_one(tmp_path, monkeypatch):
    from osworld.rails.model_fallback_rail import ModelFallbackRail
    from osworld.rails.request_size_rail import RequestSizeRail
    monkeypatch.setenv(BENCHMARK_INTEGRITY_MODE_ENV, "off")
    without = vm_agent.osworld_parts("Do it.", str(tmp_path), routing=False, log=lambda _: None)["rails"]
    assert any(isinstance(r, RequestSizeRail) for r in without)
    assert not any(isinstance(r, ModelFallbackRail) for r in without)
    model = object()
    rails = vm_agent.osworld_parts("Do it.", str(tmp_path), routing=False, log=lambda _: None,
                                   fallback=("anthropic/claude-opus-4.8", model))["rails"]
    fallback = next(r for r in rails if isinstance(r, ModelFallbackRail))
    assert fallback._fallbacks == [model] and fallback._names == ["anthropic/claude-opus-4.8"]


def test_the_fallback_rail_uses_hooks_this_openjiuwen_has():
    # ModelFallbackRail retries through ctx.request_retry and swaps models through ctx.agent.set_llm; in a rail
    # callback ctx.agent is the ReActAgent the DeepAgent runs (ComputerUseAgent itself has no set_llm).
    from openjiuwen.core.single_agent import ReActAgent
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
    assert hasattr(AgentCallbackContext, "request_retry")
    assert hasattr(ReActAgent, "set_llm")


# --- the pre-task gate ------------------------------------------------------------------------------------------

def _gate(verdict="FEASIBLE", notes="### Skill basis\n- none\n### Probe evidence\n- ok\n### Feasibility\nok",
          skills=("gimp",)):
    from osworld.gate import GateResult
    return GateResult(verdict=verdict, skills=list(skills), notes=notes, raw=f"VERDICT: {verdict}\n{notes}")


def test_with_a_gate_the_agent_gets_its_skills_and_its_notes_every_step(tmp_path, monkeypatch):
    from osworld.rails.gate_notes_rail import GateNotesRail
    monkeypatch.setenv(BENCHMARK_INTEGRITY_MODE_ENV, "off")
    parts = vm_agent.osworld_parts("Use Chrome to do it.", str(tmp_path), routing=False, log=lambda _: None,
                                   env_block=SNAPSHOT, gate=_gate())
    # The gate's skills, not the ones injection would pick from the instruction and the screen.
    assert "### Skill: gimp" in parts["system_prompt"]
    assert "### Skill: chrome" not in parts["system_prompt"]
    assert "### Skill: libreoffice-calc" not in parts["system_prompt"]
    assert any(isinstance(r, GateNotesRail) for r in parts["rails"])


def test_the_gate_s_notes_follow_the_task_in_the_query():
    gate = _gate()
    query = gate.handoff("Do it.")
    assert query.startswith("Do it.") and "## Notes from a read-only pre-task check" in query


def test_the_gate_sees_the_screen_without_input(monkeypatch):
    # The gate must not change the VM: its screen tools only read.
    from osworld import gate
    calls = []

    class App:
        @staticmethod
        def read(view):
            calls.append(("read", view))
            return type("Obs", (), {"text": "Window 0: gedit", "screenshot": None})()

        @staticmethod
        def __getattr__(name):
            raise AssertionError(f"the gate called {name}")

    class Computer:
        @staticmethod
        def list_apps():
            calls.append(("list_apps",))
            return [type("A", (), {"id": 13, "display_name": "gedit", "is_running": True})(),
                    type("A", (), {"id": 20, "display_name": "Calendar", "is_running": False})()]

        @staticmethod
        def app(selector):
            calls.append(("app", selector))
            return App()

    list_apps, read_window = gate.make_screen_tools(Computer())
    assert asyncio.run(list_apps.invoke({})) == "13: gedit"
    out = asyncio.run(read_window.invoke({"app": "13"}))
    assert "gedit" in str(getattr(out, "data", out))
    assert calls == [("list_apps",), ("app", 13), ("read", "both")]


def test_read_window_returns_text_the_tracer_can_copy():
    # chrome 06fe7178 (2026-11-25): the SDK's CapturedObservationText survives slicing, openjiuwen's tracer
    # deepcopies every tool result, and read_window failed with "missing ... 'observation'".
    import copy

    from computer_use_engine.session.observation import CapturedObservationText
    from osworld import gate

    class App:
        @staticmethod
        def read(view):
            return type("Obs", (), {"text": CapturedObservationText("Window 0: gedit", object()),
                                    "screenshot": None})()

    class Computer:
        @staticmethod
        def app(selector):
            return App()

    _, read_window = gate.make_screen_tools(Computer())
    out = asyncio.run(read_window.invoke({"app": "gedit"}))
    assert copy.deepcopy(out.data)["content"] == "Window 0: gedit"


def test_the_gate_s_tools_are_all_on_its_read_only_list():
    from osworld import gate
    names = {t.card.name for t in gate.make_file_tools()} | {"list_apps", "read_window"}
    assert names <= gate._READ_ONLY
