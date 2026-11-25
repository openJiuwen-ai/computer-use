# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The OSWorld 1.0 prompt block must route impossible-in-this-app tasks to declare_infeasible.

1.0's infeasible tasks (e.g. "trim the video within GIMP") score 1 only when the
agent declares them. Once the v1 rules allowed "a script on the file", the agent
trimmed that video with ffmpeg and reported success instead (v1fix2,
2026-10-04: 1 -> 0). The rules live in osworld/osworld_rules.py; the sudo note is
part of osworld/vm_agent.py's prompt.
"""

from __future__ import annotations

import pytest

from osworld.osworld_rules import TASK_RULES, V1_RULES


def test_v1_block_names_declare_infeasible_for_what_the_app_cannot_do():
    block = V1_RULES

    assert "declare_infeasible" in block
    # The permission to use scripts must not read as permission to swap in another program,
    # nor in the app's own scripting: gimp 2e6f678f ran headless Script-Fu as "GIMP itself"
    # while the permission came first (envsnap1/2, 2026-10-14).
    assert "write a script or macro that imitates it" in block
    assert block.index("An end state, or a capability") < block.index("whichever way is reliable")


def test_v1_block_still_requires_the_app_left_open_on_the_result():
    assert "leave it open" in V1_RULES


def test_agent_is_told_the_vm_password_for_sudo():
    # multi_apps e1fc0df3 needs `sudo apt install openjdk`; without the password the agent
    # guessed six, then hand-installed a JRE the grader cannot see.
    vm_agent = pytest.importorskip("osworld.vm_agent")
    prompt = vm_agent.osworld_prompt("", password="osworld-public-evaluation", integrity=False)

    assert "osworld-public-evaluation" in prompt
    assert "sudo -S" in prompt


def test_no_password_line_when_none_is_configured():
    vm_agent = pytest.importorskip("osworld.vm_agent")

    assert "sudo" not in vm_agent.osworld_prompt("", password="", integrity=False)


def test_v1_block_lets_the_apps_own_command_line_count_without_adding_features():
    # multi_apps 2fe4b718 and vlc 8f080098 (open1022) did VLC conversions through the dialogs and hit
    # the 100-action cap; the app's own CLI is the same feature at no GUI cost, but must not become
    # a way round rule 2 (gimp 2e6f678f's batch).
    assert "cvlc" in V1_RULES and "never adds an operation the menus lack" in V1_RULES


def test_task_rules_cover_scope_output_folder_and_blocked_routes():
    assert "even when its header suggests a value" in TASK_RULES  # calc 035f41ba: filled unnamed columns
    assert "Do not create a new folder for it" in TASK_RULES  # multi_apps 185f29bd: PDFs in a new subfolder
    assert "do not switch to another site, program or source" in TASK_RULES  # chrome 82279c77: moved to CarGurus
