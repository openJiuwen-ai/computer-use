# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""run_task_sdk.run_agent waits out a guest server that is slow to answer.

The guest server also serves the agent's own calls, so a status check can time out while the agent works
(libreoffice_calc 04d9aeaf, 2026-11-25: one 35 s read timeout ended the run before grading). Runs in your OSWorld
checkout's .venv, like the runner:
    PYTHONPATH=<osworld> <osworld>/.venv/Scripts/python.exe -m pytest osworld/tests/test_sdk_runner_wait.py
"""
from __future__ import annotations

import argparse
import os

import pytest

if not os.environ.get("OSWORLD_VERIFIED_ROOT"):
    pytest.skip("OSWORLD_VERIFIED_ROOT is not set", allow_module_level=True)
pytest.importorskip("desktop_env")
import requests  # noqa: E402

from osworld import run_task_sdk  # noqa: E402


class FlakyGuest:
    """Answers the exit-code check with a read timeout first, then with the exit code."""

    def __init__(self, failures: int, code: str = "0"):
        self.failures, self.code, self.polls = failures, code, 0

    def sh(self, script: str, timeout: int = 30) -> str:
        if script.startswith("cat ") and "exit_code" in script:  # the status check, not the launch
            self.polls += 1
            if self.polls <= self.failures:
                raise requests.exceptions.ReadTimeout("Read timed out. (read timeout=35)")
            return self.code
        if script.startswith("cat ") and "agent.log" in script:
            return "agent log"
        if script.startswith("cat "):
            raise RuntimeError("guest command exited 1")  # an optional results file the run did not write
        return ""


def _args(time_budget: float = 0.0) -> argparse.Namespace:
    return argparse.Namespace(max_iterations=0, max_gui_steps=100, autosave_every=6, gate=True, gate_binding=True,
                              time_budget=time_budget)


def test_a_status_check_that_times_out_does_not_end_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(run_task_sdk.time, "sleep", lambda _: None)
    monkeypatch.setattr(run_task_sdk, "agent_config", lambda args: {"model": "m", "api_key": "k", "api_base": "b"})
    guest = FlakyGuest(failures=2)
    assert run_task_sdk.run_agent(guest, "Do it.", _args(), str(tmp_path)) == "completed"
    assert guest.polls == 3
    assert (tmp_path / "agent.log").read_text(encoding="utf-8") == "agent log"


def test_the_time_budget_still_ends_a_run_whose_status_never_comes(tmp_path, monkeypatch):
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(run_task_sdk.time, "sleep", lambda _: None)
    monkeypatch.setattr(run_task_sdk.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(run_task_sdk, "agent_config", lambda args: {"model": "m", "api_key": "k", "api_base": "b"})
    guest = FlakyGuest(failures=10_000)
    assert run_task_sdk.run_agent(guest, "Do it.", _args(time_budget=300), str(tmp_path)) == "timeout"
