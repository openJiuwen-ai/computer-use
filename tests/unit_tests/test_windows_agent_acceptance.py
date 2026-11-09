# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import importlib
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("answer", "effect_ok", "passed"),
    [
        ("结果是 1,200。", True, True),
        ("结果是 12,000。", True, False),
        ("结果是 1,200。", False, False),
    ],
)
def test_calculator_answer_matches_complete_numeric_value(monkeypatch, tmp_path, answer, effect_ok, passed):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "manual"))
    workflow = importlib.import_module("windows_agent_workflow")
    monkeypatch.setattr(workflow, "native_driver", lambda: {"command": "unused"})
    monkeypatch.setattr(workflow, "startup_provenance", lambda _command: {})
    monkeypatch.setattr(
        workflow,
        "calculator",
        lambda *_: {
            "effect_ok": effect_ok, "exit": 0, "desktop_calls": 3, "expected_answer": "1200", "answer": answer,
        },
    )

    assert workflow.attempt("calculator", tmp_path, None, 1)["automated_pass"] is passed
