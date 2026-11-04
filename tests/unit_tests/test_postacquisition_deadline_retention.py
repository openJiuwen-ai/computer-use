# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A presentation cutoff cannot erase acquired source facts or invent public refs."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from computer_use.interaction.read.app import NativeAppReader
from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline
from computer_use.native.scopes import WorkingWindow
from computer_use.session import images, operations
from computer_use.session.events import set_observation_handler
from computer_use.session.observation_data import observation_data
from test_d34_read_contracts import directory, setup, window
from test_dynamic_observation import deliver_through_runtime, member_target, pixels
from test_observation_budget_delivery import Port


async def expired_app_read(monkeypatch, *, with_image=True, scope_managed=True, cancelled=False):
    """Real acquisition returns, then the original operation CPU budget expires."""
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr("computer_use.native.lifecycle.time.monotonic", lambda: clock.now)
    port = Port(clock, expired_at="none")
    reader = NativeAppReader(port)
    item = window("A")
    current = directory(item)
    controller, app, state = setup(current)
    image = pixels(state, item)
    token = operations._budget.set(operations.OperationBudget(
        1000, clock=lambda: clock.now, cancelled=lambda: cancelled and clock.now > 100,
    ))

    async def capture(read, **kwargs):
        clock.now += 1.01
        if not with_image:
            raise TimeoutError("Native capture returned no pixels")
        return image

    reader.images.collect = AsyncMock(side_effect=capture)
    # A suffix admission must fail on the existing deadline, without replacing
    # the principal's successfully acquired source tree or returned image.
    reader._discover = AsyncMock(side_effect=lambda *args, **kwargs: operations.current_operation_budget().remaining())
    selected = WorkingWindow("application", "Fixture", replace(member_target(state, item), node=None))
    try:
        observed = await reader.read(
            selected, view="both", overview=False, include_menus=False,
            source_directory=current, scope_managed=scope_managed,
        )
        return observed, image, current, controller, app, state, port
    finally:
        operations._budget.reset(token)


@pytest.mark.parametrize("scope_managed", [False, True])
@pytest.mark.parametrize("with_image", [False, True])
def test_expired_numbering_retains_exact_raw_source_without_public_aliases(monkeypatch, scope_managed, with_image):
    observed, image, current, _controller, _app, _state, port = asyncio.run(expired_app_read(
        monkeypatch, with_image=with_image, scope_managed=scope_managed,
    ))

    assert observed.tree is not None and observed.tree.native.id == "read"
    assert observed.tree.native.interval_ns == (1, 2)
    assert any(node.name == "Search" for node in observed.tree.native.nodes.values())
    assert observed.target.root_window == "A" and observed.target.surface == "surface:A"
    assert observed.source_directory is current and observed.directory is current
    assert observed.image is (image if with_image else None)
    assert not observed.snapshots_numbered and not observed.refs
    assert "Search" not in observed.text
    assert any(gap.component == "presentation" for gap in observed.gaps)
    assert len(port.queries) == 1 and len(port.opened) == 1 and port.finished == ["1"]
    assert not port.calls.retired and not port.calls.busy


@pytest.mark.parametrize("with_image", [False, True])
def test_initial_app_read_publishes_available_partial_and_never_acknowledges_raw_tree(monkeypatch, with_image):
    monkeypatch.setattr(images, "_delivery_required", True)
    observed, image, current, controller, app, state, _port = asyncio.run(expired_app_read(
        monkeypatch, with_image=with_image,
    ))
    controller.reader.read = AsyncMock(return_value=observed)
    pipeline = InterfaceReadPipeline(controller)
    pipeline.preparation.run = Mock(side_effect=AssertionError("Unnumbered facts cannot be prepared as public refs"))
    pipeline.member = AsyncMock(side_effect=AssertionError("An expired suffix cannot begin another component"))

    events = []
    set_observation_handler(events.append)
    try:
        result = asyncio.run(pipeline._collect(app, "both", False, None, current, None, None, None))
    finally:
        set_observation_handler(None)
    metadata = observation_data(result)
    assert metadata["partial"] and not metadata["refs"] and not metadata["roots"]
    assert metadata["content_available"] is with_image
    assert "Search" not in result.text and "presentation budget exhausted" in result.text
    record = next(row for row in metadata["window_coverage"] if row["window"] == "A")
    assert "tree" in record["captured"] and "tree" not in record["included"]
    assert record["treeCollection"] == {"session": "session", "observation": "read", "intervalNs": (1, 2)}
    assert not state.refs and controller.state(state.owners["A"]).query_snapshot is observed.tree
    assert (result.screenshot is not None) is with_image
    assert not pipeline.preparation.run.called and not pipeline.member.called
    if with_image:
        assert result.screenshot is image.image
        delivered = deliver_through_runtime(event["observation"] for event in events)
        assert any(block["type"] == "image" for block in delivered["content"]), delivered
        assert "tree" not in observation_data(result)["window_coverage"][0]["delivered"]


def test_explicit_cancellation_after_capture_still_propagates(monkeypatch):
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(expired_app_read(monkeypatch, cancelled=True))


def test_fixed_window_numbering_cutoff_keeps_raw_tree_and_independent_pixels(monkeypatch):
    from computer_use.interaction.query.instances.scope import adopt
    from computer_use.interaction.read.views.pipeline import remember
    from computer_use.session.operations import OperationDeadlineExceeded
    from test_dynamic_observation import tree

    item = window("A")
    current = directory(item)
    controller, app, state = setup(current)
    adopt(controller, app, current)
    snapshot, image = tree(item, "Acquired button"), pixels(state, item)
    controller.reader.reader.read = AsyncMock(return_value=snapshot)
    controller.reader.number_snapshots = Mock(side_effect=OperationDeadlineExceeded("Original deadline"))
    monkeypatch.setattr(
        "computer_use.interaction.read.views.pipeline.FixedWindowImagePipeline.arun",
        AsyncMock(return_value=SimpleNamespace(image=image, gap=None)),
    )
    part = asyncio.run(InterfaceReadPipeline(controller).member(app, item, "both", component_budget_ms=2000))
    assert part.tree is snapshot and part.image is image and not part.snapshots_numbered
    assert any(gap.component == "presentation" for gap in part.gaps)
    remember(controller, app, part)
    assert not state.refs and controller.state(state.owners["A"]).query_snapshot is snapshot


def test_unrelated_numbering_failure_is_not_converted_to_a_deadline_partial(monkeypatch):
    from computer_use.interaction.read.acquisition.model import AcquiredObservation, Components
    from computer_use.interaction.read.pipeline import _PrepareCapturedObservation
    from computer_use.pipeline.core import Control

    node = _PrepareCapturedObservation(SimpleNamespace())
    node.presentation.run = Mock(side_effect=ValueError("Invalid presentation contract"))
    acquired = AcquiredObservation(None, None, None, Components(None), (), False)
    with pytest.raises(ValueError, match="Invalid presentation contract"):
        node.run(acquired, Control())
