# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

# coding: utf-8
"""The [Environment Snapshot] block: what the guest has, taken once at task start.

Mirrors jiuwen-terminal-bench harbor_agents/openjiuwen_agent.py _gather_env_snapshot:
one compound guest command split on @@SECTION@@ markers, appended to the system
prompt so it holds for every round, also after context compression. OSWorld
adds the app versions: 6/14 infeasible misses on 10-13 got around a missing
feature with another tool (gimp -b, curl, a hand-made tasks.json), so the
agent is told what this machine has. The task config's related_apps is not
used: it is annotation, not something an agent is given.
"""

from __future__ import annotations

import asyncio
import re
from typing import Awaitable, Callable

# One line per program. command -v first: with 2>&1, bash's own "command not found"
# message passed for the version line.
_VERSIONS = (
    ("google-chrome --version", "google-chrome"),
    ("libreoffice --version", "libreoffice"),
    ("gimp --version", "gimp"),
    ("echo \"VS Code $(code --version | head -1)\"", "code"),  # prints a bare "1.85.1"
    ("thunderbird --version", "thunderbird"),
    ("vlc --version 2>/dev/null | head -1", "vlc"),
    ("python3 --version", "python3"),
)
SNAPSHOT_CMD = " ; ".join(
    ["echo '@@OS@@'", "(lsb_release -ds 2>/dev/null || head -1 /etc/os-release)", "echo '@@APPS@@'"]
    + [f"(command -v {name} >/dev/null && {{ {cmd} 2>/dev/null | head -1 | grep . || echo '{name}: installed, "
       f"version unknown'; }} || echo '{name}: not found')" for cmd, name in _VERSIONS]
    + ["echo '@@EXTENSIONS@@'", "(code --list-extensions 2>/dev/null | head -30)",
       "echo '@@WINDOWS@@'",
       "(DISPLAY=:0 xdotool search --onlyvisible --name . getwindowname %@ 2>/dev/null "
       "| grep -v -E '^(gnome-shell|mutter guard window|@!.*)$' | sort -u | head -20)",
       "echo '@@DESKTOP@@'", "(ls -A ~/Desktop 2>/dev/null | head -30)",
       "echo '@@HOME@@'", "(ls -A ~ 2>/dev/null | grep -v '^\\.' | head -30)",
       # Relative dates in tasks ("next Monday") are graded from the VM's clock.
       "echo '@@DATE@@'", "date '+%A, %B %d, %Y'"]
)

_LABELS = (
    ("OS", "OS"),
    ("APPS", "Installed application versions"),
    ("EXTENSIONS", "VS Code extensions"),
    ("WINDOWS", "Open windows"),
    ("DESKTOP", "~/Desktop"),
    ("HOME", "~ (non-hidden)"),
    ("DATE", "Today (the VM's clock)"),
)


def parse_sections(stdout: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    key, lines = None, []
    for line in stdout.splitlines():
        if line.startswith("@@") and line.endswith("@@"):
            if key:
                sections[key] = "\n".join(lines).strip()
            key, lines = line.strip("@"), []
        else:
            lines.append(line)
    if key:
        sections[key] = "\n".join(lines).strip()
    return sections


def format_snapshot(sections: dict[str, str]) -> str:
    parts = []
    for key, label in _LABELS:
        body = sections.get(key, "")
        parts.append(f"{label}:\n{body}" if body else f"{label}: (none)")
    return "[Environment Snapshot] (taken at task start; the machine you work on)\n" + "\n".join(parts)


def open_windows(block: str) -> str:
    """The open-window titles of a snapshot block, "" when there are none."""
    match = re.search(r"^Open windows:\n(.*?)\n~/Desktop", block, re.S | re.M)
    return match.group(1) if match else ""


async def gather_env_snapshot(
    run_command: Callable[..., Awaitable], timeout_sec: int = 30
) -> tuple[str, str]:
    """Return (block, error). block is "" when the guest gave nothing; error then says why."""
    try:
        result = await asyncio.wait_for(run_command(SNAPSHOT_CMD, timeout_sec=timeout_sec), timeout_sec + 10)
    except Exception as exc:  # noqa: BLE001 - the caller logs it and runs without the block
        return "", f"{type(exc).__name__}: {exc}"
    sections = parse_sections(result.stdout or "")
    if not sections:
        return "", f"no output (rc={result.return_code}): {(result.stderr or '')[:200]}"
    return format_snapshot(sections), ""
