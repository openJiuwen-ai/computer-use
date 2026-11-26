# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""run_parallel.py launching OSWorld-Verified tasks through run_task_sdk.py.

Verified task ids are uuids filed under a domain directory (evaluation_examples/examples/<domain>/<uuid>.json), so
every run needs --domain or it loads nothing.
"""

from __future__ import annotations

import argparse

import pytest

from osworld.run_parallel import base_name, split_spec, task_cmd

UUID = "06fe7178-4491-4589-810f-2e2bc9502122"


def _args(**kw):
    return argparse.Namespace(**{"max_iterations": 0, "time_budget": None, "max_gui_steps": None, **kw})


def test_a_task_passes_its_domain_id_and_budget_to_the_runner():
    cmd = task_cmd("python", "run_task_sdk.py", f"chrome/{UUID}", "chrome_06fe7178_x", _args(time_budget=1200))

    assert cmd[cmd.index("--task-id") + 1] == UUID
    assert cmd[cmd.index("--domain") + 1] == "chrome"
    assert cmd[cmd.index("--run-name") + 1] == "chrome_06fe7178_x"
    assert cmd[cmd.index("--time-budget") + 1] == "1200"


def test_without_a_budget_the_runner_keeps_its_default():
    assert "--time-budget" not in task_cmd("python", "run_task_sdk.py", f"chrome/{UUID}", "x", _args())


def test_an_id_without_a_domain_is_refused():
    # It would load no task file; refusing it here beats a crashed run in the sweep.
    with pytest.raises(ValueError):
        task_cmd("python", "run_task_sdk.py", "014", "014", _args())


def test_results_dir_is_domain_plus_short_uuid():
    # A full uuid is unreadable in listings.
    assert base_name(f"libreoffice_calc/{UUID}") == "libreoffice_calc_06fe7178"
    assert split_spec("014") == (None, "014")


def test_runs_use_the_given_interpreter():
    # Verified tasks must run under the upstream checkout's venv: its desktop_env and
    # graders are what make the score comparable to the OSWorld-Verified leaderboard.
    cmd = task_cmd("C:/OSWorld/.venv/Scripts/python.exe", "run_task_sdk.py", f"chrome/{UUID}",
                   "chrome_06fe7178_ver", _args())

    assert cmd[0] == "C:/OSWorld/.venv/Scripts/python.exe"
    assert cmd[1] == "run_task_sdk.py"


def test_default_model_reads_dotenv(tmp_path, monkeypatch):
    # .env is the documented place to pick the model; run_parallel passes MODEL_NAME down to
    # every run, so ignoring .env here silently ran the 10-04/10-05 sweeps on the wrong model.
    from osworld.run_parallel import default_model

    monkeypatch.delenv("MODEL_NAME", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('MODEL_NAME = "anthropic/claude-opus-5"\n', encoding="utf-8")

    assert default_model(str(env_file)) == "anthropic/claude-opus-5"


def test_default_model_prefers_the_environment_then_falls_back(tmp_path, monkeypatch):
    from osworld.run_parallel import default_model

    env_file = tmp_path / ".env"
    env_file.write_text("MODEL_NAME=anthropic/claude-opus-5\n", encoding="utf-8")
    monkeypatch.setenv("MODEL_NAME", "anthropic/claude-sonnet-5")
    assert default_model(str(env_file)) == "anthropic/claude-sonnet-5"

    monkeypatch.delenv("MODEL_NAME")
    assert default_model(str(tmp_path / "missing.env")) == "anthropic/claude-sonnet-5"


def test_runs_without_a_score_are_reported_not_counted():
    # 44ee5668 and 82bc8d6a died in setup on 2026-10-09 and the sweep still exited 0.
    from osworld.run_parallel import unscored_runs

    scores = {"a": "1.0", "b": "0", "c": "0.5147", "d": "crashed", "e": "no-result", "f": "missing"}
    assert unscored_runs(scores) == ["d", "e", "f"]


def test_a_crashed_agent_is_listed_apart_from_real_scores(tmp_path):
    # run_task_sdk grades the VM even when the agent died and prints a normal-looking
    # "RESULT score=0.0"; only agent= tells a dead agent from a real zero.
    from osworld.run_parallel import agent_faults, scrape_agent_status, scrape_score

    log = tmp_path / "task.log"
    log.write_text("Agent failed (exit 1) after 3s\nRESULT score=0.0 agent=failed (exit 1)\n", encoding="utf-8")
    assert scrape_score(str(log)) == "0.0"
    assert scrape_agent_status(str(log)) == "failed (exit 1)"
    statuses = {"ok": "completed", "dead": "failed (exit 1)", "slow": "timeout", "old": None}
    assert agent_faults(statuses) == {"dead": "failed (exit 1)", "slow": "timeout", "old": "unknown"}


def test_the_gui_budget_reaches_the_runner():
    # A budget run_parallel drops would run the leaderboard sweep uncapped.
    assert "--max-gui-steps" not in task_cmd("python", "run_task_sdk.py", f"chrome/{UUID}", "x", _args())
    cmd = task_cmd("python", "run_task_sdk.py", f"chrome/{UUID}", "x", _args(max_gui_steps=50))
    assert cmd[cmd.index("--max-gui-steps") + 1] == "50"
