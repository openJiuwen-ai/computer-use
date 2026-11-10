# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import json
from pathlib import Path

import pytest

from computer_use.errors import ComputerUseError
from computer_use import Computer
from computer_use.session.events import set_observation_handler
from computer_use_mcp.diagnostics import Journal
from computer_use_native_tools.evaluation.ledger import Check, EnvironmentBlocked, Episode, enumerate_episodes
from computer_use_native_tools.evaluation.provenance import digest
from computer_use_native_tools.evaluation.checks import evaluate_file
from program_fixtures import observed_editor_ref


def test_receipt_is_not_business_success_and_unknown_is_not_success(tmp_path):
    run = Episode(tmp_path, {"task": "test"})
    with run.step("replace"):
        run.action({"status": "verified", "target": "test", "detail": "API returned"})
    result = run.finish(Check("UNKNOWN", "document", {}), reset_duration_s=0, cleanup={})
    assert result["outcome"] == "unknown"
    rows = [json.loads(line) for line in Path(result["journal"]["logPath"]).read_text().splitlines()]
    receipt = next(row for row in rows if row["kind"] == "action_receipt")
    assert receipt["episode_id"] == run.id and receipt["step"] == "replace"
    assert result["model_usage"]["cost_usd"] is None


@pytest.mark.parametrize(
    "error,outcome,effect",
    [
        (TimeoutError(), "timeout", "unknown"),
        (asyncio.CancelledError(), "cancelled", "unknown"),
        (EnvironmentBlocked(), "blocked", "unknown"),
        (
            ComputerUseError(
                dict(code="execution_unknown", target="editor", detail="partial", effect="partial", next="observe")
            ),
            "error",
            "partial",
        ),
    ],
)
def test_exception_preserves_episode_and_disallows_replay(tmp_path, error, outcome, effect):
    run = Episode(tmp_path, {"task": "test"})
    dispatched = []
    with pytest.raises(type(error)):
        with run.step("input"):
            dispatched.append(1)
            raise error
    with pytest.raises(RuntimeError):
        with run.step("input-again"):
            dispatched.append(2)
    # Even TRUE file evidence cannot erase an execution exception.
    result = run.finish(Check("TRUE", "document", {}), reset_duration_s=0, cleanup={})
    assert result["outcome"] == outcome
    assert result["failure"]["effect"] == effect
    assert dispatched == [1]
    assert len(enumerate_episodes(tmp_path)) == 1


def test_monotonic_elapsed_separates_reset_and_cleanup(tmp_path):
    now = [100.0]
    run = Episode(tmp_path, {}, clock=lambda: now[0])
    now[0] = 112
    result = run.finish(Check("TRUE", "file", {}), reset_duration_s=4, cleanup={"duration_s": 2})
    assert result["wall_duration_s"] == 12
    assert result["duration_s"] == 6
    assert result["reset_duration_s"] == 4


def test_recording_failure_cannot_become_usable_success(tmp_path):
    run = Episode(tmp_path, {})
    with run.step("input"):
        run.journal._error = "injected disk full after dispatch"
        run.action({"status": "dispatched"})
    with pytest.raises(RuntimeError):
        with run.step("next-input"):
            pytest.fail("Cannot send input after losing evidence")
    result = run.finish(Check("TRUE", "file", {}), reset_duration_s=0, cleanup={})
    assert result["recording_complete"] is False
    assert "logError" in result["journal"]


def test_interrupted_and_corrupt_runs_stay_in_denominator(tmp_path):
    first = Episode(tmp_path, {})
    second = Episode(tmp_path, {})
    (second.directory / "summary.json").write_text("{unfinished")
    runs = enumerate_episodes(tmp_path)
    assert {r["episode_id"] for r in runs} == {first.id, second.id}
    assert all(r["outcome"] == "incomplete" and not r["recording_complete"] for r in runs)


def test_log_failure_at_step_start_still_allows_terminal_evidence(tmp_path, monkeypatch):
    run = Episode(tmp_path, {})
    monkeypatch.setattr(run.journal, "record", lambda _: setattr(run.journal, "_error", "disk full"))
    with pytest.raises(RuntimeError):
        with run.step("input"):
            pytest.fail("No action may start without its start record")
    result = run.finish(Check("UNKNOWN", "file", {}), reset_duration_s=0, cleanup={})
    assert result["recording_complete"] is False
    assert result["steps"][0]["status"] == "error"


def test_independent_evaluator_rejects_existing_wrong_or_changed_control(tmp_path):
    document, control = tmp_path / "file.txt", tmp_path / "control.txt"
    control.write_bytes(b"unchanged")
    control_hash = digest(control.read_bytes())
    assert evaluate_file(document, b"target", control, control_hash).value == "UNKNOWN"
    for content in (b"", b"old target", b"target extra", b"Target", b"target\n"):
        document.write_bytes(content)
        assert evaluate_file(document, b"target", control, control_hash).value == "FALSE"
    document.write_bytes(b"target")
    assert evaluate_file(document, b"target", control, control_hash).value == "TRUE"
    control.write_bytes(b"changed")
    assert evaluate_file(document, b"target", control, control_hash).value == "FALSE"


def test_editor_reference_requires_one_fresh_observed_identifier():
    assert observed_editor_ref("Window 0: test\n\t12 文本输入区, ID: fixture-editor") == 12
    assert observed_editor_ref("\t79 text input, Value: other, ID: fixture-editor") == 79
    for observation in ("", "2 text input, ID: other", "1 editor, ID: fixture-editor\n2 editor, ID: fixture-editor"):
        with pytest.raises(RuntimeError):
            observed_editor_ref(observation)


def test_journal_explicit_directory_preserves_default_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPUTER_USE_LOG_DIR", str(tmp_path / "default"))
    assert Journal().path.parent == tmp_path / "default"
    assert Journal(tmp_path / "episode").path.parent == tmp_path / "episode"


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
        computer._controller.transport.close()
    finally:
        set_observation_handler(None)
    result = run.finish(Check("UNKNOWN", "file", {}), reset_duration_s=0, cleanup={})
    assert result["recording_complete"]
    rows = [json.loads(line) for line in Path(result["journal"]["logPath"]).read_text().splitlines()]
    assert any(row.get("text") == "captured content" for row in rows)
    assert any(row.get("change") == "invalidate" for row in rows)


def test_invalid_check_and_duplicate_finish_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        Check("yes", "file", {})
    run = Episode(tmp_path, {})
    run.finish(Check("FALSE", "file", {}), reset_duration_s=0, cleanup={})
    with pytest.raises(RuntimeError):
        run.finish(Check("TRUE", "file", {}), reset_duration_s=0, cleanup={})
