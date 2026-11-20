# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Admission boundaries: these fixtures are not actual human approvals."""

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


def test_review_packet_keeps_database_episode_order_and_existing_pending_slots(registry):
    for name in ("z", "a", "中"):
        pending = attempt(registry, name)
    registry.withdraw("example.flow", "1", state="retired", reason="completed")
    result = evidence(registry, pending)
    registry.record_evidence(result)
    packet = registry.review_packet("example.flow", "1")
    assert list(packet) == ["definition", "evidence", "packet_sha256"]
    assert [item["attempt"]["episode_id"] for item in packet["evidence"]] == ["a", "z", "中"]
    assert [item["result"] for item in packet["evidence"]] == [None, None, asdict(result)]
    assert packet["packet_sha256"] == fingerprint({key: packet[key] for key in ("definition", "evidence")})
