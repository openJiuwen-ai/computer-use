# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A late popup must reach the caller, even with usable parent content and partial AX."""

import asyncio
import base64
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from computer_use.interaction.query.instances.scope import adopt
from computer_use.interaction.read.acquisition.components.support import ObservationGap
from computer_use.interaction.read.acquisition.images.capture import WindowImage
from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.interaction.read.model import AppObservation
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.views.collection import CollectionPlan
from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline, WindowPart, member_target
from computer_use.interaction.read.views.related import RelatedRead
from computer_use.native.scopes import WorkingWindow
from computer_use.protocol.captures import decode_capture
from computer_use.protocol.observation import NativeNode, NativeObservation, Relation
from computer_use.session import images
from computer_use.session.events import observation_delivered, set_observation_handler
from computer_use.session.interfaces import WindowIdentity
from computer_use.session.observation_data import observation_data
from computer_use_mcp.output import ObservationOutput, ObservationPresenter, OutputBuffer
from PIL import Image
from test_d34_read_contracts import directory, setup, window


def tree(item, title):
    root, child = item["node"], item["node"] + ":child"
    native = NativeObservation(
        "macos",
        "session",
        title,
        (1, 2),
        (root,),
        {
            root: NativeNode(
                role="window",
                name=item["title"],
                relations={
                    "children": Relation(kind="children", targets=(child,), total=1),
                },
            ),
            child: NativeNode(role="button", name=title),
        },
    )
    return normalize_facts(native, DisplayReferences("session"))


def pixels(state, item):
    buffer = BytesIO()
    with Image.new("RGB", (20, 10), "white") as image:
        image.save(buffer, format="PNG")
    target = replace(member_target(state, item), node=None, capture="capture:" + item["window"])
    png = decode_capture(
        {
            "id": target.capture,
            "kind": "window",
            "scope": target.surface,
            "startedAt": 1,
            "completedAt": 2,
            "width": 20,
            "height": 10,
            "png": base64.b64encode(buffer.getvalue()).decode(),
        },
        "window",
    )
    return WindowImage(target, png, {}, {"nativeSpace": "screen", "toNative": (1, 0, 0, 1, 0, 0)})


def parent_result(state, a, before, after):
    target = member_target(state, a)
    return AppObservation(
        WorkingWindow("application", "Fixture", target),
        target,
        None,
        before,
        after,
        tree(a, "Parent content"),
        None,
        DisplayDocument.join(()),
        {},
        {},
        (),
        False,
    )


@pytest.mark.parametrize("failure", [None, "tree", "image", "directory", "both"])
def test_late_sheet_is_delivered_in_same_observation_with_partial_catalogue(monkeypatch, failure):
    a = window("A")
    sheet = {**window("Sheet", parent="A"), "modal": True, "kind": "sheet"}
    before = directory(a)
    after = directory(a, sheet, focused="Sheet", reasons=("unrelated AXError -25202",))
    controller, app, state = setup(before)
    controller.reader.read = AsyncMock(return_value=parent_result(state, a, before, after))
    pipeline = InterfaceReadPipeline(controller)
    related = RelatedRead(member_target(state, a), (WindowIdentity.of(a),))
    gap = ObservationGap("tree", "observation_incomplete", "AX not ready")
    calls = []

    async def member(owner, item, view, **kwargs):
        calls.append((item["window"], view))
        assert item["window"] == "Sheet"
        return WindowPart(
            member_target(state, item),
            tree=None if failure in ("tree", "both") else tree(item, "Use Touch ID or enter password"),
            image=None if failure in ("image", "both") else pixels(state, item),
            gaps=(gap,) if failure in ("tree", "both") else (),
        )

    async def recheck(*args, **kwargs):
        if failure == "directory":
            raise TimeoutError("optional recheck expired")
        adopt(controller, app, after)
        return after

    pipeline.member = member
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.refresh", recheck)
    monkeypatch.setattr(images, "_delivery_required", True)
    events = []
    set_observation_handler(events.append)
    try:
        observed = asyncio.run(pipeline._collect(app, "tree", False, None, before, None, None, related))
    finally:
        set_observation_handler(None)
    assert calls == [("Sheet", "both")]
    assert controller.reader.read.await_count == 1
    assert len(events) == 1
    coverage = {record["window"]: record for record in observation_data(observed)["window_coverage"]}
    assert coverage["Sheet"]["delivered"] == ()
    assert coverage["Sheet"]["requested"] == ("image", "tree")
    assert "Parent content" in observed.text
    assert observation_data(observed)["partial"]  # unrelated directory error is retained
    if failure not in ("tree", "both"):
        assert "Use Touch ID or enter password" in observed.text
        assert observed.text.index("Use Touch ID") < observed.text.index("Parent content")
    if failure not in ("image", "both"):
        assert observed.screenshot is not None
        assert app._image.pieces[0].window == "Sheet"
    if failure == "both":
        assert coverage["Sheet"]["missing"] == ("image", "tree")
        assert "content was not included" in observed.text
    if failure == "directory":
        assert "optional recheck expired" in observed.text
    presenter = ObservationPresenter()
    output, received = ObservationOutput(presenter), []
    output.observe(events[0]["observation"])

    def emit(text, event):
        received.append(text)
        observation_delivered(event["state"])

    def emit_image(png):
        received.append(png)
        images.image_delivered(png)

    try:
        output.flush(emit, emit_image)
    finally:
        presenter.close()
    coverage = {record["window"]: record for record in observation_data(observed)["window_coverage"]}
    assert set(coverage["Sheet"]["delivered"]) == set(coverage["Sheet"]["included"])
    if failure not in ("tree", "both"):
        assert "Use Touch ID or enter password" in received[0]


def test_explicit_tree_view_keeps_contract_but_still_collects_late_sheet(monkeypatch):
    a = window("A")
    sheet = {**window("Sheet", parent="A"), "modal": True}
    before, after = directory(a), directory(a, sheet)
    controller, app, state = setup(before)
    controller.reader.read = AsyncMock(return_value=parent_result(state, a, before, after))
    pipeline = InterfaceReadPipeline(controller)
    pipeline.member = AsyncMock(return_value=WindowPart(member_target(state, sheet), tree=tree(sheet, "Sheet content")))
    result = asyncio.run(pipeline._collect(app, "tree", False, None, before, None, None, None))
    assert pipeline.member.call_args.args[2] == "tree"
    assert "Sheet content" in result.text and result.screenshot is None


def test_replacement_sheet_is_collected_without_rebinding_captured_old_identity(monkeypatch):
    a = window("A")
    one = {**window("One", parent="A"), "modal": True}
    two = {**window("Two", parent="A"), "modal": True}
    before, first, last = directory(a), directory(a, one), directory(a, two)
    controller, app, state = setup(before)
    controller.reader.read = AsyncMock(return_value=parent_result(state, a, before, first))
    pipeline = InterfaceReadPipeline(controller)
    captured = []

    async def member(owner, item, view, **kwargs):
        captured.append(member_target(state, item))
        return WindowPart(captured[-1], tree=tree(item, item["window"] + " body"), image=pixels(state, item))

    async def recheck(*args, **kwargs):
        adopt(controller, app, last)
        return last

    pipeline.member = member
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.refresh", recheck)
    related = RelatedRead(member_target(state, a), (WindowIdentity.of(a),))
    result = asyncio.run(pipeline._collect(app, "tree", False, None, before, None, None, related))
    assert [target.root_window for target in captured] == ["One", "Two"]
    assert captured[0].surface == one["surface"]
    assert "Two body" in result.text and "One body" not in result.text
    records = {record["window"]: record for record in observation_data(result)["window_coverage"]}
    assert records["One"]["lifecycle"] == "closed" and records["One"]["included"] == ()
    assert records["Two"]["included"] == ("tree", "image")


def test_ambiguous_modal_selection_does_not_block_independently_bound_reads():
    a = window("A")
    one, two = ({**window(key, parent="A"), "modal": True} for key in ("One", "Two"))
    current = directory(a, one, two)
    plan = CollectionPlan("A", "tree")
    plan.update(current, {item["window"]: item for item in current["surfaces"]})
    assert plan.selection_error is not None
    assert {plan.next()[0].window for _ in range(3)} == {"A", "One", "Two"}
    assert plan.next() is None


def test_member_tree_timeout_keeps_independent_pixels(monkeypatch):
    a = window("A")
    controller, app, state = setup(directory(a))
    controller.reader.reader.read = AsyncMock(side_effect=TimeoutError("tree component expired"))
    image = pixels(state, a)
    monkeypatch.setattr(
        "computer_use.interaction.read.views.pipeline.FixedWindowImagePipeline.arun",
        AsyncMock(return_value=SimpleNamespace(image=image, gap=None)),
    )
    part = asyncio.run(InterfaceReadPipeline(controller).member(app, a, "both", component_budget_ms=100))
    assert part.tree is None and part.image is image
    assert "tree component expired" in part.gaps[0].detail


def test_known_window_becoming_modal_gets_content_without_new_window_id():
    a, child = window("A"), window("Child")
    before = directory(a, child)
    _controller, _app, state = setup(before)
    related = RelatedRead(member_target(state, a), tuple(WindowIdentity.of(item) for item in before["surfaces"]))
    current = directory(a, {**child, "parent": "A", "parentState": "known", "modal": True})
    assert related.new_windows(current) == ()
    plan = CollectionPlan("A", "tree", related)
    plan.update(current, {item["window"]: item for item in current["surfaces"]})
    assert plan.next() == (WindowIdentity.of(child), "both")


def test_failed_optional_recheck_keeps_collecting_already_known_peers(monkeypatch):
    a, one, two = window("A"), window("One"), window("Two")
    before, after = directory(a), directory(a, one, two)
    controller, app, state = setup(before)
    controller.reader.read = AsyncMock(return_value=parent_result(state, a, before, after))
    pipeline = InterfaceReadPipeline(controller)
    calls = []

    async def member(owner, item, view, **kwargs):
        calls.append(item["window"])
        return WindowPart(member_target(state, item), tree=tree(item, item["window"] + " body"))

    pipeline.member = member
    monkeypatch.setattr(
        "computer_use.interaction.read.views.pipeline.refresh", AsyncMock(side_effect=ValueError("recheck failed"))
    )
    related = RelatedRead(member_target(state, a), (WindowIdentity.of(a),))
    result = asyncio.run(pipeline._collect(app, "tree", False, None, before, None, None, related))
    assert calls == ["One", "Two"]
    assert all(content in result.text for content in ("One body", "Two body", "Parent content", "recheck failed"))
    assert observation_data(result)["partial"]


def test_new_members_share_work_limit_and_unattempted_members_remain_explicit():
    a = window("A")
    current = directory(a, *(window(str(index), parent="A") for index in range(8)))
    plan = CollectionPlan("A", "tree")
    plan.update(current, {item["window"]: item for item in current["surfaces"]})
    count = 0
    while plan.next() is not None:
        count += 1
    assert count == 6
    records = plan.coverage(current, [])
    assert len(records) == 9
    assert all(record["missing"] == ("tree",) for record in records)
    assert sum(not record["attempted"] for record in records) == 3


def test_ordinary_read_does_not_add_directory_polling_or_image_acquisition(monkeypatch):
    a = window("A")
    current = directory(a)
    controller, app, state = setup(current)
    controller.reader.read = AsyncMock(return_value=parent_result(state, a, current, current))
    pipeline = InterfaceReadPipeline(controller)
    pipeline.member = AsyncMock()
    refresh = AsyncMock()
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.refresh", refresh)
    related = RelatedRead(member_target(state, a), (WindowIdentity.of(a),))
    result = asyncio.run(pipeline._collect(app, "tree", False, None, current, None, None, related))
    assert "Parent content" in result.text and result.screenshot is None
    assert controller.reader.read.await_count == 1
    pipeline.member.assert_not_awaited()
    refresh.assert_not_awaited()


def test_completeness_check_failure_keeps_captured_tree_and_image(monkeypatch):
    a = window("A")
    controller, app, state = setup(directory(a))
    snapshot, image = tree(a, "Retained content"), pixels(state, a)
    controller.reader.reader.read = AsyncMock(return_value=snapshot)
    monkeypatch.setattr(
        "computer_use.interaction.read.views.pipeline.FixedWindowImagePipeline.arun",
        AsyncMock(return_value=SimpleNamespace(image=image, gap=None)),
    )

    def failed_check(_):
        raise ValueError("completeness check failed")

    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.window_content_unexposed", failed_check)
    part = asyncio.run(InterfaceReadPipeline(controller).member(app, a, "both", component_budget_ms=100))
    assert part.tree is not None and part.image is image
    assert any("Retained content" == node.name for node in part.tree.native.nodes.values())
    assert part.gaps[0].component == "coverage"


def test_final_accounting_failure_does_not_veto_ordinary_observation(monkeypatch):
    a = window("A")
    current = directory(a)
    controller, app, state = setup(current)
    controller.reader.read = AsyncMock(return_value=parent_result(state, a, current, current))

    def failed_check(*args, **kwargs):
        raise ValueError("accounting failed")

    monkeypatch.setattr(CollectionPlan, "coverage", failed_check)
    related = RelatedRead(member_target(state, a), (WindowIdentity.of(a),))
    result = asyncio.run(
        InterfaceReadPipeline(controller)._collect(app, "tree", False, None, current, None, None, related)
    )
    assert "Parent content" in result.text and "accounting failed" in result.text
    assert observation_data(result)["content_available"]
    assert observation_data(result)["partial"]


def test_attached_menu_keeps_its_surface_kind_and_gets_visual_fallback():
    a = window("A")
    menu = {**window("Menu", parent="A"), "kind": "menu"}
    _, _, state = setup(directory(a))
    related = RelatedRead(member_target(state, a), (WindowIdentity.of(a),))
    current = directory(a, menu)
    assert related.new_windows(current) == ()  # Not an independent working window.
    plan = CollectionPlan("A", "tree", related)
    plan.update(current, {item["window"]: item for item in current["surfaces"]})
    assert plan.next() == (WindowIdentity.of(a), "tree")
    assert plan.next() == (WindowIdentity.of(menu), "both")


def test_explicit_member_read_does_not_run_automatic_suffix_completeness_check(monkeypatch):
    a = window("A")
    controller, app, _state = setup(directory(a))
    controller.reader.reader.read = AsyncMock(return_value=tree(a, "Explicit content"))
    check = Mock(side_effect=AssertionError("Only automatic related reads need this annotation"))
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.window_content_unexposed", check)
    part = asyncio.run(InterfaceReadPipeline(controller).member(app, a, "tree"))
    assert part.tree is not None and part.gaps == ()
    check.assert_not_called()


def test_image_composition_failure_does_not_claim_popup_body_was_included(monkeypatch):
    a = window("A", title="Parent")
    sheet = {**window("Sheet", title="Popup", parent="A"), "modal": True}
    before, after = directory(a), directory(a, sheet)
    controller, app, state = setup(before)
    controller.reader.read = AsyncMock(return_value=parent_result(state, a, before, after))
    pipeline = InterfaceReadPipeline(controller)
    pipeline.member = AsyncMock(return_value=WindowPart(member_target(state, sheet), image=pixels(state, sheet)))

    def failed_composition(*args, **kwargs):
        raise ValueError("Image composition failed")

    async def recheck(*args, **kwargs):
        adopt(controller, app, after)
        return after

    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.refresh", recheck)
    monkeypatch.setattr(
        "computer_use.interaction.read.acquisition.images.composite.pipeline.InterfaceImagePipeline.run",
        failed_composition,
    )
    related = RelatedRead(member_target(state, a), (WindowIdentity.of(a),))
    observed = asyncio.run(pipeline._collect(app, "tree", False, None, before, None, None, related))
    assert "Parent content" in observed.text and "Image composition failed" in observed.text
    assert observed.screenshot is None and observation_data(observed)["partial"]
    assert any('"Popup"' in line and "not expanded" in line for line in observed.text.splitlines())
    coverage = {record["window"]: record for record in observation_data(observed)["window_coverage"]}
    assert coverage["Sheet"]["captured"] == ("image",)
    assert coverage["Sheet"]["included"] == ()
    assert coverage["Sheet"]["delivered"] == ()
    assert coverage["Sheet"]["missing"] == ("image", "tree")
    assert coverage["A"]["included"] == ("tree",)


@pytest.mark.parametrize("failed_component", ["tree", "image"])
def test_output_limit_acknowledges_only_observation_components_actually_emitted(monkeypatch, failed_component):
    a = window("A")
    current = directory(a)
    controller, app, state = setup(current)
    parent = parent_result(state, a, current, current)
    parent = replace(parent, image=pixels(state, a))
    controller.reader.read = AsyncMock(return_value=parent)
    monkeypatch.setattr(images, "_delivery_required", True)
    events = []
    set_observation_handler(events.append)
    try:
        observed = asyncio.run(
            InterfaceReadPipeline(controller)._collect(app, "both", False, None, current, None, None, None)
        )
    finally:
        set_observation_handler(None)

    def coverage():
        return observation_data(observed)["window_coverage"][0]

    assert set(coverage()["included"]) == {"tree", "image"}
    assert coverage()["delivered"] == ()
    presenter = ObservationPresenter()
    output = ObservationOutput(presenter)
    output.observe(events[0]["observation"])
    buffer = OutputBuffer()
    # Exercise the real per-response block limit, before either component or
    # between successful text and the image. A captured PNG is not delivery.
    for _ in range(128 if failed_component == "tree" else 127):
        buffer.append({"type": "text", "text": "already emitted"})

    def emit(text, event):
        buffer.append({"type": "text", "text": text})
        observation_delivered(event["state"])

    def emit_image(png):
        buffer.append({"type": "image", "mimeType": "image/png", "data": base64.b64encode(png).decode()})
        images.image_delivered(png)

    try:
        with pytest.raises(RuntimeError, match="Output limit reached"):
            output.flush(emit, emit_image)
        assert coverage()["delivered"] == (() if failed_component == "tree" else ("tree",))
        assert not any(block["type"] == "image" for block in buffer.content)
        assert ("Parent content" in buffer.content[-1]["text"]) == (failed_component == "image")
    finally:
        presenter.close()
