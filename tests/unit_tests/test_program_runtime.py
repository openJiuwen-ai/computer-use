# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Program orchestration behavior using only fake public SDK surfaces."""

import asyncio
from dataclasses import asdict, replace
from time import monotonic
from types import SimpleNamespace

import pytest

from computer_use.errors import ComputerUseError
from computer_use.programs.model import Check, conclude
from computer_use.programs.pipeline import execute
from program_fixtures import artifact_at
from pathlib import Path
from tempfile import TemporaryDirectory


class Desktop:
    def __init__(self, *, initial="old", count=1, editor_count=1):
        self.content, self.count, self.editor_count = initial, count, editor_count
        self.calls, self.reads, self.saves = [], 0, 0
        self.id, self.editor_ref = 7, 42
        self.write_error = None
        self.truncated = False
        self.foreground = "test.application"

    async def environment(self):
        return dict(
            platform="test-os",
            app_version="1",
            locale="test",
            desktop_mode="shared",
            foreground_assist=False,
            foreground_app=self.foreground,
        )

    async def list_apps(self):
        self.calls.append("discover")
        return [SimpleNamespace(id=self.id + n, display_name="sample.txt", is_running=True) for n in range(self.count)]

    def app(self, ref):
        assert ref == self.id
        return self

    async def read(self, **kwargs):
        self.calls.append("read")
        return SimpleNamespace(
            text='Reported input window: 0\nWindow 0: "sample.txt"\n'
            + "\n".join(f"{self.editor_ref + n} editor" for n in range(self.editor_count))
        )

    async def element(self, ref):
        self.calls.append(("element", ref))
        if isinstance(ref, int):
            assert ref == self.editor_ref
        return self

    async def read_text(self):
        self.reads += 1
        return SimpleNamespace(text=self.content, next="cursor" if self.truncated else None)

    async def type_text(self, text, *, replace):
        assert replace
        self.calls.append(("replace", text))
        self.content = text
        if self.write_error:
            raise self.write_error
        return SimpleNamespace(status="verified", detail="editor text replaced")

    async def click(self):
        self.calls.append("save")
        self.saves += 1
        return SimpleNamespace(status="dispatched", detail="save sent")


async def invoke_async(desktop, *, text="new", emit=lambda event: None, checkpoint=lambda: None, **options):
    with TemporaryDirectory() as folder:
        artifact = artifact_at(Path(folder))
        return await execute(
            artifact.path,
            desktop,
            dict(document="sample.txt", initial="old", replacement=text),
            desktop.environment,
            emit=emit,
            checkpoint=checkpoint,
            development=True,
            **options,
        )


def invoke(desktop, **options):
    return asyncio.run(invoke_async(desktop, **options))


def acceptance(result, value="TRUE"):
    return Check(
        result.acceptance_condition,
        value,
        result.scope,
        {"expected_sha256": result.expected_sha256, "execution_id": result.execution_id},
        monotonic(),
    )


def test_success_requires_fresh_external_acceptance_and_keeps_progress():
    desktop, events = Desktop(), []
    result = invoke(desktop, emit=events.append)
    assert result.status == "awaiting_acceptance" and result.observation_required
    assert desktop.content == "new" and desktop.saves == 1
    assert "replacement_visible" in result.confirmed_progress
    assert result.steps[-1]["receipt"]["status"] == "dispatched"
    assert conclude(result, acceptance(result)).status == "succeeded"
    assert {event["execution_id"] for event in events} == {result.execution_id}
    # Results retain evidence, never resumable handles/session bindings.
    import json

    json.dumps(asdict(result))


@pytest.mark.parametrize("value,status", [("FALSE", "failed"), ("UNKNOWN", "unconfirmed")])
def test_unsatisfied_business_acceptance_cannot_trigger_more_input(value, status):
    desktop = Desktop()
    result = invoke(desktop)
    before = list(desktop.calls)
    final = conclude(result, acceptance(result, value))
    assert final.status == status and final.observation_required
    assert desktop.calls == before
    with pytest.raises(ValueError, match="only once"):
        conclude(final, acceptance(result))


@pytest.mark.parametrize("count", [0, 2])
def test_missing_or_ambiguous_document_stops_before_input(count):
    desktop = Desktop(count=count)
    result = invoke(desktop)
    assert result.status == "refused"
    assert result.checks[-1].value == "FALSE"
    assert desktop.content == "old" and not desktop.saves


@pytest.mark.parametrize("editor_count", [0, 2])
def test_missing_or_ambiguous_editor_is_unknown_and_not_success(editor_count):
    desktop = Desktop(editor_count=editor_count)
    result = invoke(desktop)
    assert result.status == "refused" and result.checks[-1].value == "UNKNOWN"
    assert not any(isinstance(call, tuple) and call[0] == "replace" for call in desktop.calls)


def test_truncated_or_changed_initial_content_never_dispatches():
    for desktop in (Desktop(initial="some other document"), Desktop()):
        desktop.truncated = desktop.content == "old"
        result = invoke(desktop)
        assert result.status == "refused" and not desktop.saves
        assert not any(isinstance(call, tuple) and call[0] == "replace" for call in desktop.calls)


@pytest.mark.parametrize("effect", ["possible", "partial"])
def test_uncertain_replace_stops_once_without_save_or_replay(effect):
    desktop = Desktop()
    desktop.write_error = ComputerUseError(
        dict(code="execution_unknown", target="editor", detail="injected", effect=effect, next="observe")
    )
    result = invoke(desktop)
    assert result.status == "stopped" and result.failure["effect"] == effect
    assert "initial_content" in result.confirmed_progress and "replacement_visible" not in result.confirmed_progress
    assert [c for c in desktop.calls if isinstance(c, tuple) and c[0] == "replace"] == [("replace", "new")]
    assert desktop.saves == 0
    assert conclude(result, acceptance(result)).status == "stopped"


def test_environment_drift_after_replace_retains_visible_checkpoint_and_stops():
    desktop = Desktop()

    async def environment():
        return {
            **await Desktop.environment(desktop),
            "foreground_app": "other-app" if desktop.content == "new" else "test.application",
        }

    desktop.environment = environment
    result = invoke(desktop)
    assert result.status == "stopped" and "replacement_visible" in result.confirmed_progress
    assert result.failure["write_started"] and desktop.saves == 0


def test_unknown_environment_blocks_all_sdk_input():
    desktop = Desktop()
    desktop.foreground = None
    result = invoke(desktop)
    assert result.status == "refused" and result.checks[0].value == "UNKNOWN"
    assert desktop.calls == []


@pytest.mark.parametrize(
    "changed",
    [
        {"app_version": "2.0"},
        {"desktop_mode": "exclusive"},
        {"foreground_assist": True},
        {"locale": "en"},
        {"platform": "linux"},
    ],
)
def test_environment_mismatch_is_not_silently_adapted(changed):
    desktop = Desktop()

    async def environment():
        return {**await Desktop.environment(desktop), **changed}

    desktop.environment = environment
    result = invoke(desktop)
    assert result.status == "refused" and result.checks[0].value == "FALSE"
    assert desktop.calls == []


def test_new_parameters_discover_new_ids_in_each_execution():
    desktop = Desktop()
    first = invoke(desktop)
    desktop.content, desktop.id, desktop.editor_ref = "old", 91, 77
    second = invoke(desktop, text="another value")
    assert first.execution_id != second.execution_id
    assert first.program == second.program
    assert second.expected_sha256 != first.expected_sha256
    assert ("element", 77) in desktop.calls and desktop.calls.count("discover") == 2
    assert desktop.content == "another value"


@pytest.mark.parametrize("cancel", [False, True])
def test_deadline_or_cancel_during_write_does_not_save(cancel):
    desktop = Desktop()

    async def scenario():
        started = asyncio.Event()

        async def blocked_write(text, **kwargs):
            desktop.content = text
            started.set()
            await asyncio.Event().wait()

        desktop.type_text = blocked_write
        task = asyncio.create_task(invoke_async(desktop, timeout_s=5 if cancel else 0.02))
        await started.wait()
        if cancel:
            task.cancel()
        return await task

    result = asyncio.run(scenario())
    assert result.status == ("cancelled" if cancel else "timeout")
    assert result.failure["effect"] == "unknown" and result.observation_required
    assert desktop.saves == 0


def test_recording_failure_after_dispatch_preserves_receipt_and_stops():
    desktop = Desktop()

    def emit(event):
        if event["kind"] == "program_step_end" and event["step"] == "replace_content":
            raise OSError("recording delivery failed")

    result = invoke(desktop, emit=emit)
    assert result.status == "stopped" and desktop.saves == 0
    assert result.steps[-1]["receipt"]["status"] == "verified"
    assert result.steps[-1]["error_type"] == "OSError"


def test_wrong_scope_parameter_or_stale_acceptance_is_rejected():
    result = invoke(Desktop())
    check = acceptance(result)
    for invalid in (
        replace(check, scope="another document"),
        replace(check, evidence={"expected_sha256": "other"}),
        replace(check, evidence={**check.evidence, "execution_id": "another-execution"}),
        replace(check, observed_at_s=result.returned_at_s - 1),
    ):
        with pytest.raises(ValueError, match="does not cover"):
            conclude(result, invalid)
    expired = replace(result, deadline_at_s=monotonic() - 1)
    assert conclude(expired, acceptance(expired)).status == "timeout"
