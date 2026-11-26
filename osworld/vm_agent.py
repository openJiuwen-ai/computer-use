#!/usr/bin/env python3
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""This repo's computer_use agent with the OSWorld tools and rails, run inside the task's VM.

run_task_sdk.py uploads this package beside the agent environment and starts it with the environment's Python:

    python -m osworld.vm_agent --config <agent config.json> --instruction-file <file> --results-dir <dir>

The agent is ComputerUseAgent as computer-use-agent builds it (computer_use_agent.cli.config load/build), plus:
- declare_infeasible (osworld/tools/infeasible.py): writes infeasible.json in --results-dir, which the runner
  turns into OSWorld's FAIL action before grading;
- BenchmarkIntegrityRail (osworld/rails/benchmark_integrity_rail.py), unless
  OPENJIUWEN_BENCHMARK_INTEGRITY_MODE turns it off;
- TrajectoryStreamRail (osworld/rails/trajectory_rail.py): the message history in trajectory.jsonl after every
  model call and tool call, in the format the fork agent's runs recorded;
- the fork agent's pre-task gate (osworld/gate.py; --gate and --gate-binding, both on by default): a read-only
  pass before the agent that loads the task's skills, checks its resources and decides feasibility. A binding
  INFEASIBLE verdict with evidence writes infeasible.json and the agent does not run; otherwise the agent gets the
  skills the gate loaded, its notes in the query and again before every model call (GateNotesRail);
- the fork agent's skills: inspect_file and read_skill (osworld/tools/osworld_tools.py) and openjiuwen's
  list_skill, and, as its --inject-skills did, the full text of the skills for the apps the task names or has open
  in the system prompt, after the environment snapshot (osworld/env_snapshot.py) they are matched against;
- GuiStepCapRail (osworld/rails/gui_step_cap_rail.py): stops the run at --max-gui-steps GUI actions (default
  100, the leaderboard budget), counted from computer_use's action receipts, and tells the model its budget;
- AutosaveReminderRail (osworld/rails/autosave_rail.py): after --autosave-every GUI edits in one window without
  a save (default 6), reminds the model to save;
- RequestSizeRail (images and bytes per model request, in agent.log) and ModelFallbackRail (a failed model call is
  retried, then re-routed to OPENJIUWEN_REFUSAL_FALLBACK_MODEL), as the fork agent wired them;
- the fork agent's OSWorld prompt text, in its order: VM_NOTE, SUDO_NOTE, TASK_RULES and V1_RULES
  (osworld/osworld_rules.py), the integrity prompt, the environment snapshot, the skills and the GUI budget.
Every streamed chunk and toolkit event goes to events.jsonl in --results-dir. Exit codes: 0 done, 3 the agent
returned an error result (e.g. max iterations), 1 it could not run.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from uuid import uuid4

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openjiuwen.harness.tools import ListSkillTool

from computer_use_agent import create_agent
from computer_use_agent.cli.config import build, load
from computer_use_agent.prompt import system_prompt
from osworld.benchmark_integrity import BENCHMARK_INTEGRITY_PROMPT, benchmark_integrity_enabled
from osworld.env_snapshot import gather_env_snapshot, open_windows
from osworld.gate import (make_file_tools, make_file_type_tool, make_probe_bash_tool, make_screen_tools,
                          read_only_tools, run_gate)
from osworld.osworld_rules import TASK_RULES, V1_RULES
from osworld.rails.autosave_rail import AutosaveReminderRail
from osworld.rails.benchmark_integrity_rail import BenchmarkIntegrityRail
from osworld.rails.gate_notes_rail import GateNotesRail
from osworld.rails.gui_step_cap_rail import BUDGET_PROMPT, GuiStepCapRail
from osworld.rails.model_fallback_rail import ModelFallbackRail
from osworld.rails.request_size_rail import RequestSizeRail
from osworld.rails.trajectory_rail import TrajectoryStreamRail, dump_trajectory
from osworld.tools.execution import OSWorldExecutionEnvironment
from osworld.tools.infeasible import MARKER_NAME, DeclareInfeasibleTool, clear_infeasible_marker
from osworld.tools.osworld_tools import make_osworld_tools, skill_objects, skills_section

logger = logging.getLogger("osworld.vm_agent")  # named, not __name__: this runs as -m (__main__)
EXIT_AGENT_ERROR = 3
TRAJECTORY = "trajectory.jsonl"
EVENTS = "events.jsonl"
ENV_SNAPSHOT = "env_snapshot.txt"
EVAL_VERSION = "v1"  # OSWorld-Verified; skill_body appends a skill's v1.md when it has one
GUEST_SERVER = "http://localhost:5000"  # the VM's own OSWorld guest server, as the agent sees it from inside
SKILL_TOOLS = ("inspect_file", "read_skill")
# The fork agent's FALLBACK_MODEL: where a model call goes once the agent's own model keeps failing.
FALLBACK_MODEL = (os.environ.get("OPENJIUWEN_REFUSAL_FALLBACK_MODEL") or "anthropic/claude-opus-4.8").strip()

# The parts of the fork agent's PROMPT_ADDENDUM that name only tools this agent has, word for word
# (the fork's openjiuwen_agent.py, kwangyy/OSWorld verified-agent).
VM_NOTE = (
    "\n\nThis computer is an Ubuntu (GNOME, X11) virtual machine and you are its only user. "
    "inspect_file reads a saved pptx/xlsx/docx/image. "
    "When the task is in an application list_skill knows about, read_skill(name) first and follow it. "
    "Work until the task's end state is reached and saved, then stop and summarize."
)
# The fork agent's SKILL_SIGNS and task_skills (openjiuwen_agent.py), word for word.
SKILL_SIGNS = (
    ("chrome", r"Google Chrome|Chromium|\bChrome\b|(?i:\bbrowser\b)"),
    ("gimp", r"\bGIMP\b|GNU Image Manipulation"),
    ("libreoffice-calc", r"LibreOffice Calc|\bCalc\b|(?i:spreadsheet|\.xlsx\b|\.ods\b)"),
    ("libreoffice-impress", r"LibreOffice Impress|\bImpress\b|(?i:\bslides?\b|presentation|\.pptx\b|\.odp\b)"),
    ("libreoffice-writer", r"LibreOffice Writer|\bWriter\b|(?i:\.docx\b|\.odt\b)"),
    ("thunderbird", r"Thunderbird|(?i:\be-?mails?\b)"),
    ("vlc", r"\bVLC\b"),
    ("vscode", r"Visual Studio Code|(?i:\bVS ?Code\b)"),
)
# The fork agent's SUDO_NOTE (openjiuwen_agent.py), word for word.
SUDO_NOTE = (
    "\n\nThe VM user's password is '{password}'. Use it for sudo: printf '%s\\n' '{password}' | sudo -S <command>."
)


def task_skills(instruction: str, windows: str) -> list:
    """Skills for the apps the instruction names or that have a window open; the os skill when none does."""
    text = f"{instruction}\n{windows}"
    return [name for name, sign in SKILL_SIGNS if re.search(sign, text)] or ["os"]


class GuestPython:
    """The one call inspect_file makes on OSWorld's PythonController, against this VM's own guest server.

    POST /run_python, the response's JSON (desktop_env is not installed in the VM).
    """

    def __init__(self, server: str = GUEST_SERVER) -> None:
        self._server = server.rstrip("/")

    def run_python_script(self, script: str) -> dict:
        import requests

        try:
            resp = requests.post(f"{self._server}/run_python", json={"code": script}, timeout=90)
        except requests.RequestException as exc:
            return {"status": "error", "message": "Failed to execute command.", "output": "", "error": str(exc)}
        if resp.status_code == 200:
            return resp.json()
        return {"status": "error", "message": "Failed to execute command.", "output": None,
                "error": resp.json().get("error") if resp.content else resp.text}


def osworld_prompt(base: str, *, password: str, integrity: bool, env_block: str = "", skills_block: str = "",
                   max_gui_steps: int = 0) -> str:
    """The agent's own system prompt followed by the OSWorld text, the snapshot and the skills, in the fork's order."""
    return (base + VM_NOTE + (SUDO_NOTE.format(password=password) if password else "")
            + TASK_RULES + "\n\n" + V1_RULES
            + (BENCHMARK_INTEGRITY_PROMPT if integrity else "")
            + (f"\n\n{env_block}" if env_block else "") + skills_block
            + (BUDGET_PROMPT.format(limit=max_gui_steps) if max_gui_steps else ""))


def osworld_parts(instruction: str, results_dir: str, routing: bool, log=logger.info, *, env_block: str = "",
                  controller=None, fallback=None, max_gui_steps: int = 0, autosave_every: int = 0,
                  gate=None) -> dict:
    """The tools, rails and system prompt this entry point adds to ComputerUseAgent.

    rails[0] is the TrajectoryStreamRail; its .agent is set once the agent exists.
    """
    integrity = benchmark_integrity_enabled()
    password = os.environ.get("OSWORLD_CLIENT_PASSWORD", "").strip().strip('"')
    rails = [TrajectoryStreamRail(os.path.join(results_dir, TRAJECTORY)), RequestSizeRail(log)]
    if max_gui_steps:  # the leaderboard budget: GUI actions only
        rails.append(GuiStepCapRail(max_gui_steps, notice=True, log=log))
    if integrity:
        rails.append(BenchmarkIntegrityRail(instruction, results_dir, log))
    if autosave_every:
        rails.append(AutosaveReminderRail(autosave_every, log=log))
    if fallback is not None:  # (name, Model); None when the fallback is the agent's own model
        rails.append(ModelFallbackRail([fallback[1]], names=[fallback[0]], log=log))
    if gate is not None:
        skills_block = skills_section(gate.skills, EVAL_VERSION)
        if gate.notes:  # the gate's notes before every model call, not only in the query
            rails.append(GateNotesRail(gate.notes))
    else:  # without a gate result: the skills of the apps the task names or has open
        names = task_skills(instruction, open_windows(env_block))
        log(f"Injected skills: {names}")
        skills_block = skills_section(names, EVAL_VERSION, "the applications this task names or has open")
    skill_tools = [t for t in make_osworld_tools(controller or GuestPython(), eval_version=EVAL_VERSION)
                   if t.card.name in SKILL_TOOLS]
    return {"tools": [DeclareInfeasibleTool(results_dir), *skill_tools,
                      ListSkillTool(get_skills=skill_objects, language="en")],
            "rails": rails,
            "system_prompt": osworld_prompt(system_prompt(routing=routing), password=password, integrity=integrity,
                                            env_block=env_block, skills_block=skills_block,
                                            max_gui_steps=max_gui_steps)}


async def pre_task_gate(model, instruction: str, results: Path, env_block: str, *, binding: bool, controller=None):
    """The fork agent's gate, with this repo's SDK as its eyes; None when it fails (the agent then runs without)."""
    from computer_use import Computer

    integrity = benchmark_integrity_enabled()
    execution = OSWorldExecutionEnvironment(GUEST_SERVER)
    try:
        with Computer() as computer:
            skill_tools = [t for t in make_osworld_tools(controller or GuestPython(), eval_version=EVAL_VERSION)
                           if t.card.name == "inspect_file"]
            tools = read_only_tools(make_screen_tools(computer) + make_file_tools() + skill_tools)
            tools.append(make_file_type_tool(execution))
            if binding:
                tools.append(make_probe_bash_tool(execution))
            # The gate's probe_bash is a hidden shell with network access: the same guard as the agent's.
            rails = [BenchmarkIntegrityRail(instruction, str(results), logger.info)] if integrity else []
            return await run_gate(model, instruction, tools=tools, rails=rails, env_block=env_block,
                                  workspace=str(results / "workspace" / "gate"), results_dir=str(results),
                                  session_prefix="osworld", log=logger.info, binding=binding,
                                  eval_version=EVAL_VERSION)
    except Exception as exc:  # noqa: BLE001 - a failed gate falls back to the plain agent
        logger.warning(f"WARNING: gate failed, running without it: {exc!r}")
        return None


def _jsonable(chunk):
    return chunk.model_dump(mode="json") if hasattr(chunk, "model_dump") else str(chunk)


async def run(args: argparse.Namespace) -> int:
    instruction = Path(args.instruction_file).read_text(encoding="utf-8")
    results = Path(args.results_dir)
    results.mkdir(parents=True, exist_ok=True)
    clear_infeasible_marker(str(results))
    settings = load(Path(args.config), overrides={"max_iterations": args.max_iterations or None})
    model, config = build(settings, workspace=results / "workspace")
    env_block, env_error = await gather_env_snapshot(OSWorldExecutionEnvironment(GUEST_SERVER).run_command)
    if env_block:
        (results / ENV_SNAPSHOT).write_text(env_block, encoding="utf-8")
    else:
        logger.warning(f"WARNING: no environment snapshot, running without it ({env_error})")
    fallback = None
    if FALLBACK_MODEL and FALLBACK_MODEL != settings["model"]:
        fallback_model, _ = build({**settings, "model": FALLBACK_MODEL}, workspace=results / "workspace")
        fallback = (FALLBACK_MODEL, fallback_model)
    gate = None
    if args.gate:
        gate_model, _ = build(settings, workspace=results / "workspace" / "gate")
        gate = await pre_task_gate(gate_model, instruction, results, env_block, binding=args.gate_binding)
        if gate and args.gate_binding and gate.binding_infeasible:
            # The runner records FAIL from the marker, as for declare_infeasible.
            (results / MARKER_NAME).write_text(json.dumps({"reason": gate.infeasible_reason, "by": "gate"}),
                                               encoding="utf-8")
            logger.info("Binding gate: INFEASIBLE with evidence; the agent does not run")
            return 0
    parts = osworld_parts(instruction, str(results), routing=config.routing.enabled, env_block=env_block,
                          fallback=fallback, max_gui_steps=args.max_gui_steps,
                          autosave_every=args.autosave_every, gate=gate)
    query = gate.handoff(instruction) if gate else instruction
    trajectory_rail = parts["rails"][0]
    events = (results / EVENTS).open("w", encoding="utf-8")

    def record(kind, data):
        events.write(json.dumps({"event": kind, "data": data}, ensure_ascii=False, default=str) + "\n")
        events.flush()

    code, conversation_id, agent = 0, uuid4().hex, None
    try:
        async with create_agent(model, config=config, on_event=record,
                                completion_timeout=settings.get("model_timeout", 600), **parts) as agent:
            trajectory_rail.agent = agent
            async for chunk in agent.stream({"query": query, "conversation_id": conversation_id}):
                record("chunk", _jsonable(chunk))
                kind, payload = getattr(chunk, "type", ""), getattr(chunk, "payload", None)
                if kind == "answer" and isinstance(payload, dict):
                    if payload.get("result_type") == "error":
                        code = EXIT_AGENT_ERROR
                    logger.info(f"Agent answer ({payload.get('result_type')}): "
                                f"{payload.get('output') or payload.get('content') or ''}")
            dump_trajectory(agent, str(results / TRAJECTORY), conversation_id)
    finally:
        events.close()
    return code


def main() -> None:
    # agent.log is this process's stdout; the runner copies it back. This package's loggers (the agent, the gate,
    # the trajectory rail): the libraries' own logging is left as it was.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    package_logger = logging.getLogger("osworld")
    package_logger.addHandler(handler)
    package_logger.setLevel(logging.INFO)
    package_logger.propagate = False
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--instruction-file", required=True)
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--max-iterations", type=int, default=0)
    parser.add_argument("--max-gui-steps", type=int, default=100,
                        help="Stop the run after this many GUI actions (leaderboard step budget); 0 = no cap.")
    parser.add_argument("--autosave-every", type=int, default=6,
                        help="Remind the model to save after this many GUI edits in one window; 0 = off.")
    parser.add_argument("--gate", action=argparse.BooleanOptionalAction, default=True,
                        help="Run the read-only pre-task check (skills, feasibility) before the agent.")
    parser.add_argument("--gate-binding", action=argparse.BooleanOptionalAction, default=True,
                        help="With --gate: probe_bash, 20 calls, and an INFEASIBLE verdict with evidence ends the "
                             "task (FAIL) without running the agent.")
    sys.exit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
