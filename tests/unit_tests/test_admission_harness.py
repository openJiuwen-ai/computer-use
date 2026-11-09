# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Generic episode/admission bridge; no task-specific harness or desktop."""

import json
import sys

import pytest

from computer_use.programs.admission import Registry
from computer_use_native_tools.evaluation.admission import (
    instance_fingerprint,
    main,
    prepare_episode,
    record_summary,
    seed_development,
)
from computer_use_native_tools.evaluation.ledger import Check, EnvironmentBlocked, Episode
from computer_use_native_tools.evaluation.provenance import digest
from program_fixtures import artifact_at


def setup_episode(tmp_path, *, split="admission", value="fresh", registry=None):
    artifact = artifact_at(tmp_path / "artifact")
    registry = registry or Registry(tmp_path / "registry.sqlite")
    metadata = dict(
        task="synthetic-task",
        split=split,
        arm="program",
        program=artifact.definition,
        instance_parameters={"value": value},
        expected_acceptance="TRUE",
        runtime={"source": {"source_sha256": "a" * 64}, "driver": {"sha256": "b" * 64}},
    )
    metadata["instance_sha256"] = instance_fingerprint(metadata)
    return registry, artifact, Episode(tmp_path / "runs", metadata)


def test_candidate_final_gate_prevents_entering_reset(tmp_path):
    registry, artifact, episode = setup_episode(tmp_path, split="final")
    registry.register(artifact.definition)
    reached = []
    with pytest.raises(EnvironmentBlocked):
        prepare_episode(registry, episode, artifact)
        reached.append("reset")
    assert reached == []
    events = [json.loads(line) for line in episode.journal.path.read_text().splitlines()]
    selection = next(row["selection"] for row in events if row["kind"] == "program_selection")
    assert selection["state"] == "candidate" and not selection["allowed"]
    assert registry.review_packet(artifact.definition["id"], "1")["evidence"] == []


def test_reserved_attempt_retains_subsequent_environment_failure(tmp_path):
    registry, artifact, episode = setup_episode(tmp_path)
    assert prepare_episode(registry, episode, artifact)
    packet = registry.review_packet(artifact.definition["id"], "1")
    assert packet["evidence"][0]["result"] is None
    episode.failed(EnvironmentBlocked("missing permission"))
    result = episode.finish(Check("UNKNOWN", "document", {}), reset_duration_s=0, cleanup={})
    record_summary(registry, episode.directory / "summary.json")
    evidence = registry.review_packet(artifact.definition["id"], "1")["evidence"][0]["result"]
    assert result["outcome"] == evidence["outcome"] == "blocked"
    assert evidence["acceptance"] == "UNKNOWN"
    assert evidence["summary_sha256"] == digest((episode.directory / "summary.json").read_bytes())


def test_failed_delivery_leaves_summary_and_pending_slot(tmp_path, monkeypatch):
    registry, artifact, episode = setup_episode(tmp_path)
    prepare_episode(registry, episode, artifact)
    episode.finish(Check("FALSE", "document", {}), reset_duration_s=0, cleanup={})
    monkeypatch.setattr(registry, "record_evidence", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        record_summary(registry, episode.directory / "summary.json")
    assert (episode.directory / "summary.json").exists()
    assert registry.review_packet(artifact.definition["id"], "1")["evidence"][0]["result"] is None


def test_development_value_cannot_be_relabelled_as_admission(tmp_path):
    registry, artifact, dev = setup_episode(tmp_path, split="development", value="same")
    dev.finish(Check("FALSE", "document", {}), reset_duration_s=0, cleanup={})
    seed_development(registry, dev.directory.parent)
    _, _, admission = setup_episode(tmp_path, value="same", registry=registry)
    with pytest.raises(ValueError, match="cannot cross"):
        prepare_episode(registry, admission, artifact)


def test_fingerprint_uses_semantic_instance_not_episode_or_case():
    metadata = {"task": "task", "instance_parameters": {"quantity": 2, "category": "blue"}}
    assert instance_fingerprint({**metadata, "episode_id": "a", "case": "success"}) == instance_fingerprint(
        {**metadata, "episode_id": "b", "case": "negative"}
    )


def test_corrupt_history_blocks_allocation_until_reconciled(tmp_path):
    registry, _, episode = setup_episode(tmp_path)
    with pytest.raises(ValueError, match="reconciliation"):
        seed_development(registry, episode.directory.parent)


def test_final_results_cannot_enter_review_packet(tmp_path):
    path = tmp_path / "summary.json"
    path.write_text(json.dumps({"metadata": {"split": "final", "arm": "program"}}))
    with pytest.raises(ValueError, match="Only program admission"):
        record_summary(Registry(tmp_path / "registry.sqlite"), path)


def test_admission_cli_emits_one_unprefixed_json_packet_per_call(tmp_path, monkeypatch, capsys):
    artifact = artifact_at(tmp_path / "artifact", program_id="workspace.审查")
    registry_path = tmp_path / "registry.sqlite"
    base_args = ["admission", "--registry", str(registry_path), "--artifact", str(artifact.path)]
    for command in (["register", "--development-runs", str(tmp_path / "runs")], ["packet"]):
        monkeypatch.setattr(sys, "argv", base_args + command)
        main()
        output = capsys.readouterr()
        packet = Registry(registry_path).review_packet(artifact.definition["id"], "1")
        assert json.loads(output.out) == packet
        assert output.out == json.dumps(packet, ensure_ascii=False, indent=2) + "\n"
        assert output.err == ""


@pytest.mark.parametrize("failed_operation", ["write", "flush"])
def test_admission_cli_does_not_hide_packet_delivery_failure(tmp_path, monkeypatch, failed_operation):
    import io

    artifact = artifact_at(tmp_path / "artifact")
    registry = Registry(tmp_path / "registry.sqlite")
    registry.register(artifact.definition)
    monkeypatch.setattr(
        sys, "argv", ["admission", "--registry", str(registry.path), "--artifact", str(artifact.path), "packet"]
    )
    output = io.StringIO()

    def fail(*args):
        raise OSError("packet delivery failed")

    monkeypatch.setattr(output, failed_operation, fail)
    with monkeypatch.context() as streams:
        streams.setattr(sys, "stdout", output)
        with pytest.raises(OSError, match="packet delivery failed"):
            main()
