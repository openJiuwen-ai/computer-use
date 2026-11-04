# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Presentation expiry preserves ready facts/pixels without claiming omitted trees."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline, WindowPart, member_target
from computer_use.session.observation_data import observation_data
from computer_use.session.operations import OperationDeadlineExceeded
from test_d34_read_contracts import directory, setup, window
from test_dynamic_observation import parent_result, pixels, tree


@pytest.mark.parametrize("failure", ["first", "second", "cancel"])
def test_preparation_boundary_preserves_ready_components_and_exact_bindings(monkeypatch, failure):
    a, b = window("A"), window("B", parent="A")
    current = directory(a, b, focused="A")
    controller, app, state = setup(current)
    initial = replace(parent_result(state, a, current, current), image=pixels(state, a))
    controller.reader.read = AsyncMock(return_value=initial)
    pipeline = InterfaceReadPipeline(controller)
    second_tree = tree(b, "Unprepared second body")
    pipeline.member = AsyncMock(return_value=WindowPart(member_target(state, b), tree=second_tree))
    prepared = []

    def prepare(request, control):
        prepared.append(request.snapshot.native.roots)
        if failure == "cancel":
            raise asyncio.CancelledError()
        if failure == "first" or len(prepared) == 2:
            raise OperationDeadlineExceeded("original operation deadline")
        return SimpleNamespace(roots=())

    pipeline.preparation.run = prepare
    rendered = []

    def document(_controller, target, _snapshot, **kwargs):
        rendered.append(target.root_window)
        return DisplayDocument.join((target.root_window + " prepared body",))

    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.tree_document", document)
    collected = pipeline._collect(app, "both", False, None, current, None, None, None)
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(collected)
        assert rendered == []
        return
    result = asyncio.run(collected)
    metadata = observation_data(result)
    assert result.screenshot is not None and metadata["content_available"]
    assert metadata["partial"] and "Tree: partial" in result.text
    assert any(gap.component == "presentation" for gap in metadata["gaps"])
    assert rendered == ([] if failure == "first" else ["A"])
    assert len(prepared) == (1 if failure == "first" else 2)
    coverage = {item["window"]: item for item in metadata["window_coverage"]}
    assert coverage["B"]["captured"] == ("tree",)
    assert "tree" not in coverage["B"]["included"]
    assert "tree" in coverage["B"]["missing"]
    assert ("tree" in coverage["A"]["included"]) == (failure == "second")
    assert controller.state(state.owners["B"]).query_snapshot is second_tree
    assert controller.state(state.owners["B"]).query_target == member_target(state, b)
