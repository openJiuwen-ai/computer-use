# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json
from pathlib import Path

import pytest
from computer_use import Computer
from computer_use.session.events import register_observation_delivery, set_observation_handler
from computer_use.session.observation import lazy_observation
from computer_use_native_tools.evaluation.ledger import Check, Episode


def test_real_session_cleanup_and_opaque_observation_do_not_poison_journal(tmp_path):
    run = Episode(tmp_path, {})
    set_observation_handler(run.observation)
    computer = Computer()
    try:
        run.observation(
            {
                "kind": "observed",
                "observation": {
                    "source": computer._controller,
                    "owner": object(),
                    "document": object(),
                    "scope": "test document",
                    "view": "tree",
                    "state": {"text": "captured content"},
                    "refs": {1: object()},
                },
            }
        )
        computer.close()
    finally:
        set_observation_handler(None)
    result = run.finish(Check("UNKNOWN", "file", {}), reset_duration_s=0, cleanup={})
    assert result["recording_complete"]
    rows = [json.loads(line) for line in Path(result["journal"]["logPath"]).read_text().splitlines()]
    assert any(row.get("text") == "captured content" for row in rows)
    assert any(row.get("change") == "invalidate" for row in rows)


def test_prepared_observation_is_recorded_without_rendering(tmp_path):
    class Prepared:
        @property
        def text(self):
            pytest.fail("Recording a prepared observation must not render the tree")

        def summary_data(self):
            return {"sections": 1}

    run = Episode(tmp_path, {})
    state = lazy_observation(Prepared())
    delivered = []
    register_observation_delivery(state, lambda: delivered.append(True))
    run.observation(
        {
            "kind": "observed",
            "observation": {"scope": "document", "view": "tree", "state": state, "refs": {1: object()}},
        }
    )
    assert delivered == [True]
    result = run.finish(Check("UNKNOWN", "file", {}), reset_duration_s=0, cleanup={})
    rows = [json.loads(line) for line in Path(result["journal"]["logPath"]).read_text().splitlines()]
    row = next(row for row in rows if row["kind"] == "observation")
    assert row["presentation"] == {"sections": 1} and not row["text_materialized"]
