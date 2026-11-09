# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from computer_use.programs.admission import Attempt, Evidence, HumanReview, Registry, fingerprint
from computer_use.programs.artifact import Artifact, validate_parameters
from computer_use.programs.pipeline import execute
from program_fixtures import artifact_at


async def environment():
    return {}


def invoke(artifact, computer, parameters, **kwargs):
    return asyncio.run(
        execute(
            artifact.path, computer, parameters, environment, emit=lambda event: None, checkpoint=lambda: None, **kwargs
        )
    )


def test_same_runtime_executes_two_external_task_contracts_without_product_registration(tmp_path):
    first = artifact_at(
        tmp_path / "first",
        program_id="workspace.increment",
        schema={"scope": {"type": "string"}, "count": {"type": "integer", "minimum": 1}},
        acceptance={"condition": "counter_equals", "scope_parameter": "scope", "expected_parameters": ["count"]},
        source='async def run(ctx, computer, p):\n    await ctx.step("increment", lambda: computer.change(p["count"]), write=True)\n',
    )
    second = artifact_at(
        tmp_path / "second",
        program_id="workspace.flag",
        schema={"scope": {"type": "string"}, "enabled": {"type": "boolean"}},
        acceptance={"condition": "flag_equals", "scope_parameter": "scope", "expected_parameters": ["enabled"]},
        source='async def run(ctx, computer, p):\n    await ctx.step("set_flag", lambda: computer.change(p["enabled"]), write=True)\n',
    )
    calls = []

    async def change(value):
        calls.append(value)
        return SimpleNamespace(status="dispatched", detail="unconfirmed")

    computer = SimpleNamespace(change=change)
    one = invoke(first, computer, {"scope": "counter", "count": 3}, development=True)
    two = invoke(second, computer, {"scope": "flag", "enabled": False}, development=True)
    assert calls == [3, False]
    assert one.status == two.status == "awaiting_acceptance"
    assert one.acceptance_condition == "counter_equals" and two.acceptance_condition == "flag_equals"
    assert one.program["id"] != two.program["id"]


def test_candidate_is_not_imported_before_admission_or_explicit_development_authorization(tmp_path):
    artifact = artifact_at(tmp_path / "candidate", source='raise RuntimeError("must not import candidate")\n')
    registry = Registry(tmp_path / "registry.sqlite")
    registry.register(artifact.definition)
    result = invoke(artifact, None, dict(document="doc", initial="a", replacement="b"), registry=registry)
    assert result.status == "refused" and result.failure["effect"] == "none"
    assert "must not import" not in result.failure["detail"]
    missing = invoke(artifact, None, dict(document="doc", initial="a", replacement="b"))
    assert missing.status == "refused"


def test_admitted_artifact_executes_but_changed_source_invalidates_before_import(tmp_path):
    artifact = artifact_at(
        tmp_path / "artifact", source='async def run(ctx, computer, p):\n    ctx.check("ready", "TRUE")\n'
    )
    registry = Registry(tmp_path / "registry.sqlite")
    program = artifact.definition
    registry.register(program)
    for expected in ("TRUE", "FALSE"):
        instance = fingerprint(expected)
        registry.assign_instance("fixture", instance, "admission")
        registry.reserve_attempt(
            Attempt(expected, program["id"], "1", program["implementation_sha256"], "fixture", instance, expected)
        )
        registry.record_evidence(
            Evidence(
                expected,
                program["id"],
                "1",
                program["implementation_sha256"],
                "fixture",
                instance,
                "admission",
                "a" * 64,
                "b" * 64,
                "c" * 64,
                True,
                "success" if expected == "TRUE" else "failure",
                expected,
                expected,
            )
        )
    packet = registry.review_packet(program["id"], "1")
    registry.admit(
        program["id"], "1", HumanReview("unit-test reviewer", "synthetic approval fixture", packet["packet_sha256"])
    )
    parameters = dict(document="doc", initial="old", replacement="new")
    assert invoke(artifact, None, parameters, registry=registry).status == "awaiting_acceptance"
    changed = artifact_at(artifact.path, source='raise AssertionError("changed code must not load")\n')
    result = invoke(changed, None, parameters, registry=registry)
    assert result.status == "refused" and "definition_changed" in result.failure["detail"]
    assert registry.select(artifact.definition).state == "invalidated"


def test_manifest_tampering_or_file_escape_is_rejected_before_import(tmp_path):
    artifact = artifact_at(tmp_path / "artifact")
    (artifact.path / "program.py").write_text('raise RuntimeError("changed")')
    with pytest.raises(ValueError, match="digest"):
        Artifact.read(artifact.path)
    artifact = artifact_at(artifact.path)
    entry = artifact.path / "program.py"
    external = tmp_path / "outside.py"
    external.write_bytes(entry.read_bytes())
    entry.unlink()
    entry.symlink_to(external)
    with pytest.raises(ValueError, match="regular files"):
        Artifact.read(artifact.path)


@pytest.mark.parametrize(
    "schema,value",
    [
        ({"type": "integer"}, True),
        ({"type": "number"}, float("nan")),
        ({"type": "integer", "minimum": 1}, 0),
        ({"type": "string", "max_length": 2}, "long"),
        ({"type": "string", "pattern": "[a-z]+"}, "123"),
        ({"type": "string", "enum": ["blue"]}, "red"),
        ({"type": "string", "ignored_rule": True}, "value"),
    ],
)
def test_parameter_schema_rejects_invalid_or_unknown_constraints(schema, value):
    with pytest.raises(ValueError):
        validate_parameters({"value": schema}, {"value": value})


def test_unknown_parameters_and_bad_scope_cannot_load_code(tmp_path):
    artifact = artifact_at(tmp_path / "artifact", source='raise AssertionError("no import")\n')
    with pytest.raises(ValueError, match="exactly"):
        invoke(artifact, None, {"document": "doc", "initial": "a", "replacement": "b", "extra": 1}, development=True)
    manifest = artifact.manifest
    manifest["acceptance"]["scope_parameter"] = "missing"
    (artifact.path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Acceptance"):
        Artifact.read(artifact.path)


def test_task_cannot_swallow_unknown_check_and_continue_input(tmp_path):
    artifact = artifact_at(
        tmp_path / "artifact",
        source="""async def run(ctx, computer, p):
    try:
        ctx.check("required_precondition", "UNKNOWN")
    except Exception:
        pass
    await ctx.step("unsafe_continue", computer.change, write=True)
""",
    )
    calls = []

    async def change():
        calls.append("input")
        return SimpleNamespace(status="dispatched", detail="sent")

    result = invoke(
        artifact, SimpleNamespace(change=change), dict(document="doc", initial="a", replacement="b"), development=True
    )
    assert calls == [] and result.status == "refused"
    assert result.checks[0].value == "UNKNOWN"


@pytest.mark.parametrize("wrapped_error", [False, True])
def test_task_cannot_swallow_partial_input_and_report_normal_return(tmp_path, wrapped_error):
    from computer_use.errors import ComputerUseError

    artifact = artifact_at(
        tmp_path / "artifact",
        source="""async def run(ctx, computer, p):
    try:
        await ctx.step("write", computer.change, write=True)
    except Exception:
        pass
"""
        + ('    raise RuntimeError("outer wrapper failed")\n' if wrapped_error else ""),
    )
    calls = []

    async def change():
        calls.append("partial")
        raise ComputerUseError(
            dict(code="execution_unknown", target="original", detail="partial", effect="partial", next="observe")
        )

    result = invoke(
        artifact, SimpleNamespace(change=change), dict(document="doc", initial="a", replacement="b"), development=True
    )
    assert calls == ["partial"] and result.status == "stopped" and result.failure["effect"] == "partial"


def test_program_cannot_start_concurrent_context_actions(tmp_path):
    artifact = artifact_at(
        tmp_path / "artifact",
        source="""import asyncio
async def run(ctx, computer, p):
    await asyncio.gather(ctx.step("one", computer.change, write=True), ctx.step("two", computer.change, write=True))
""",
    )
    calls = []

    async def change():
        calls.append("input")
        return SimpleNamespace(status="dispatched", detail="sent")

    result = invoke(
        artifact, SimpleNamespace(change=change), dict(document="doc", initial="a", replacement="b"), development=True
    )
    assert calls == [] and result.status == "stopped"
    assert "owning task" in result.failure["detail"]


def test_product_has_no_builtin_task_import_or_catalog():
    root = Path(__file__).resolve().parents[2]
    for folder in (root / "computer_use_engine/programs", root / "scripts/evaluation"):
        for source in folder.glob("*.py"):
            assert "TextEdit" not in source.read_text()
            assert "programs.textedit" not in source.read_text()
    assert not (root / "computer_use_engine/programs/textedit/pipeline.py").exists()
    assert not (root / "scripts/evaluation/textedit.py").exists()
