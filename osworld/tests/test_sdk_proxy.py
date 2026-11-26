# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The proxy check stops a run before its VM starts when the proxy cannot work.

DesktopEnv itself would only fail at a proxy task's setup, after the VM booted. Moved from the fork agent's tests
(test_jiuwen_run_switches.py) with osworld/proxy.py.
"""
from __future__ import annotations

import argparse
import json
import os

import pytest

from osworld.proxy import proxy_problem
from osworld.run_parallel import task_cmd


def test_proxy_problem_names_the_missing_config_and_accepts_a_proxy_list(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXY_CONFIG_FILE", "proxy.json")
    assert "proxy.json" in proxy_problem(str(tmp_path)) and "--no-proxy" in proxy_problem(str(tmp_path))
    (tmp_path / "proxy.json").write_text("[]", encoding="utf-8")
    assert "non-empty" in proxy_problem(str(tmp_path))
    (tmp_path / "proxy.json").write_text(json.dumps([{"host": "p.example", "port": 823, "username": "u",
                                                      "password": "p"}]), encoding="utf-8")
    assert proxy_problem(str(tmp_path)) == ""


def test_proxy_problem_rejects_the_repos_placeholder_template(monkeypatch):
    # The placeholder template ships with the OSWorld-Verified checkout, not with this repo.
    verified_root = os.environ.get("OSWORLD_VERIFIED_ROOT")
    if not verified_root:
        pytest.skip("OSWORLD_VERIFIED_ROOT is not set")
    monkeypatch.delenv("PROXY_CONFIG_FILE", raising=False)
    assert "placeholders" in proxy_problem(verified_root)


def test_no_proxy_reaches_the_runner():
    # Without it, a sweep someone ran with --no-proxy would still route proxy tasks through the proxy.
    args = argparse.Namespace(max_iterations=0, time_budget=None, max_gui_steps=None, proxy=True)
    assert "--no-proxy" not in task_cmd("python", "run_task_sdk.py", "chrome/x", "x", args)
    args.proxy = False
    assert "--no-proxy" in task_cmd("python", "run_task_sdk.py", "chrome/x", "x", args)
