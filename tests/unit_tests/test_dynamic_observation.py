# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A late popup must reach the caller, even with usable parent content and partial AX."""

import asyncio
import base64
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from computer_use.client.host import (
    set_action_handler,
    set_diagnostic_handler,
    set_execution_guard,
    set_preview_handler,
    set_text_page_handler,
)
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
from computer_use.session.events import set_observation_handler
from computer_use.session.interfaces import WindowIdentity
from computer_use.session.observation_data import observation_data
from computer_use_mcp.runtime import Runtime
from PIL import Image
from test_d34_read_contracts import directory, setup, window


def deliver_through_runtime(observations, *, prefix_blocks=0):
    """Use the actual final-response publisher and delivery acknowledgements."""
    async def deliver():
        previous_delivery = images._delivery_required
        runtime = Runtime(lambda event: None, computer_factory=lambda: None)

        def publish():
            for _ in range(prefix_blocks):
                runtime.emit({"type": "text", "text": "already emitted"})
            for observation in observations:
                runtime.observation({"kind": "observed", "observation": observation})

        runtime.task_namespace["deliver"] = lambda: runtime.executor.call(publish)
        try:
            return await runtime.execute({"code": "deliver()", "executionId": 1})
        finally:
            await runtime.close()
            for setter in (
                set_action_handler, set_diagnostic_handler, set_execution_guard,
                set_observation_handler, set_preview_handler, set_text_page_handler,
            ):
                setter(None)
            images._delivery_required = previous_delivery

    return asyncio.run(deliver())


def tree(item, title, references=None):
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
    return normalize_facts(native, references if references is not None else DisplayReferences("session"))


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
    result = deliver_through_runtime([event["observation"] for event in events])
    assert not result.get("isError"), result
    received = [block["text"] for block in result["content"] if block["type"] == "text"]
    coverage = {record["window"]: record for record in observation_data(observed)["window_coverage"]}
    assert set(coverage["Sheet"]["delivered"]) == set(coverage["Sheet"]["included"])
    if failure not in ("tree", "both"):
        assert "Use Touch ID or enter password" in received[0]


@pytest.mark.parametrize(
    "relationship", ["attached", "attached-partial", "independent", "unknown", "replaced", "wrong-anchor"],
)
def test_related_read_accounts_for_parent_bound_sheet_by_its_own_window(monkeypatch, relationship):
    parent, other = window("A"), window("B")
    sheet = {**window("Sheet", title="Go To", parent="A"), "modal": True, "kind": "sheet"}
    if relationship in ("independent", "unknown"):
        sheet = {**sheet, "parent": None, "parentState": "none" if relationship == "independent" else "unknown"}
    reasons = ("unrelated AX budget",) if relationship == "attached-partial" else ()
    source = directory(parent, other, sheet, focused="Sheet", reasons=reasons)
    current = (
        directory(parent, other, {**sheet, "instance": "replacement", "node": "node:replacement"},
                  focused="Sheet", ended=(sheet,))
        if relationship == "replaced" else source
    )
    controller, app, state = setup(source)
    parent_target = member_target(state, parent)
    target = replace(
        parent_target, surface=sheet["surface"], node=sheet["node"],
        instance=other["instance"] if relationship == "wrong-anchor" else parent_target.instance,
    )
    image = pixels(state, sheet)
    image = replace(image, target=replace(target, node=None, capture=image.target.capture))
    references = DisplayReferences("session")
    snapshot = tree(sheet, "Path input", references)
    first = replace(
        parent_result(state, parent, source, current), target=target, tree=snapshot,
        image=image, scope_confirmed=not reasons,
    )
    controller.reader.read = AsyncMock(return_value=first)
    pipeline = InterfaceReadPipeline(controller)

    async def member(_owner, item, _view, **_kwargs):
        return WindowPart(member_target(state, item), tree=tree(item, "Other content", references))

    async def recheck(*_args, **_kwargs):
        adopt(controller, app, current)
        return current

    pipeline.member = member
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.refresh", recheck)
    related = RelatedRead(parent_target, tuple(WindowIdentity.of(item) for item in source["surfaces"]))
    events = []
    set_observation_handler(events.append)
    try:
        observation = asyncio.run(pipeline._collect(app, "tree", False, None, source, None, None, related))
    finally:
        set_observation_handler(None)
    coverage = {record["window"]: record for record in observation_data(observation)["window_coverage"]}
    if relationship not in ("attached", "attached-partial"):
        assert "Path input" not in observation.text and observation.screenshot is None
        assert not set(snapshot.refs).intersection(state.refs)
        assert not any(item.get("window_identity") == WindowIdentity.of(sheet) for item in state.refs.values())
        return

    assert "Path input" in observation.text and observation.screenshot is image.image
    sheet_ref = next(ref for ref, key in state.windows.items() if key == "Sheet")
    heading = next(line for line in observation.text.splitlines() if line.startswith(f"Window {sheet_ref}:"))
    assert "current" in heading and "modal" in heading and "not expanded" not in heading
    assert f"Window {sheet_ref}: image, tree content was not included" not in observation.text
    assert coverage["Sheet"]["captured"] == coverage["Sheet"]["included"] == ("tree", "image")
    assert app._image.pieces[0].window == "Sheet"
    assert app._image.pieces[0].image.target == image.target
    assert not app._image.pieces[0].scope_confirmed  # coordinate authority still needs the exact member binding
    result = deliver_through_runtime([event["observation"] for event in events])
    assert not result.get("isError")
    assert any("Path input" in block.get("text", "") for block in result["content"])
    assert any(block["type"] == "image" for block in result["content"])
    coverage = {record["window"]: record for record in observation_data(observation)["window_coverage"]}
    assert set(coverage["Sheet"]["delivered"]) == {"tree", "image"}
    for ref in snapshot.refs:
        assert state.refs[ref]["binding"].root_window == "A"
        assert state.refs[ref]["window_identity"] == WindowIdentity.of(sheet)


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
    last["endedWindows"] = [one]
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


@pytest.mark.parametrize("automatic", [True, False])
def test_lost_tree_body_adds_pixels_only_to_automatic_observation(monkeypatch, automatic):
    a = window("A")
    current = directory(a)
    controller, app, state = setup(current)
    observed = replace(parent_result(state, a, current, current), tree=None)
    controller.reader.read = AsyncMock(return_value=observed)
    pipeline = InterfaceReadPipeline(controller)
    image = pixels(state, a)
    pipeline.member = AsyncMock(return_value=WindowPart(member_target(state, a), image=image))
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.refresh", AsyncMock(return_value=current))
    from computer_use.interaction.read.views.changes import PostwriteChanges

    related = RelatedRead(
        member_target(state, a), (WindowIdentity.of(a),), changes=PostwriteChanges(None, tree(a, "Previous content")),
    )
    events = []
    set_observation_handler(events.append)
    try:
        result = asyncio.run(pipeline._collect(
            app, "tree", False, None, current, None, None, related if automatic else None
        ))
    finally:
        set_observation_handler(None)
    if automatic:
        pipeline.member.assert_awaited_once()
        assert pipeline.member.call_args.args[2] == "image"
        assert result.screenshot is not None
        blocks = deliver_through_runtime([event["observation"] for event in events])
        assert not blocks.get("isError"), blocks
        assert sum(block["type"] == "image" for block in blocks["content"]) == 1
    else:
        pipeline.member.assert_not_awaited()
        assert result.screenshot is None


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
    # Exercise the real per-response block limit, before either component or
    # between successful text and the image. A captured PNG is not delivery.
    result = deliver_through_runtime(
        [event["observation"] for event in events],
        prefix_blocks=128 if failed_component == "tree" else 127,
    )
    assert result["isError"]
    assert "Output limit reached" in result["content"][-1]["text"]
    assert coverage()["delivered"] == (() if failed_component == "tree" else ("tree",))
    assert not any(block["type"] == "image" for block in result["content"])
    assert ("Parent content" in result["content"][-2]["text"]) == (failed_component == "image")

@pytest.mark.parametrize("root_available,tree_success", [(True, True), (False, False), (True, False)])
def test_visual_first_window_uses_same_call_ax_upgrade_without_repeating_image(
    monkeypatch, root_available, tree_success
):
    """Reproduce the delivered form image/no-root gap followed by a positive AX directory."""
    form = window("Form", title="Booking")
    visual = {**form, "node": None, "accessibility": False}
    toolbar = window("Toolbar", title="Sharing", parent="Form")
    first_catalog = directory(visual, toolbar, focused="Form")
    upgraded = directory(form if root_available else visual, toolbar, focused="Form")
    controller, app, state = setup(first_catalog)
    original = member_target(state, visual)
    image = pixels(state, visual)
    unavailable = ObservationGap("tree", "observation_incomplete", "AX root is not available")
    controller.reader.read = AsyncMock(return_value=AppObservation(
        selection=WorkingWindow("application", "Fixture", original), target=original,
        applications=None, source_directory=first_catalog, directory=upgraded, tree=None, image=image,
        document=DisplayDocument.join(()), refs={}, windows={}, gaps=(unavailable,), scope_confirmed=True,
    ))
    pipeline = InterfaceReadPipeline(controller)
    calls = []

    async def member(owner, item, view, **kwargs):
        calls.append((item["window"], view, item["node"]))
        if item["window"] == "Form":
            assert root_available and view == "tree"
            assert item["node"] == form["node"]
            return WindowPart(
                member_target(state, item), tree=tree(form, "Meeting title") if tree_success else None,
                gaps=() if tree_success else (
                    ObservationGap("tree", "observation_incomplete", "tree budget exhausted"),
                ),
            )
        return WindowPart(member_target(state, item), tree=tree(item, "Sharing button"))

    async def recheck(*args, **kwargs):
        adopt(controller, app, upgraded)
        return upgraded

    pipeline.member = member
    recheck = AsyncMock(side_effect=recheck)
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.refresh", recheck)
    result = asyncio.run(pipeline._collect(
        app, "both", False, None, first_catalog, None, None,
        RelatedRead(original, (WindowIdentity.of(form), WindowIdentity.of(toolbar))),
    ))
    assert controller.reader.read.await_count == 1
    assert [call for call in calls if call[0] == "Form"] == (
        [("Form", "tree", form["node"])] if root_available else []
    )
    assert recheck.await_count == 1  # one recheck for the planned member batch
    assert result.screenshot is not None
    metadata = observation_data(result)
    assert metadata["content_available"]
    coverage = {record["window"]: record for record in metadata["window_coverage"]}
    if tree_success:
        assert "Meeting title" in result.text
        assert "AX root is not available" not in result.text
        assert not any(gap.component == "tree" for gap in metadata["gaps"])
        assert set(coverage["Form"]["included"]) == {"tree", "image"}
        assert controller.state(state.owners["Form"]).query_target.node == form["node"]
    else:
        assert "tree" in coverage["Form"]["missing"]
        if root_available:
            assert "AX root is not available" not in result.text
            assert "tree budget exhausted" in result.text
            assert controller.state(state.owners["Form"]).query_target.node == form["node"]
        else:
            assert "AX root is not available" in result.text
