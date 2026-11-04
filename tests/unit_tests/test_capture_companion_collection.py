# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Only a proved sharing companion may omit its automatic second screenshot."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from computer_use.interaction.query.instances.scope import adopt
from computer_use.interaction.read.facts.capture_companion import SHARING_CONTROL_FIELDS
from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline, WindowPart, member_target
from computer_use.interaction.read.views.related import RelatedRead
from computer_use.protocol.observation.adapters import decode_driver_reply
from computer_use.session.interfaces import WindowIdentity
from computer_use.session.observation_data import observation_data
from test_d34_read_contracts import directory, setup, window
from test_dynamic_observation import parent_result, pixels, tree


def sharing_snapshot(*, ordinary=False, incomplete=False):
    recording = json.loads((
        Path(__file__).parents[1] / "fixtures/recorded/offscreen-mute-sharing-control.json"
    ).read_text())
    native = replace(decode_driver_reply(recording["narrow_raw_reply"]).observation, session="session")
    root = native.roots[0]
    button = native.nodes[root].relations["children"].targets[0]
    if ordinary:
        native = replace(native, nodes={
            **native.nodes, button: replace(native.nodes[button], name="Continue"),
        })
    if incomplete:
        from computer_use.protocol.observation import ReadIssue

        native = replace(native, issues=(*native.issues, ReadIssue(("nodes", root, "relations", "children"), "partial")))
    return normalize_facts(native, DisplayReferences("session"))


def scenario(
    monkeypatch, *, ordinary=False, incomplete=False, parent_image=True, full=False, rootless=False,
    related_read=True, view="tree",
):
    a, child = window("A"), {**window("C", parent="A"), "kind": "dialog", "modal": False}
    snapshot = sharing_snapshot(ordinary=ordinary, incomplete=incomplete)
    child["node"] = None if rootless else snapshot.native.roots[0]
    before, after = directory(a), directory(a, child)
    before["observed"] = {"id": "before-image", "startedAt": 0, "completedAt": 0.5}
    after["observed"] = {"id": "after-image", "startedAt": 3, "completedAt": 4}
    controller, app, state = setup(before)
    image = pixels(state, a) if parent_image else None
    initial = replace(parent_result(state, a, before, after), image=image)
    controller.reader.read = AsyncMock(return_value=initial)
    pipeline = InterfaceReadPipeline(controller)
    calls = []

    async def member(owner, item, view, **kwargs):
        calls.append((view, kwargs))
        if kwargs.get("fields") is not None:
            return WindowPart(member_target(state, item), tree=snapshot)
        return WindowPart(
            member_target(state, item), tree=None if rootless else tree(child, "Ordinary dialog"),
            image=pixels(state, item),
        )

    pipeline.member = AsyncMock(side_effect=member)

    async def refresh(*args, **kwargs):
        adopt(controller, app, after)
        return after

    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.refresh", AsyncMock(side_effect=refresh))
    related = RelatedRead(member_target(state, a), (WindowIdentity.of(a),)) if related_read else None
    result = asyncio.run(pipeline._collect(app, view, full, None, before, None, None, related))
    return controller, app, state, child, pipeline, result, calls


@pytest.mark.parametrize(("related_read", "view"), [(True, "tree"), (False, "both")],
                         ids=["related-read", "initial-app-both"])
def test_recorded_sharing_status_uses_one_small_tree_and_retains_parent_pixels(monkeypatch, related_read, view):
    controller, _app, state, child, _pipeline, result, calls = scenario(monkeypatch, related_read=related_read, view=view)

    assert controller.reader.read.await_args.kwargs["view"] == view
    assert len(calls) == 1 and calls[0][0] == "tree"
    assert calls[0][1]["fields"] == list(SHARING_CONTROL_FIELDS)
    assert calls[0][1]["max_nodes"] == 8 and calls[0][1]["max_depth"] == 2
    assert result.screenshot is not None and "Parent content" in result.text
    assert "Capture status (system sharing indicator, Window 1)" in result.text
    assert "WindowSharingSessionButton" in result.text
    metadata = observation_data(result)
    covered = {row["window"]: row for row in metadata["window_coverage"]}
    assert covered["C"]["requested"] == ("tree",)
    assert covered["C"]["attempted"] == ("tree",)
    assert covered["C"]["captured"] == ("tree",)
    assert covered["C"]["included"] == ("tree",)
    assert child["window"] in state.members and any(key == "C" for key in state.windows.values())
    assert controller.interfaces.windows.reference("session", "application", WindowIdentity.of(child)) is not None
    assert not metadata["partial"]


@pytest.mark.parametrize("case", ["ordinary", "incomplete", "no_parent_pixels", "explicit_full", "rootless"])
@pytest.mark.parametrize(("related_read", "view"), [(True, "tree"), (False, "both")],
                         ids=["related-read", "initial-app-both"])
def test_unknown_ordinary_and_explicit_requests_keep_normal_window_capabilities(monkeypatch, case, related_read, view):
    _controller, _app, state, child, _pipeline, result, calls = scenario(
        monkeypatch, ordinary=case == "ordinary", incomplete=case == "incomplete",
        parent_image=case != "no_parent_pixels", full=case == "explicit_full", rootless=case == "rootless",
        related_read=related_read, view=view,
    )

    assert calls[-1][0] == "both" and "fields" not in calls[-1][1]
    assert len(calls) == (2 if case in ("ordinary", "incomplete") else 1)
    assert result.screenshot is not None
    assert "Capture status (system sharing indicator" not in result.text
    coverage = {row["window"]: row for row in observation_data(result)["window_coverage"]}
    assert set(coverage["C"]["requested"]) == {"tree", "image"}
    assert "image" in coverage["C"]["included"]
    assert child["window"] in state.members


def test_explicit_fixed_window_read_is_not_folded(monkeypatch):
    child = window("C")
    snapshot = sharing_snapshot()
    child["node"] = snapshot.native.roots[0]
    catalog = directory(child)
    controller, app, state = setup(catalog)
    adopt(controller, app, catalog)
    app._anchor = child["window"]
    pipeline = InterfaceReadPipeline(controller)
    pipeline.member = AsyncMock(return_value=WindowPart(
        member_target(state, child), tree=snapshot, image=pixels(state, child),
    ))

    result = asyncio.run(pipeline._collect(app, "both", False, None, catalog, None, None, None))

    pipeline.member.assert_awaited_once()
    assert pipeline.member.call_args.args[2] == "both"
    assert "fields" not in pipeline.member.call_args.kwargs
    assert result.screenshot is not None
    assert "Capture status (system sharing indicator" not in result.text
