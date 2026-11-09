# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Admission boundaries: these fixtures are not actual human approvals."""

import sqlite3
from dataclasses import asdict, replace

import pytest

from computer_use.programs.admission import Attempt, Evidence, HumanReview, Registry, fingerprint


DEFINITION = dict(
    id="example.flow", version="1", implementation_sha256="a" * 64, acceptance="independent-test", constraints="shared"
)


@pytest.fixture
def registry(tmp_path):
    result = Registry(tmp_path / "admission.sqlite")
    result.register(DEFINITION)
    return result


def attempt(registry, name, **changes):
    value = Attempt(name, "example.flow", "1", "a" * 64, "test-task", fingerprint(name), "TRUE")
    value = replace(value, **changes)
    registry.assign_instance(value.task, value.instance_sha256, "admission")
    registry.reserve_attempt(value)
    return value


def evidence(registry, planned, **changes):
    value = Evidence(
        **asdict(planned),
        split="admission",
        summary_sha256="b" * 64,
        source_sha256="c" * 64,
        driver_sha256="d" * 64,
        recording_complete=True,
        outcome="success",
        acceptance="TRUE",
    )
    value = replace(value, **changes)
    registry.record_evidence(value)
    return value


def ready(registry):
    evidence(registry, attempt(registry, "positive"))
    evidence(
        registry, attempt(registry, "negative", expected_acceptance="FALSE"), outcome="failure", acceptance="FALSE"
    )


def review(registry):
    packet = registry.review_packet("example.flow", "1")
    return HumanReview("unit-test reviewer", "unit-test fixture, not human authorization", packet["packet_sha256"])


def test_candidate_denied_until_exact_review_then_persists(registry):
    assert not registry.select(DEFINITION).allowed
    ready(registry)
    approval = review(registry)
    registry.admit("example.flow", "1", approval)
    reopened = Registry(registry.path)
    decision = reopened.select(DEFINITION)
    assert decision.allowed and decision.state == "admitted"
    assert decision.review == asdict(approval)
    assert not reopened.select({**DEFINITION, "version": "2"}).allowed


def test_version_cannot_be_replaced_or_self_admit(registry):
    with pytest.raises(ValueError, match="immutable"):
        registry.register({**DEFINITION, "implementation_sha256": "e" * 64})
    with pytest.raises(ValueError, match="grant itself"):
        registry.register({**DEFINITION, "state": "admitted"})
    registry.register(DEFINITION)
    assert registry.select(DEFINITION).state == "candidate"


@pytest.mark.parametrize(
    "changes", [{"implementation_sha256": "e" * 64}, {"constraints": "exclusive"}, {"acceptance": "weaker"}]
)
def test_drift_invalidates_before_selection_and_cannot_restore_old_approval(registry, changes):
    ready(registry)
    registry.admit("example.flow", "1", review(registry))
    decision = registry.select({**DEFINITION, **changes})
    assert not decision.allowed and decision.reason == "definition_changed"
    assert Registry(registry.path).select(DEFINITION).state == "invalidated"
    with pytest.raises(ValueError, match="Only a candidate"):
        registry.admit("example.flow", "1", review(registry))


@pytest.mark.parametrize("split", ["development", "final"])
def test_instances_cannot_cross_splits_and_nonadmission_results_are_excluded(registry, split):
    fp = fingerprint({"task": "test-task", "parameters": "same values despite different episode IDs"})
    registry.assign_instance("test-task", fp, split)
    with pytest.raises(ValueError, match="cannot cross"):
        registry.assign_instance("test-task", fp, "admission")
    with pytest.raises(ValueError, match="assigned to admission"):
        registry.reserve_attempt(Attempt("attempt", "example.flow", "1", "a" * 64, "test-task", fp, "TRUE"))
    item = evidence(registry, attempt(registry, "okay"))
    with pytest.raises(ValueError, match="Only admission"):
        registry.record_evidence(replace(item, split=split))


def test_pending_attempt_stays_in_review_and_blocks_admission(registry):
    ready(registry)
    attempt(registry, "interrupted")
    packet = registry.review_packet("example.flow", "1")
    assert len(packet["evidence"]) == 3 and any(row["result"] is None for row in packet["evidence"])
    with pytest.raises(ValueError, match="complete records"):
        registry.admit("example.flow", "1", review(registry))


def test_new_failure_invalidates_stale_review_without_removing_failure(registry):
    ready(registry)
    approval = review(registry)
    evidence(registry, attempt(registry, "timeout"), outcome="timeout", acceptance="UNKNOWN")
    with pytest.raises(ValueError, match="complete evidence set"):
        registry.admit("example.flow", "1", approval)
    packet = registry.review_packet("example.flow", "1")
    assert {row["result"]["outcome"] for row in packet["evidence"]} == {"success", "failure", "timeout"}
    registry.admit("example.flow", "1", review(registry))
    assert registry.select(DEFINITION).allowed  # Explicit review includes the failure; no automatic rate claim.


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"recording_complete": False}, "complete records"),
        ({"acceptance": "UNKNOWN", "outcome": "unknown"}, "independently successful"),
    ],
)
def test_incomplete_or_unknown_positive_cannot_admit(registry, changes, message):
    evidence(registry, attempt(registry, "positive"), **changes)
    evidence(
        registry, attempt(registry, "negative", expected_acceptance="FALSE"), outcome="failure", acceptance="FALSE"
    )
    with pytest.raises(ValueError, match=message):
        registry.admit("example.flow", "1", review(registry))


@pytest.mark.parametrize("recording_complete", [0, 1, "true", None])
def test_recording_completeness_requires_boolean_evidence(registry, recording_complete):
    planned = attempt(registry, "positive")
    with pytest.raises(ValueError, match="Invalid episode evidence"):
        evidence(registry, planned, recording_complete=recording_complete)
    packet = registry.review_packet("example.flow", "1")
    assert packet["evidence"][0]["result"] is None


def test_missing_negative_or_false_accept_blocks_admission(registry):
    evidence(registry, attempt(registry, "positive"))
    with pytest.raises(ValueError, match="negative"):
        registry.admit("example.flow", "1", review(registry))
    evidence(registry, attempt(registry, "false-accept", expected_acceptance="FALSE"))
    with pytest.raises(ValueError, match="False acceptance"):
        registry.admit("example.flow", "1", review(registry))


def test_evidence_and_expected_outcome_cannot_be_rewritten(registry):
    planned = attempt(registry, "negative", expected_acceptance="FALSE")
    with pytest.raises(ValueError, match="reserved twice"):
        registry.reserve_attempt(planned)
    value = evidence(registry, planned, acceptance="FALSE", outcome="failure")
    registry.record_evidence(value)  # Idempotent delivery of the identical result.
    with pytest.raises(ValueError, match="overwritten"):
        registry.record_evidence(replace(value, acceptance="TRUE"))
    with pytest.raises(ValueError, match="reserved attempt"):
        registry.record_evidence(replace(value, expected_acceptance="TRUE"))
    with pytest.raises(ValueError, match="Reserve an attempt"):
        registry.record_evidence(replace(value, episode_id="unreserved"))


def test_withdrawal_is_terminal_and_retains_review(registry):
    ready(registry)
    registry.admit("example.flow", "1", review(registry))
    registry.withdraw("example.flow", "1", state="invalidated", reason="observed application drift")
    assert not registry.select(DEFINITION).allowed
    registry.withdraw("example.flow", "1", state="retired", reason="replaced by version 2")
    assert registry.select(DEFINITION).state == "retired" and registry.select(DEFINITION).review
    with pytest.raises(ValueError, match="Invalid lifecycle"):
        registry.withdraw("example.flow", "1", state="invalidated", reason="attempted revival")


def test_locked_or_corrupt_registry_fails_closed_without_retry(registry, tmp_path):
    db = sqlite3.connect(registry.path)
    try:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            registry.select(DEFINITION)
    finally:
        db.close()
    corrupt = tmp_path / "corrupt.sqlite"
    corrupt.write_text("not a registry")
    with pytest.raises(sqlite3.DatabaseError):
        Registry(corrupt)
