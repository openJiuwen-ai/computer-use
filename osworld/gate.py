# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read-only pre-task gate (``--gate``): the orchestration step the cua agent lacks.

In jiuwenswarm the main agent reads skills and plans before it hands desktop
work to the cua subagent; on OSWorld the cua agent gets the raw instruction.
This puts a thin version of that step back, after Intelligence-Indeed's
SkillGate + Planner (OSWorld-Verified #1, 2026-07-25): one look-only pass that

  * must load the skills the task needs with select_skills (the index is in
    its prompt; when none of its names is a skill, every skill is loaded),
  * checks the named resources and decides feasibility,
  * writes three-section notes: Skill basis (quoted skill rules), Probe
    evidence, Feasibility. Milestones were dropped: impress 986fc832
    (gate1021f) followed one into a 100-step palette search,

and hands the cua agent its notes in the query and again before every model
call (GateNotesRail, as Intelligence-Indeed's executor gets them every turn),
plus the chosen skills' full text in the system prompt. It has no GUI input
and no file writes, so it cannot change the VM.

A verdict of INFEASIBLE is advice, not a stop: the gate can only look, and in
gate1020 (2026-10-20) it called two feasible tasks infeasible from what it saw
(gimp dbbf4b99: a JPEG named .raw, judged by GIMP's error dialog; vs_code
7aeae0e2: "visualize" read as plotting). The cua agent, which can try the
application, makes the call with declare_infeasible.

``--gate-binding`` is Intelligence-Indeed's own form, for an A/B against the
advisory one: the gate also gets probe_bash (a hidden shell, for looking), a
20-call budget, and its INFEASIBLE verdict ends the task when it carries probe
evidence and a reason -- the cua agent does not run. No verdict means FEASIBLE.

On this repo's agent (osworld/vm_agent.py runs it inside the VM, before the agent): the gate is a plain
openjiuwen DeepAgent. It sees the screen through this repo's SDK, read-only -- list_apps and read_window (a
window's accessibility tree and screenshot) take the place of the cua agent's list_windows, get_window_state and
zoom -- and reads the VM's disk with openjiuwen's read_file, glob, grep and list_files on a local operation.
file_type and probe_bash run through the VM's own guest server. Everything else is the fork's gate.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional
from uuid import uuid4

from openjiuwen.core.foundation.tool.mcp.base import McpToolResult
from openjiuwen.core.foundation.tool.tool import tool
from openjiuwen.harness.factory import create_deep_agent

from osworld.osworld_rules import V1_RULES
from osworld.rails.trajectory_rail import dump_trajectory
from osworld.tools.osworld_tools import _image_item, _render_content, list_skills, skill_body, skills_section

# Intelligence-Indeed's gate_max_steps.
BINDING_GATE_MAX_ITERATIONS = 20
# 25 ran out on multi_apps 2c1ebcd7 (gate1020: it was checking references and never answered).
GATE_MAX_ITERATIONS = 40
# 40 ran out too on chrome 480bcfea (holdgate1022: 29 greps of the Chrome binary for a flag name), so a gate
# that ends without a verdict line is asked once more, in the same session, to answer from what it found.
GATE_FINAL_ASK = ("Stop checking: do not call any more tools. Write your answer now, in exactly the format "
                  "above, from what you have found. If the blocker is not confirmed, answer FEASIBLE.")
# The only non-cua tools the gate gets. An allowlist, not a blocklist: the sysop set
# also has `code` (arbitrary Python on the guest), which a name filter let through.
_READ_ONLY = frozenset({"list_apps", "read_window", "read_file", "glob", "grep", "list_files", "inspect_file",
                        "file_type", "probe_bash",
                        "select_skills"})

BINDING_NOTE = """
## Your verdict is final
INFEASIBLE ends the task: nobody tries it after you, and on a task that can be done it scores 0. Answer
INFEASIBLE only when you confirmed the blocker -- the version's menus, settings, `--help` or file formats lack
the feature; the file, device or account does not exist -- and say how under Probe evidence. probe_bash runs a
shell command out of sight, for looking only: versions, `--help`, ls, cat, grep in config files, `gsettings get`,
`python3 -c` that reads. Never change, create, install, start or stop anything with it.
"""

GATE_PROMPT = """\
You are the pre-task check for a desktop task on an Ubuntu (GNOME, X11) virtual machine. You do NOT do the
task and you must not change anything: you only look. Another agent does the task after you, guided by
your notes, so what you find and quote decides how it works.

Your tools only look: list_apps and read_window (the screen: a window's accessibility tree and screenshot);
read_file, glob, grep and list_files (files on disk); file_type (what a file really is, from its bytes); inspect_file \
(a saved
pptx/xlsx/docx/image); select_skills (loads skills' full guidance).

Work in this order:
1. Call select_skills first, with the names from the Skill Index below of every application the task involves
   (several for a multi-app task); their full text comes back. If none fits, all skills are loaded. Call it again
   only if checking shows an application you missed.
2. Read the request the way the person who wrote it most plausibly meant it, in everyday terms. If it can be
   read more than one way, write the readings down and judge the most ordinary one. Problems in the task's
   own inputs -- a bug in a file, an odd format, a misleading file name -- are usually part of the job.
3. Check the resources: every file, folder, window, page, account or setting the task names exists where it
   says. Look at the open windows and on disk; note exact paths and window titles. Use file_type on any
   input file: trust what its bytes say over its name and over an application's error message. With probe_bash,
   check each name the task depends on with one command: a shell variable or placeholder (`echo "$name"`), a
   user (`getent passwd NAME`), a file (`ls`), a device (`ls /sys/class/<subsystem>`). A reference that resolves to \
nothing makes
   the task infeasible; do not guess which similar thing was meant.
4. Read the task's constraints ("only", "without", "using X", "in <app>", "do not ...") and decide
   feasibility by the rules of this environment below (rules 2 and 3).
5. You have at most {budget} tool calls in all; checking is for the verdict, not for doing the task's research.
   Answer in exactly this format, then stop:

VERDICT: FEASIBLE or INFEASIBLE
SKILLS: <comma-separated names of the skills you read, or none>
### Skill basis
- [skill-name] "an exact quote of a rule from that skill the doer must follow for this task"
(the 3-6 most relevant quotes: saving/exporting, what must stay open, what not to do; "- none" if no skill applies.
Each quote is passed on as the whole paragraph it comes from, with its section heading. Quote only rules whose
condition -- their "when" or "if", or their section heading -- holds for this task)
### Probe evidence
- what you checked and what you saw (paths, window titles, file types, values, versions); what you could
  not confirm
### Feasibility
<one or two sentences: the verdict and why. For INFEASIBLE: the missing feature, file, hardware or account,
and the evidence for it. State facts and the verdict; do not prescribe click sequences.>

INFEASIBLE needs positive evidence; if you could not confirm the blocker by looking, answer FEASIBLE and say
under Probe evidence what is uncertain. A string you cannot find by grepping a program binary or a packed
resource file is not evidence either way: judge from what the user would see (menus, settings pages, the
version) and from the skills.
"""


@dataclass
class GateResult:
    verdict: Optional[str]  # "FEASIBLE", "INFEASIBLE", or None when no verdict line was found
    skills: List[str] = field(default_factory=list)
    notes: str = ""
    raw: str = ""

    @property
    def infeasible(self) -> bool:
        return self.verdict == "INFEASIBLE"

    @property
    def infeasible_reason(self) -> str:
        # "### Infeasible reason" is the format before the three-section notes.
        match = re.search(r"### (?:Feasibility|Infeasible reason)\s*\n(.+?)(?=\n### |\Z)", self.raw, re.S)
        return match.group(1).strip() if match else ""

    @property
    def binding_infeasible(self) -> bool:
        """INFEASIBLE with probe evidence and a stated reason: what --gate-binding stops on."""
        return self.infeasible and "### Probe evidence" in self.raw and bool(self.infeasible_reason)

    def handoff(self, instruction: str) -> str:
        """The cua agent's query: the task, then the gate's notes."""
        if not self.notes:
            return instruction
        if self.infeasible:
            lead = ("A read-only pre-task check judged this task INFEASIBLE (reason at the end of its notes). It "
                    "could only look, not try, so verify it: try the named application's own way once (open the "
                    "file, look in its menus and dialogs). If the blocker holds, call declare_infeasible; if the "
                    "application can do it after all, do the task, following the notes.")
        else:
            lead = ("The rules quoted under Skill basis are binding where their conditions hold. "
                    "Probe evidence is what the check saw before you "
                    "started; Feasibility is its verdict.")
        return f"{instruction}\n\n## Notes from a read-only pre-task check\n{lead}\n\n{self.notes}"


def skill_index() -> str:
    lines = [f"- {s['name']}: {s['description']}" for s in list_skills()]
    return "\n\n## Skill Index\n" + ("\n".join(lines) if lines else "(no skills)")


# A gate quote only locates a rule; the doer gets the whole paragraph or list item it comes from, under its
# section headings. Quoting half a rule dropped its condition: multi2fix_1030 09a37c51 quoted "do not edit the
# image files with other programs or scripts" without "When the task names GIMP, or the image is open in GIMP",
# and the agent, told quotes are binding, traced a cut-out by hand for 100 steps in a task that named no app.
_HEADING = re.compile(r"(#{1,6})\s+(.*)")
_LIST_ITEM = re.compile(r"(?:[-*]|\d+\.)\s")
_QUOTE_LINE = re.compile(r'-\s*\[([^\]]+)\]\s*"(.*)"')
_MIN_FRAGMENT = 15


def _norm(text: str) -> str:
    return " ".join(re.sub(r'[*`\\]', "", text).replace("’", "'").split()).lower()


def skill_blocks(body: str) -> List[tuple]:
    """(section heading path, text) for each paragraph and top-level list item of a skill body."""
    blocks, path, current = [], [], []

    def close():
        if current:
            blocks.append((" > ".join(h for _, h in path), " ".join(" ".join(current).split())))
            current.clear()

    for line in body.splitlines():
        heading = _HEADING.match(line)
        if heading or not line.strip() or _LIST_ITEM.match(line):
            close()
        if heading:
            level = len(heading.group(1))
            path[:] = [(lv, h) for lv, h in path if lv < level] + [(level, heading.group(2).strip())]
        elif line.strip():
            current.append(line.strip())
    close()
    return blocks


def expand_skill_basis(notes: str, eval_version: str) -> tuple:
    """Replace each Skill basis quote with the skill blocks it was taken from: (notes, quotes that matched none)."""
    section = re.search(r"### Skill basis[^\n]*\n(.*?)(?=\n### |\Z)", notes, re.S)
    if not section:
        return notes, []
    by_name = {s["name"]: s for s in list_skills()}
    lines, unmatched = [], []
    for raw in section.group(1).splitlines():
        quote = _QUOTE_LINE.match(raw.strip())
        if not quote:
            continue
        name = quote.group(1).strip()
        skill = by_name.get(name)
        fragments = [f for f in map(_norm, re.split(r"\.\.\.|…", quote.group(2))) if len(f) >= _MIN_FRAGMENT]
        blocks = skill_blocks(skill_body(os.path.join(skill["directory"], "SKILL.md"), eval_version)) if skill else []
        hits = [(heading, block) for heading, block in blocks if any(f in _norm(block) for f in fragments)]
        if not hits:
            unmatched.append(raw.strip())
        for heading, block in hits:
            line = f'- [{name}] ({heading}) "{block}"' if heading else f'- [{name}] "{block}"'
            if line not in lines:
                lines.append(line)
    start, end = section.span(1)
    return notes[:start] + ("\n".join(lines) or "- none") + notes[end:], unmatched


def parse_gate_output(text: str) -> GateResult:
    verdict = re.search(r"VERDICT:\s*\**\s*(FEASIBLE|INFEASIBLE)", text, re.I)
    skills_line = re.search(r"SKILLS:\s*(.+)", text)
    known = {s["name"] for s in list_skills()}
    skills = [s.strip().strip("`*") for s in skills_line.group(1).split(",")] if skills_line else []
    first_section = text.find("### ")
    return GateResult(verdict=verdict.group(1).upper() if verdict else None,
                      skills=[s for s in skills if s in known],
                      notes=text[first_section:].strip() if first_section != -1 else "",
                      raw=text)


def make_file_type_tool(execution_env):
    """`file -b` plus the first 16 bytes, run on the guest without a terminal window."""

    @tool(name="file_type",
          description="Identify what a file on the VM really is from its contents (not its name): returns the "
                      "`file` command's description and the first 16 bytes in hex.")
    async def file_type(path: str) -> str:
        # The guest's shell expands ~ to the guest's home; os.path.expanduser here gave the host's
        # (/root on a Linux host, C:\Users\... on Windows).
        quoted = '"$HOME"/' + shlex.quote(path[2:]) if path.startswith("~/") else shlex.quote(path)
        result = await execution_env.run_command(
            f"file -b -- {quoted}; head -c 16 -- {quoted} | od -An -tx1", timeout_sec=20)
        return (result.stdout or result.stderr or "(no output)").strip()

    return file_type


def make_probe_bash_tool(execution_env):
    """A shell on the guest that no window shows, for the binding gate's probes.

    bash types into the visible terminal, whose history some tasks grade.
    """

    @tool(name="probe_bash",
          description="Run a shell command on the VM out of sight and return its output, for looking only "
                      "(versions, --help, ls, cat, grep, gsettings get). It must not change anything.")
    async def probe_bash(command: str) -> str:
        result = await execution_env.run_command(command, timeout_sec=30)
        return ((result.stdout or "") + (result.stderr or "")).strip()[:4000] or "(no output)"

    return probe_bash


def make_screen_tools(computer: Any) -> list:
    """list_apps and read_window: the screen through this repo's SDK, for looking only.

    No SDK input method is reachable from them.
    """

    @tool(name="list_apps",
          description="List the running applications with their ids, to pass to read_window. It only looks.")
    async def list_apps() -> str:
        apps = await asyncio.to_thread(computer.list_apps)
        running = [f"{a.id}: {a.display_name}" for a in apps if a.is_running]
        return "\n".join(running) or "(no running applications)"

    @tool(name="read_window",
          description="Read an application's window: its accessibility tree as text and a screenshot. app is an id "
                      "or a name from list_apps. It only looks.")
    async def read_window(app: str) -> McpToolResult:
        selector = int(app) if str(app).strip().isdigit() else str(app)
        obs = await asyncio.to_thread(lambda: computer.app(selector).read(view="both"))
        images = [_image_item(bytes(obs.screenshot), f"read_window({app})")] if obs.screenshot else []
        # A plain str: the SDK's text keeps its capture through slicing, and openjiuwen's tracer cannot deepcopy it.
        return McpToolResult(data={"content": str(obs.text)[:20000], "multimodal": images})

    read_window.render_for_llm = _render_content
    return [list_apps, read_window]


def make_file_tools() -> list:
    """openjiuwen's read_file, glob, grep and list_files on a local operation: the VM's own disk."""
    from openjiuwen.core.sys_operation import OperationMode, SysOperation, SysOperationCard
    from openjiuwen.harness.tools import GlobTool, ListDirTool, ReadFileTool
    from openjiuwen.harness.tools.filesystem import GrepTool

    operation = SysOperation(SysOperationCard(id="osworld_gate_local", mode=OperationMode.LOCAL))
    return [ReadFileTool(operation, language="en"), GlobTool(operation, language="en"),
            GrepTool(operation, language="en"), ListDirTool(operation, language="en")]


def make_select_skills_tool(selected: List[str], eval_version: str):
    """select_skills: the gate's way to load skills, as Intelligence-Indeed's gate does.

    Names that are not skills are ignored; when none of the first call's names is a skill, every skill is loaded.
    """

    @tool(name="select_skills",
          description="Load the full guidance of the named skills (names from the Skill Index), e.g. "
                      "\"libreoffice-calc, chrome\". Returns their text. Call it first.")
    async def select_skills(names: str) -> str:
        known = [s["name"] for s in list_skills()]
        asked = [n.strip().strip("`*'\"") for n in re.split(r"[,\s]+", names or "") if n.strip()]
        new = [n for n in asked if n in known and n not in selected]
        note = ""
        if not selected and not new:
            new, note = list(known), "None of those names is a skill, so every skill is loaded.\n\n"
        selected.extend(new)
        return note + (skills_section(new, eval_version, "you") if new else "Already loaded: " + ", ".join(selected))

    return select_skills


def read_only_tools(tools: list) -> list:
    return [t for t in tools if t.card.name in _READ_ONLY]


def _answer(chunk) -> Optional[str]:
    payload = getattr(chunk, "payload", None)
    if getattr(chunk, "type", "") == "answer" and isinstance(payload, dict):
        output = payload.get("output") or payload.get("content") or ""
        return output if isinstance(output, str) else str(output)
    return None


async def _run_once(agent, query: str, session_id: str) -> GateResult:
    text = ""
    async for chunk in agent.stream({"query": query, "conversation_id": session_id}):
        answer = _answer(chunk)
        if answer is not None:
            text = answer
    return parse_gate_output(text)


async def run_gate(model, instruction: str, *, tools: list, rails: list, env_block: str, workspace: str,
                   results_dir: str, session_prefix: str, log: Callable[[str], None],
                   binding: bool = False, eval_version: str = "v1") -> GateResult:
    """Run the gate once and record its output under results_dir."""
    selected: List[str] = []
    tools = [t for t in tools if t.card.name != "select_skills"] + [make_select_skills_tool(selected, eval_version)]
    log(f"Gate tools: {sorted(t.card.name for t in tools)}")
    budget = BINDING_GATE_MAX_ITERATIONS if binding else GATE_MAX_ITERATIONS
    agent = create_deep_agent(
        model,
        system_prompt=GATE_PROMPT.format(budget=budget - 5) + (BINDING_NOTE if binding else "") + "\n" + V1_RULES
        + skill_index() + (f"\n\n{env_block}" if env_block else ""),
        tools=tools,
        rails=rails,
        language="en",
        max_iterations=budget,
        workspace=workspace,
        parallel_tool_calls=False,
        enable_read_image_multimodal=True,
    )
    session_id = f"{session_prefix}-gate-{uuid4().hex[:8]}"
    try:
        gate = await _run_once(agent, f"TASK: {instruction}", session_id)
        if gate.verdict is None:
            log("Gate ended without a verdict; asking it to answer from what it found")
            gate = await _run_once(agent, f"TASK: {instruction}\n\n{GATE_FINAL_ASK}", session_id)
    finally:
        dump_trajectory(agent, os.path.join(results_dir, "gate_trajectory.jsonl"), session_id)
    # The skills the gate loaded; else the SKILLS line; else every skill, as Intelligence-Indeed falls back.
    gate.skills = selected or gate.skills or [s["name"] for s in list_skills()]
    gate.notes, unmatched = expand_skill_basis(gate.notes, eval_version)
    if unmatched:
        log(f"Gate quotes found in no skill, dropped: {unmatched}")
    with open(os.path.join(results_dir, "gate.json"), "w", encoding="utf-8") as f:
        json.dump({"verdict": gate.verdict, "skills": gate.skills, "binding": binding, "output": gate.raw,
                   "notes": gate.notes, "unmatched_quotes": unmatched}, f, indent=2)
    log(f"Gate verdict={gate.verdict} skills={gate.skills}")
    return gate
