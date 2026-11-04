# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The live16 popup source remains historical; the App observes its current scope."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from computer_use.interaction.read.acquisition.components.support import ObservationGap
from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.interaction.read.model import AppObservation
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline, WindowPart, member_target
from computer_use.interaction.read.views.related import RelatedRead
from computer_use.native.scopes import WorkingWindow
from computer_use.protocol.observation.adapters import decode_driver_reply
from computer_use.session import operations
from computer_use.session.events import observation_delivered
from computer_use.session.interfaces import WindowIdentity, WindowLifetime
from computer_use.session.observation_data import observation_data
from test_d34_read_contracts import directory, setup, window
from test_dynamic_observation import parent_result, tree


def recorded_scope():
    recording = json.loads((
        Path(__file__).parents[1] / "fixtures/recorded/live16-popup-return-scope.json"
    ).read_text())

    def bind_directory(source):
        # Only bind the recorded App/session to the fake port. Every window,
        # node, lifecycle item, relationship and original timestamp stays exact.
        return {
            **source, "session": "session", "application": "application",
            "appChange": {**source["appChange"], "session": "session", "application": "application"},
        }

    first = bind_directory(recording["first_directory"])
    later = bind_directory(recording["later_directory"])
    native = replace(decode_driver_reply(recording["postwrite_popup"]).observation, session="session")
    return first, later, normalize_facts(native, DisplayReferences("session"))


def test_recorded_disappearing_popup_retains_all_facts_and_recovers_current_tree_before_presentation(monkeypatch):
    first, later, captured = recorded_scope()
    controller, app, state = setup(first)
    popup = next(item for item in first["surfaces"] if item["window"] == "4")
    parent = next(item for item in later["surfaces"] if item["window"] == "2")
    assert popup["parent"] is None and first["focusedWindow"] == later["focusedWindow"] == "2"
    assert len(captured.native.nodes) == 179 and "4" not in {item["window"] for item in later["surfaces"]}
    assert all(item["window"] != "4" for item in later["endedWindows"])
    target = member_target(state, popup)
    initial = AppObservation(
        WorkingWindow("application", "Fixture", replace(target, node=None)), target, None,
        first, later, captured, None, DisplayDocument.join(()), {}, {},
        (ObservationGap("scope", "observation_incomplete", "Captured window unconfirmed in later directory"),), False,
    )
    controller.reader.read = AsyncMock(return_value=initial)
    # The provider stub proves scheduling/retention. This small fresh result is
    # not presented as a recorded post-choose native tree or UI acceptance.
    current = tree(parent, "Current form controls")
    pipeline = InterfaceReadPipeline(controller)
    original_prepare = pipeline.preparation.run
    pipeline.preparation.run = Mock(wraps=original_prepare)
    clock = [100.0]
    budget = operations.OperationBudget(2000, clock=lambda: clock[0])

    async def current_member(owner, item, view, **kwargs):
        assert item is parent and view == "tree"
        assert operations.current_operation_budget() is budget
        assert 890 <= budget.remaining() <= 910
        clock[0] += 0.140
        return WindowPart(member_target(state, item), tree=current)

    async def principal(*args, **kwargs):
        clock[0] += 1.1
        return initial

    controller.reader.read.side_effect = principal
    pipeline.member = AsyncMock(side_effect=current_member)
    related = RelatedRead(target, tuple(WindowIdentity.of(item) for item in first["surfaces"]))

    async def run():
        token = operations._budget.set(budget)
        try:
            return await pipeline._collect(app, "tree", False, None, first, None, None, related)
        finally:
            operations._budget.reset(token)

    observed = asyncio.run(run())
    assert "Current form controls" in observed.text
    assert "outside current read scope, tree not expanded" in observed.text
    assert app._context == "2" and state.anchor.root.root_window == "2"
    assert pipeline.member.await_count == 1 and controller.reader.read.await_count == 1
    assert pipeline.preparation.run.call_count == 1
    assert pipeline.preparation.run.call_args.args[0].snapshot is current
    metadata = observation_data(observed)
    assert any(source is captured for _, source in metadata["captured_trees"])
    assert captured.native.interval_ns == initial.tree.native.interval_ns
    coverage = {item["window"]: item for item in metadata["window_coverage"]}
    assert coverage["4"]["lifecycle"] == "unknown" and coverage["4"]["captured"] == ("tree",)
    assert coverage["4"]["included"] == () and coverage["4"]["treeCollection"]["intervalNs"] == captured.native.interval_ns
    assert coverage["2"]["requested"] == coverage["2"]["included"] == ("tree",)
    assert state.evidence[WindowIdentity.of(popup)].lifetime is not WindowLifetime.RETIRED
    assert budget.elapsed_ms == pytest.approx(1240)
    observation_delivered(observed)
    assert state.lookup_context.window == WindowIdentity.of(parent)
    assert "tree" not in observation_data(observed)["window_coverage"][0]["delivered"]


def test_original_popup_already_absent_does_not_force_a_new_old_popup_capture(monkeypatch):
    first, later, _captured = recorded_scope()
    controller, app, state = setup(later)
    popup = next(item for item in first["surfaces"] if item["window"] == "4")
    parent = next(item for item in later["surfaces"] if item["window"] == "2")
    # InterfaceReadPipeline.run resolves the current App display selection
    # before _collect. The generic fixture helper instead selects row zero.
    state.anchor = controller.reader._selection("application", "Fixture", later, "2")
    current = replace(parent_result(state, parent, later, later), scope_confirmed=True)
    controller.reader.read = AsyncMock(return_value=current)
    pipeline = InterfaceReadPipeline(controller)
    async def member(owner, item, view, **kwargs):
        assert item["window"] == "3" and view == "tree"
        return WindowPart(member_target(state, item), tree=tree(item, "Sharing status"))

    pipeline.member = AsyncMock(side_effect=member)
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.refresh", AsyncMock(return_value=later))
    related = RelatedRead(member_target(state, popup), tuple(WindowIdentity.of(item) for item in first["surfaces"]))
    result = asyncio.run(pipeline._collect(app, "tree", False, None, later, None, None, related))
    assert "Parent content" in result.text and app._context == "2"
    assert controller.reader.read.call_args.args[0].root.root_window == "2"
    assert controller.reader.read.call_args.kwargs["view"] == "tree"
    assert pipeline.member.await_count == 1  # Existing attached helper; never old popup4 or a forced PNG.


def test_historical_capture_is_kept_when_current_scope_is_ambiguous(monkeypatch):
    a, b, popup = window("A"), window("B"), window("Popup")
    first, later = directory(a, b, popup), directory(a, b)
    controller, app, state = setup(first)
    initial = replace(parent_result(state, popup, first, later), tree=tree(popup, "Retained historical choices"))
    controller.reader.read = AsyncMock(return_value=initial)
    pipeline = InterfaceReadPipeline(controller)
    pipeline.member = AsyncMock(side_effect=AssertionError("Ambiguous current scope must not choose a random window"))
    related = RelatedRead(member_target(state, popup), tuple(WindowIdentity.of(item) for item in first["surfaces"]))
    result = asyncio.run(pipeline._collect(app, "tree", False, None, first, None, None, related))
    assert "Retained historical choices" in result.text and app._context is None
    pipeline.member.assert_not_awaited()
