# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Fixed-window image refresh preserves exact target, admission and one budget."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.images.fixed import FixedWindowImagePipeline
from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline
from computer_use.native.contracts import Binding, Cleanup, OpenedOperation
from computer_use.native.lifecycle import CallLifecycle

TARGET = Binding("session", "instance:A", "A", "surface:A", "AX-root")
IMAGE_TARGET = replace(TARGET, node=None)


def failure(code="stale_target", effect="none"):
    return ComputerUseError(
        dict(code=code, effect=effect, target="Window A", detail="Window geometry changed", next="observe again")
    )


class Port:
    def __init__(self):
        self.connection = SimpleNamespace(session="session", session_identity=(1, "session"))
        self.calls = CallLifecycle(self)
        self.opened, self.finished = [], []
        self.begin_error, self.other_target = None, None

    async def begin_read(self, target, timeout):
        self.calls.check_begin("read", "read", target)
        self.opened.append((target, timeout))
        if self.begin_error is not None:
            raise self.begin_error
        return OpenedOperation(str(len(self.opened)), self.other_target or target)

    async def finish(self, token):
        self.finished.append(token)
        return Cleanup.COMPLETE


def setup(monkeypatch, outcomes, *, wait_seconds=0.08, capture_seconds=0.01):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr("computer_use.native.lifecycle.time.monotonic", lambda: clock.now)
    port, leases = Port(), []
    values = iter(outcomes)

    async def capture(read):
        leases.append(read)
        read.remaining()
        clock.now += capture_seconds
        value = next(values)
        if isinstance(value, Exception):
            raise value
        return value

    async def wait():
        clock.now += wait_seconds

    sleeper = AsyncMock(side_effect=wait)
    monkeypatch.setattr("computer_use.interaction.read.acquisition.images.fixed.settle_capture_geometry", sleeper)
    images = SimpleNamespace(collect=AsyncMock(side_effect=capture))
    return port, images, FixedWindowImagePipeline(port, images), leases, sleeper, clock


def test_changed_geometry_refreshes_once_without_changing_binding_or_budget(monkeypatch):
    image = object()
    port, images, pipeline, leases, wait, _ = setup(monkeypatch, [failure(), image])
    result = asyncio.run(pipeline.arun(TARGET))
    assert result.image is image and result.gap is None and result.attempt == 1
    assert [target for target, _ in port.opened] == [IMAGE_TARGET, IMAGE_TARGET]
    assert leases[0].remaining is leases[1].remaining
    assert port.opened[1][1] < port.opened[0][1]
    assert port.finished == ["1", "2"] and images.collect.await_count == 2
    wait.assert_awaited_once()
    assert not port.calls.busy and not port.calls.retired


def test_permanent_geometry_change_stops_at_existing_two_attempt_bound(monkeypatch):
    port, images, pipeline, _, wait, _ = setup(monkeypatch, [failure(), failure(), object()])
    result = asyncio.run(pipeline.arun(TARGET))
    assert result.image is None and result.gap.code == "stale_target" and result.attempt == 1
    assert len(port.opened) == 2 and images.collect.await_count == 2
    wait.assert_awaited_once()


@pytest.mark.parametrize(
    "code", ["observation_incomplete", "execution_failed", "snapshot_expired", "unsupported_operation"]
)
def test_other_missing_image_reasons_do_not_gain_geometry_refresh(monkeypatch, code):
    port, images, pipeline, _, wait, _ = setup(monkeypatch, [failure(code)])
    result = asyncio.run(pipeline.arun(TARGET))
    assert result.gap.code == code and len(port.opened) == 1
    wait.assert_not_awaited()


def test_dead_target_admission_is_not_retried_or_redirected(monkeypatch):
    port, images, pipeline, _, wait, _ = setup(monkeypatch, [])
    port.begin_error = failure()
    result = asyncio.run(pipeline.arun(TARGET))
    assert result.target == IMAGE_TARGET and result.gap.code == "stale_target"
    assert len(port.opened) == 1 and not result.admitted
    images.collect.assert_not_awaited()
    wait.assert_not_awaited()


def test_changed_native_identity_is_rejected_before_any_pixel_collection(monkeypatch):
    port, images, pipeline, _, wait, _ = setup(monkeypatch, [])
    port.other_target = replace(IMAGE_TARGET, root_window="B", surface="surface:B")
    with pytest.raises(ValueError, match="changed an already bound target"):
        asyncio.run(pipeline.arun(TARGET))
    assert len(port.opened) == 1 and port.finished == ["1"]
    images.collect.assert_not_awaited()
    wait.assert_not_awaited()


def test_capture_session_change_or_uncertain_effect_never_enters_refresh(monkeypatch):
    for changed in (False, True):
        port, images, pipeline, _, wait, _ = setup(monkeypatch, [])

        async def capture(_):
            if changed:
                port.connection.session_identity = (2, "session")
            raise failure(effect="none" if changed else "possible")

        images.collect.side_effect = capture
        with pytest.raises(ComputerUseError):
            asyncio.run(pipeline.arun(TARGET))
        assert len(port.opened) == 1
        wait.assert_not_awaited()


def test_small_remaining_budget_returns_gap_without_refresh(monkeypatch):
    port, _, pipeline, _, wait, _ = setup(monkeypatch, [failure()])

    async def run():
        with port.calls.limit(300):
            return await pipeline.arun(TARGET)

    result = asyncio.run(run())
    assert result.gap.code == "stale_target" and len(port.opened) == 1
    assert port.opened[0][1] <= 300
    wait.assert_not_awaited()


def test_deadline_expiring_during_settle_does_not_open_another_native_operation(monkeypatch):
    port, _, pipeline, _, wait, _ = setup(monkeypatch, [failure(), object()], wait_seconds=0.5)

    async def run():
        with port.calls.limit(450):
            return await pipeline.arun(TARGET)

    result = asyncio.run(run())
    assert result.expired and result.image is None and result.gap.code == "observation_incomplete"
    assert "budget exhausted" in result.gap.detail
    assert len(port.opened) == 1 and port.finished == ["1"]
    wait.assert_awaited_once()
    assert not port.calls.busy and not port.calls.retired


def test_public_window_member_uses_bounded_fixed_image_branch(monkeypatch):
    image = object()
    port, images, _, _, wait, _ = setup(monkeypatch, [failure(), image])
    controller = SimpleNamespace(port=port, reader=SimpleNamespace(images=images))
    ui = SimpleNamespace(_instance=SimpleNamespace(directory={"session": "session"}))
    item = dict(instance=TARGET.instance, window="A", surface=TARGET.surface, node=TARGET.node)
    result = asyncio.run(InterfaceReadPipeline(controller).member(ui, item, "image"))
    assert result.target == TARGET and result.image is image and not result.gaps
    assert len(port.opened) == 2
    wait.assert_awaited_once()


def test_member_preserves_successful_tree_when_image_budget_expires(monkeypatch):
    port, images, _, _, _, _ = setup(monkeypatch, [TimeoutError("pixel deadline")])
    tree = object()
    reader = SimpleNamespace(
        images=images, reader=SimpleNamespace(read=AsyncMock(return_value=tree)), number_snapshots=lambda values: values
    )
    controller = SimpleNamespace(port=port, reader=reader)
    ui = SimpleNamespace(_instance=SimpleNamespace(directory={"session": "session"}))
    item = dict(instance=TARGET.instance, window="A", surface=TARGET.surface, node=TARGET.node)
    result = asyncio.run(InterfaceReadPipeline(controller).member(ui, item, "both"))
    assert result.tree is tree and result.image is None
    assert len(result.gaps) == 1 and "pixel deadline" in result.gaps[0].detail
    assert len(port.opened) == 1 and not port.calls.retired


def test_timeout_with_session_change_or_uncertain_cleanup_keeps_original_failure(monkeypatch):
    for changed in (True, False):
        port, images, pipeline, _, wait, _ = setup(monkeypatch, [])
        timeout = TimeoutError("original read deadline")

        async def capture(_):
            if changed:
                port.connection.session_identity = (2, "session")
            raise timeout

        images.collect.side_effect = capture
        if not changed:
            port.finish = AsyncMock(return_value=Cleanup.UNKNOWN)
        with pytest.raises(TimeoutError) as caught:
            asyncio.run(pipeline.arun(TARGET))
        assert caught.value is timeout and len(port.opened) == 1
        wait.assert_not_awaited()
        if not changed:
            assert port.calls.retired and "cleanup unconfirmed" in " ".join(timeout.__notes__)


def test_parent_control_metadata_and_cancellation_are_preserved(monkeypatch):
    from computer_use.pipeline import Control

    port, _, pipeline, _, _, _ = setup(monkeypatch, [object()])
    events = []
    incoming = Control(
        remaining=lambda: 600,
        path=("ParentRead",),
        call_id="parent-call",
        diagnostic=events.append,
        cancelled=lambda: False,
        branch="original",
    )
    result = asyncio.run(pipeline.arun(TARGET, incoming))
    assert result.image is not None and port.opened[0][1] <= 600
    assert events and all(event.path[0] == "ParentRead" and event.call_id == "parent-call" for event in events)
    assert all(event.branch == "original" for event in events)
    port, _, pipeline, _, _, _ = setup(monkeypatch, [object()])
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(pipeline.arun(TARGET, replace(incoming, cancelled=lambda: True)))
    assert not port.opened


def test_existing_claim_is_borrowed_without_nested_or_reset_budget(monkeypatch):
    from computer_use.pipeline import Control

    port, _, pipeline, leases, _, _ = setup(monkeypatch, [failure(), object()])

    async def run():
        with port.calls.claim(700) as remaining:
            result = await pipeline.arun(TARGET, Control(remaining=remaining, path=("Outer",), call_id="outer"))
            assert all(lease.remaining is remaining for lease in leases)
            assert port.calls.busy
            return result

    assert asyncio.run(run()).image is not None
    assert len(port.opened) == 2 and all(timeout <= 700 for _, timeout in port.opened)
    assert not port.calls.busy
