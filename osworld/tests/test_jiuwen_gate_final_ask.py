# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A gate that runs out of iterations without a verdict line is asked once more to answer.

chrome 480bcfea (holdgate1022, 2026-10-22): 29 greps of the Chrome binary for a
flag name used up all 40 iterations, and the cua agent got no verdict at all.
"""
from __future__ import annotations

import asyncio
import json

import pytest

gate = pytest.importorskip("osworld.gate")


class _Card:
    name = "grep"


class _Tool:
    card = _Card()


class _Answer:
    type = "answer"

    def __init__(self, output):
        self.payload = {"output": output, "result_type": "answer"}


class _Agent:
    """A DeepAgent stand-in: each stream() answers with the next output."""

    def __init__(self, outputs, queries):
        self._outputs, self._queries = outputs, queries

    async def stream(self, inputs):
        self._queries.append(inputs["query"])
        yield _Answer(self._outputs[len(self._queries) - 1])


def _run(monkeypatch, tmp_path, outputs):
    queries = []
    monkeypatch.setattr(gate, "create_deep_agent", lambda *a, **k: _Agent(outputs, queries))
    monkeypatch.setattr(gate, "dump_trajectory", lambda *a, **k: None)
    result = asyncio.run(gate.run_gate(object(), "Do X.", tools=[_Tool()], rails=[], env_block="",
                                       workspace=str(tmp_path), results_dir=str(tmp_path), session_prefix="t",
                                       log=lambda _m: None))
    return result, queries


def test_gate_without_a_verdict_is_asked_once_more(monkeypatch, tmp_path):
    result, queries = _run(monkeypatch, tmp_path,
                           ["Max iterations reached without completion", "VERDICT: FEASIBLE\nSKILLS: none"])

    assert result.verdict == "FEASIBLE"
    assert len(queries) == 2
    assert "Do X." in queries[1] and gate.GATE_FINAL_ASK in queries[1]
    with open(tmp_path / "gate.json", encoding="utf-8") as f:
        assert json.load(f)["verdict"] == "FEASIBLE"


def test_gate_with_a_verdict_is_not_asked_again(monkeypatch, tmp_path):
    result, queries = _run(monkeypatch, tmp_path, ["VERDICT: INFEASIBLE\nSKILLS: gimp"])

    assert result.verdict == "INFEASIBLE"
    assert len(queries) == 1
