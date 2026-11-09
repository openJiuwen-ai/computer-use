# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A queued UI transition uses one native event, not sleeps or repeated old trees."""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.interaction.query.instances.scope import adopt
from computer_use.interaction.read.acquisition.components.support import ObservationGap
from computer_use.interaction.read.acquisition.content import has_content_progress
from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.views.changes import PostwriteChanges
from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline, WindowPart, member_target
from computer_use.interaction.read.views.related import RelatedRead
from computer_use.native.contracts import Binding
from computer_use.native.scopes import FixedWindowUnconfirmed
from computer_use.protocol.observation import decode_observation
from computer_use.session.interfaces import WindowIdentity
from computer_use.session.observation_data import observation_data
from computer_use.session.operations import OperationBudget
from test_d34_read_contracts import directory, setup, window
from test_dynamic_observation import parent_result, pixels, tree

STAMP = {"session": "session", "application": "application", "incarnation": "process", "token": "read-epoch"}


def recorded_snapshot(key, *, session=None):
    from dataclasses import replace

    value = json.loads((Path(__file__).parents[1] / "fixtures/recorded/live7-window-observations.json").read_text())
    native = decode_observation(value[key])
    if session is not None:
        # Bind the real recorded graph to the fake port's session. All native
        # node IDs, fields, relationships, issues and acquisition clocks remain.
        native = replace(native, session=session)
    return normalize_facts(native, DisplayReferences(native.session))


@pytest.mark.parametrize("event", [False, True])
@pytest.mark.parametrize("before_key", [None, "before_full", "before_budget"])
def test_queued_new_window_arrives_without_a_second_old_window_read(monkeypatch, event, before_key):
    from dataclasses import replace

    a, form = window("A"), {**window("Form", parent="A"), "modal": True}
    after_tree = recorded_snapshot("after", session="session") if before_key is not None else None
    if after_tree is not None:
        a["node"] = after_tree.native.roots[0]
    before, after = directory(a), directory(a, form, focused="Form")
    before["appChange"] = STAMP
    controller, app, state = setup(before)
    initial = parent_result(state, a, before, before)
    before_tree = initial.tree
    if before_key is not None:
        before_tree = recorded_snapshot(before_key, session="session")
        initial = replace(initial, tree=after_tree)
    controller.reader.read = AsyncMock(return_value=initial)
    changes = PostwriteChanges({**STAMP, "token": "before-input"}, before_tree)
    wait = AsyncMock(return_value=("AXWindowCreated",) if event else ())
    monkeypatch.setattr(PostwriteChanges, "wait", wait)
    pipeline = InterfaceReadPipeline(controller)
    pipeline.member = AsyncMock(
        return_value=WindowPart(
            member_target(state, form),
            tree=tree(form, "Meeting title"),
        )
    )

    async def recheck(*args, **kwargs):
        adopt(controller, app, after)
        return after

    refresh = AsyncMock(side_effect=recheck)
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.refresh", refresh)
    related = RelatedRead(member_target(state, a), (WindowIdentity.of(a),), changes=changes)
    result = asyncio.run(pipeline._collect(app, "tree", False, None, before, None, None, related))
    assert controller.reader.read.await_count == 1
    assert wait.await_count == 1
    if event:
        assert "Meeting title" in result.text and result.screenshot is None
        assert pipeline.member.await_count == 1
        assert pipeline.member.call_args.args[1]["window"] == "Form"
        assert pipeline.member.call_args.args[2] == "tree"
        coverage = {item["window"]: item for item in observation_data(result)["window_coverage"]}
        assert coverage["Form"]["included"] == ("tree",)
    else:
        assert ("Parent content" if before_key is None else "Open schedule") in result.text
        pipeline.member.assert_not_awaited()
        refresh.assert_not_awaited()


@pytest.mark.parametrize("reason", ["event", "timeout", "unavailable"])
def test_wait_uses_read_epoch_and_original_budget_with_collection_reserve(monkeypatch, reason):
    a = window("A")
    controller, _app, _state = setup(directory(a))
    budget = OperationBudget(clock=lambda: 100)
    monkeypatch.setattr("computer_use.interaction.read.views.changes.current_operation_budget", lambda: budget)

    @asynccontextmanager
    async def lease(target, remaining):
        assert target is None
        yield SimpleNamespace(token="read", target=Binding("session", None, None, None), remaining=remaining)

    controller.port.calls.operation = lease
    controller.port.app_changes = AsyncMock(
        return_value={
            **STAMP,
            "token": "event-epoch",
            "reason": reason,
            "events": ["AXWindowCreated"] if reason == "event" else [],
        }
    )
    changes = PostwriteChanges({**STAMP, "token": "before-input"})
    result = asyncio.run(changes.wait(controller, {"appChange": STAMP}, collection_ms=100))
    assert result == (("AXWindowCreated",) if reason == "event" else ())
    call = controller.port.app_changes.call_args
    assert call.args[:3] == ("read", Binding("session", None, None, None), "application")
    assert call.kwargs == {"after": "read-epoch", "delay_ms": 400}
    assert controller.port.calls._remaining is None and not controller.port.calls.busy


@pytest.mark.parametrize("remaining,collection", [(0, 10), (40, 40), (80, 100)])
def test_no_wait_when_original_budget_cannot_reserve_useful_collection(monkeypatch, remaining, collection):
    controller = SimpleNamespace(port=SimpleNamespace(app_changes=AsyncMock()))
    monkeypatch.setattr(
        "computer_use.interaction.read.views.changes.current_operation_budget",
        lambda: SimpleNamespace(target_available_ms=lambda: remaining, operation_id="test"),
    )
    result = asyncio.run(PostwriteChanges(STAMP).wait(controller, {"appChange": STAMP}, collection_ms=collection))
    assert result == ()
    controller.port.app_changes.assert_not_awaited()


def test_unlimited_completion_does_not_extend_optional_notification_wait(monkeypatch):
    clock = [100.0]
    budget = OperationBudget(clock=lambda: clock[0])
    clock[0] += 1.1
    assert budget.available_ms() is None and budget.target_available_ms() == 0
    controller = SimpleNamespace(port=SimpleNamespace(app_changes=AsyncMock()))
    monkeypatch.setattr("computer_use.interaction.read.views.changes.current_operation_budget", lambda: budget)
    assert asyncio.run(PostwriteChanges(STAMP).wait(controller, {"appChange": STAMP}, collection_ms=50)) == ()
    controller.port.app_changes.assert_not_awaited()


def test_captured_prior_content_without_an_epoch_never_invents_a_native_wait(monkeypatch):
    a = window("A")
    before = tree(a, "Existing body")
    changes = PostwriteChanges(None, before)
    controller = SimpleNamespace(port=SimpleNamespace(app_changes=AsyncMock()))
    monkeypatch.setattr(
        "computer_use.interaction.read.views.changes.current_operation_budget", lambda: OperationBudget(),
    )
    assert asyncio.run(changes.wait(controller, {"appChange": STAMP}, collection_ms=50)) == ()
    assert changes.before_tree is before and not changes.input_sampled
    controller.port.app_changes.assert_not_awaited()


def test_changed_content_and_capture_clocks_are_distinguished():
    a = window("A")
    before, unchanged, changed = tree(a, "Before"), tree(a, "Before"), tree(a, "After")
    assert not has_content_progress(before, unchanged)
    assert has_content_progress(before, changed)


@pytest.mark.parametrize("before_key", ["before_full", "before_budget"])
def test_recorded_live7_coverage_and_anonymous_container_do_not_discharge_event_debt(before_key):
    before, after = recorded_snapshot(before_key), recorded_snapshot("after")
    assert len(before.native.nodes) == (8 if before_key == "before_full" else 1)
    assert len(after.native.nodes) == 9
    assert before.native.interval_ns != after.native.interval_ns
    target = Binding(after.native.session, "instance", "window", "surface", after.native.roots[0])
    part = WindowPart(target, tree=after)
    changes = PostwriteChanges({**STAMP, "session": target.session}, before)
    assert not has_content_progress(before, after)
    assert changes.needs_observation(part)
    assert part.target is target  # Advisory observation never merges or rebinds identity.


@pytest.mark.parametrize("read_key", ["first_read", "last_read"])
def test_recorded_live8_covered_input_generation_does_not_wait_for_a_second_event(monkeypatch, read_key):
    value = json.loads((Path(__file__).parents[1] / "fixtures/recorded/live8-app-change-epochs.json").read_text())
    controller = SimpleNamespace(port=SimpleNamespace(app_changes=AsyncMock()))
    monkeypatch.setattr(
        "computer_use.interaction.read.views.changes.current_operation_budget",
        lambda: OperationBudget(),
    )
    # The captured read is later than the effective requested-input baseline.
    changes = PostwriteChanges(value["begin"], input_sampled=True)
    assert value["begin"]["token"].endswith(":23")
    assert value[read_key]["token"].endswith(":25" if read_key == "first_read" else ":27")
    result = asyncio.run(changes.wait(controller, {"appChange": value[read_key]}, collection_ms=100))
    assert result == ()
    controller.port.app_changes.assert_not_awaited()


def test_preparation_only_epoch_does_not_discharge_unconfirmed_requested_input(monkeypatch):
    a = window("A")
    controller, _app, _state = setup(directory(a))
    monkeypatch.setattr(
        "computer_use.interaction.read.views.changes.current_operation_budget",
        lambda: OperationBudget(),
    )

    @asynccontextmanager
    async def lease(target, remaining):
        yield SimpleNamespace(token="read", target=Binding("session", None, None, None), remaining=remaining)

    controller.port.calls.operation = lease
    controller.port.app_changes = AsyncMock(return_value={**STAMP, "reason": "timeout", "events": []})
    changes = PostwriteChanges({**STAMP, "token": "preparation-start"}, input_sampled=False)
    assert asyncio.run(changes.wait(controller, {"appChange": STAMP}, collection_ms=100)) == ()
    controller.port.app_changes.assert_awaited_once()


def test_reconciliation_epoch_cannot_claim_that_an_earlier_capture_covered_input(monkeypatch):
    a = window("A")
    before, reconciled = directory(a), directory(a)
    before["appChange"] = {**STAMP, "token": "input-epoch"}
    reconciled["appChange"] = {**STAMP, "token": "during-capture-epoch"}
    controller, app, state = setup(before)
    initial = parent_result(state, a, before, reconciled)
    controller.reader.read = AsyncMock(return_value=initial)
    changes = PostwriteChanges(before["appChange"], initial.tree, input_sampled=True)
    wait = AsyncMock(return_value=())
    monkeypatch.setattr(PostwriteChanges, "wait", wait)
    related = RelatedRead(member_target(state, a), (WindowIdentity.of(a),), changes=changes)

    result = asyncio.run(
        InterfaceReadPipeline(controller)._collect(
            app,
            "tree",
            False,
            None,
            before,
            None,
            None,
            related,
        )
    )

    assert "Parent content" in result.text
    assert wait.await_count == 1
    # AppReader's reconciliation is later than its pixels/tree. Wait from the
    # actual collection start, so an event during capture is not silently lost.
    assert wait.call_args.args[1]["appChange"]["token"] == "input-epoch"


def test_missing_original_capture_can_receive_window_created_and_deliver_new_form(monkeypatch):
    a, form = window("A"), window("Form")
    before, missing, after = directory(a), directory(), directory(form, focused="Form")
    before["appChange"] = STAMP
    missing["appChange"] = STAMP
    after["appChange"] = {**STAMP, "token": "new-form-epoch"}
    controller, app, state = setup(before)
    controller.reader.read = AsyncMock(
        side_effect=FixedWindowUnconfirmed(
            member_target(state, a),
            directory=missing,
        )
    )
    budget = OperationBudget(clock=lambda: 100)
    monkeypatch.setattr("computer_use.interaction.read.views.changes.current_operation_budget", lambda: budget)

    @asynccontextmanager
    async def lease(target, remaining):
        assert target is None  # App observation does not borrow the vanished Window's input scope.
        yield SimpleNamespace(token="read", target=Binding("session", None, None, None), remaining=remaining)

    controller.port.calls.operation = lease
    controller.port.app_changes = AsyncMock(
        return_value={
            **STAMP,
            "token": "new-form-epoch",
            "reason": "event",
            "events": ["AXWindowCreated"],
        }
    )
    pipeline = InterfaceReadPipeline(controller)
    pipeline.member = AsyncMock(
        return_value=WindowPart(
            member_target(state, form),
            tree=tree(form, "Meeting title"),
        )
    )

    async def recheck(*args, **kwargs):
        adopt(controller, app, after)
        return after

    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.refresh", AsyncMock(side_effect=recheck))
    related = RelatedRead(
        member_target(state, a),
        (WindowIdentity.of(a),),
        changes=PostwriteChanges(STAMP, input_sampled=True),
    )

    result = asyncio.run(pipeline._collect(app, "tree", False, None, before, None, None, related))

    controller.reader.read.assert_awaited_once()
    controller.port.app_changes.assert_awaited_once()
    assert pipeline.member.await_count == 1
    assert pipeline.member.call_args.args[1]["window"] == "Form"
    assert pipeline.member.call_args.args[2] == "tree"
    assert "Meeting title" in result.text and result.screenshot is None
    coverage = {item["window"]: item for item in observation_data(result)["window_coverage"]}
    assert coverage["Form"]["included"] == ("tree",)
    assert coverage["A"]["captured"] == ()


def test_later_unconfirmed_popup_keeps_its_capture_and_observes_the_current_parent(monkeypatch):
    from dataclasses import replace

    a, popup = window("A"), window("Popup")
    before, later = directory(a, popup), directory(a)
    controller, app, state = setup(before)
    initial = replace(
        parent_result(state, popup, before, later),
        scope_confirmed=False,
        tree=tree(popup, "Captured hour choices"),
        image=pixels(state, popup),
        gaps=(
            ObservationGap("scope", "observation_incomplete", "Captured scope is unconfirmed in the later directory"),
        ),
    )
    controller.reader.read = AsyncMock(return_value=initial)
    pipeline = InterfaceReadPipeline(controller)
    parent = tree(a, "Current parent content")
    pipeline.member = AsyncMock(return_value=WindowPart(member_target(state, a), tree=parent))
    monkeypatch.setattr(
        "computer_use.interaction.read.views.pipeline.refresh", AsyncMock(return_value=later),
    )
    related = RelatedRead(member_target(state, a), (WindowIdentity.of(a),))

    result = asyncio.run(pipeline._collect(app, "both", False, None, before, None, None, related))

    assert "Current parent content" in result.text and result.screenshot is not None
    assert "outside current read scope, tree not expanded" in result.text
    assert "scope is unconfirmed" in result.text
    assert initial.tree.native.interval_ns == (1, 2)
    assert app._context == "A"
    assert state.directory is later
    controller.reader.read.assert_awaited_once()
    pipeline.member.assert_awaited_once()
    assert pipeline.member.call_args.args[1]["window"] == "A"
    assert pipeline.member.call_args.args[2] == "both"
    metadata = observation_data(result)
    coverage = {item["window"]: item for item in metadata["window_coverage"]}
    assert metadata["partial"] and coverage["Popup"]["lifecycle"] == "unknown"
    assert coverage["Popup"]["captured"] == ("tree", "image")
    assert coverage["Popup"]["included"] == ("image",)
    assert coverage["A"]["included"] == ("tree",)
    assert any(source is initial.tree for _, source in metadata["captured_trees"])
    assert "Window 1:" in result.text  # Captured popup receives its own heading, not the parent's.
