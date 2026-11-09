"""A completed empty app catalogue supersedes empty failed window attempts, not native lifetimes."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from computer_use.interaction.query.instances.scope import adopt
from computer_use.interaction.read.acquisition.components.support import ObservationGap
from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline, WindowPart, member_target
from computer_use.interaction.read.views.related import RelatedRead
from computer_use.session.interfaces import WindowIdentity, WindowLifetime
from computer_use.session.observation_data import observation_data
from test_d34_read_contracts import directory, setup, window
from test_dynamic_observation import deliver_through_runtime, parent_result, tree


@pytest.mark.parametrize("final", ["empty", "incomplete", "ax_only", "captured"])
def test_postwrite_last_window_departure_reports_current_catalogue_without_ghost_card(monkeypatch, final):
    original = window("Closing")
    before = directory(original)
    after = {**directory(), "rootDiscoveryComplete": True}
    if final == "incomplete":
        after = {**after, "rootDiscoveryComplete": False, "stopReasons": ["AX unavailable"]}
    if final == "ax_only":
        after = directory({**window("AXOnly"), "nativeWindow": False}, focused="AXOnly")
    controller, app, state = setup(before)
    tree_gap = ObservationGap("tree", "observation_incomplete", "AX root unavailable in this observation")
    image_gap = ObservationGap("image", "stale_target", "window disappeared or geometry changed")
    observed = parent_result(state, original, before, after)
    if final != "captured":
        observed = replace(observed, tree=None, gaps=(tree_gap, image_gap), scope_confirmed=False)
    controller.reader.read = AsyncMock(return_value=observed)
    pipeline = InterfaceReadPipeline(controller)
    async def member(_ui, item, _view, **_kwargs):
        return WindowPart(member_target(state, item), tree=tree(item, "AX-only content preserved")) if item["window"] != "Closing" else WindowPart(member_target(state, item), gaps=(image_gap,))

    pipeline.member = AsyncMock(side_effect=member)

    async def refresh(*_args, **_kwargs):
        adopt(controller, app, after)
        return after

    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.refresh", refresh)
    related = RelatedRead(member_target(state, original), (WindowIdentity.of(original),))
    events = []
    result = asyncio.run(pipeline._collect(app, "both", False, None, before, None, None, related,
                                         on_observation=events.append))
    metadata = observation_data(result)
    delivered = deliver_through_runtime(events)
    text = "\n".join(block["text"] for block in delivered["content"] if block["type"] == "text")
    if final == "empty":
        assert "Current catalogue: no observable windows." in text
        assert "Original window lifetime: unconfirmed." in text
        assert "outside read scope" not in text
        assert "Screenshot: unavailable" not in text
        assert "AX root unavailable" in text and "window disappeared or geometry changed" in text
        assert metadata["content_available"] and not metadata["partial"]
        assert all(not item["required"] and not item["included"] for item in metadata["window_coverage"])
        assert state.evidence[WindowIdentity.of(original)].lifetime is WindowLifetime.RETAINED
    else:
        assert "Current catalogue: no observable windows." not in text
        if final == "captured":
            assert "Parent content" in text
        if final == "ax_only":
            assert "AX-only content preserved" in text
