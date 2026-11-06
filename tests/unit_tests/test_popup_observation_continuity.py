# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A menu is observation work even when it is not a new working window."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

from computer_use.interaction.query.instances.scope import adopt, allowed
from computer_use.interaction.read.views.collection import CollectionPlan
from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline, WindowPart, member_target
from computer_use.interaction.read.views.related import RelatedRead
from computer_use.session.interfaces import WindowIdentity
from computer_use.session.observation_data import observation_data
from test_d34_read_contracts import directory, setup, window
from test_dynamic_observation import deliver_through_runtime, parent_result, pixels, tree


def test_new_menu_without_native_parent_is_collected_without_replacing_working_window():
    parent = window("Form")
    menu = {**window("Options"), "kind": "menu", "parentState": "unknown"}
    before, after = directory(parent), directory(parent, menu, focused="Options")
    _, _, state = setup(before)
    related = RelatedRead(member_target(state, parent), (WindowIdentity.of(parent),))
    plan = CollectionPlan("Form", "tree", related)
    plan.update(after, {item["window"]: item for item in after["surfaces"]})
    assert plan.principal == "Form"
    assert plan.next() == (WindowIdentity.of(parent), "tree")
    assert plan.next() == (WindowIdentity.of(menu), "both")
    assert plan.next() is None


def test_same_exact_content_is_reused_but_partial_host_keeps_menu_tree_obligation():
    parent, menu = window("Form"), window("Options")
    menu["content"] = {"kind": "menu", "host": "Form", "hostNode": parent["node"], "evidence": "AXChildren"}
    current = directory(parent, menu)
    _, _, state = setup(current)
    snapshot = tree(parent, "10:00")
    part = WindowPart(member_target(state, parent), tree=snapshot)
    plan = CollectionPlan("Form", "both")
    plan.update(current, state.members)
    plan.captured(parent, part)
    plan.reuse_attached_content([part], state.members)
    assert plan.next() == (WindowIdentity.of(parent), "image")
    assert plan.next() == (WindowIdentity.of(menu), "image")
    coverage = plan.coverage(current, [part], included_trees={parent["surface"]})
    record = next(item for item in coverage if item["window"] == "Options")
    assert record["included"] == ("tree",)
    assert record["reused"]["tree"]["node"] == parent["node"]

    incomplete = replace(snapshot, native=replace(snapshot.native, nodes={parent["node"]: snapshot.native.nodes[parent["node"]]}))
    missing = CollectionPlan("Form", "both")
    missing.update(current, state.members)
    missing.captured(parent, replace(part, tree=incomplete))
    missing.reuse_attached_content([replace(part, tree=incomplete)], state.members)
    assert missing.next() == (WindowIdentity.of(parent), "image")
    assert missing.next() == (WindowIdentity.of(menu), "both")


def test_related_menu_reuses_delivered_host_body_and_only_collects_its_pixels(monkeypatch):
    parent, menu = window("Form"), window("Options")
    menu["content"] = {"kind": "menu", "host": "Form", "hostNode": parent["node"], "evidence": "AXChildren"}
    before, after = directory(parent), directory(parent, menu)
    controller, app, state = setup(before)
    controller.reader.read = AsyncMock(return_value=replace(parent_result(state, parent, before, after), scope_confirmed=True))
    pipeline = InterfaceReadPipeline(controller)
    pipeline.member = AsyncMock(return_value=WindowPart(member_target(state, menu), image=pixels(state, menu)))
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.refresh", AsyncMock(return_value=after))
    related = RelatedRead(member_target(state, parent), (WindowIdentity.of(parent),))
    events = []
    observed = asyncio.run(pipeline._collect(
        app, "tree", False, None, before, None, None, related, on_observation=events.append,
    ))
    assert pipeline.member.call_args.args[2] == "image"
    delivered = deliver_through_runtime(events)
    assert not delivered.get("isError"), delivered
    text = "\n".join(block["text"] for block in delivered["content"] if block["type"] == "text")
    assert "AX: included in host tree; Image: collected." in text
    assert "AX: unavailable" not in text
    coverage = next(item for item in observation_data(observed)["window_coverage"] if item["window"] == "Options")
    assert set(coverage["delivered"]) == {"tree", "image"}


def test_late_menu_body_and_pixels_reach_model_in_original_object_call(monkeypatch):
    parent = window("Form")
    menu = {**window("Options"), "kind": "menu", "parentState": "unknown"}
    before, after = directory(parent), directory(parent, menu)
    controller, app, state = setup(before)
    controller.reader.read = AsyncMock(return_value=parent_result(state, parent, before, after))
    pipeline = InterfaceReadPipeline(controller)
    pipeline.member = AsyncMock(return_value=WindowPart(
        member_target(state, menu), tree=tree(menu, "10:00"), image=pixels(state, menu),
    ))

    async def refresh(*args, **kwargs):
        adopt(controller, app, after)
        return after

    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.refresh", refresh)
    related = RelatedRead(member_target(state, parent), (WindowIdentity.of(parent),))
    events = []
    result = asyncio.run(pipeline._collect(
        app, "tree", False, None, before, None, None, related, on_observation=events.append,
    ))
    delivered = deliver_through_runtime(events)
    text = "\n".join(block["text"] for block in delivered["content"] if block["type"] == "text")
    assert "10:00" in text
    assert any(block["type"] == "image" for block in delivered["content"])
    coverage = {item["window"]: item for item in observation_data(result)["window_coverage"]}
    assert set(coverage["Options"]["delivered"]) == {"tree", "image"}
    assert controller.reader.read.await_count == 1
    assert pipeline.member.await_count == 1


def test_distinct_ax_sources_share_only_their_confirmed_physical_pixels():
    parent, source = window("Form"), window("Source")
    source["content"] = {"kind": "unknown", "host": "Form", "evidence": "nativeWindowID"}
    current = directory(parent, source)
    _, _, state = setup(current)
    part = WindowPart(member_target(state, parent), tree=tree(parent, "host"), image=pixels(state, parent))
    plan = CollectionPlan("Form", "both")
    plan.update(current, state.members)
    plan.captured(parent, part)
    plan.reuse_attached_content([part], state.members)
    assert plan.next() == (WindowIdentity.of(source), "tree")
    record = next(item for item in plan.coverage(current, [part], included_images={"Form"}) if item["window"] == "Source")
    assert record["included"] == ("image",)
    assert record["reused"]["image"]["window"] == "Form"


def test_fixed_window_delivers_unparented_new_menu_as_app_supplement(monkeypatch):
    parent, menu = window("Form"), {**window("Options"), "kind": "menu", "parentState": "unknown"}
    other = window("Other document")
    old_menu = {**window("Other menu"), "kind": "menu", "parentState": "known", "parent": other["window"]}
    before, after = directory(parent, other, old_menu), directory(parent, other, old_menu, menu)
    controller, app, state = setup(before)
    adopt(controller, app, before)
    app._anchor = "Form"
    controller.reader.read = AsyncMock(return_value=replace(parent_result(state, parent, before, after), scope_confirmed=True))
    pipeline = InterfaceReadPipeline(controller)

    async def member(ui, item, view, **kwargs):
        return WindowPart(member_target(state, item), tree=tree(item, "10:00" if item["window"] == "Options" else "Form"))

    async def refresh(*args, **kwargs):
        adopt(controller, app, after)
        return after

    pipeline.member = AsyncMock(side_effect=member)
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.refresh", refresh)
    related = RelatedRead(member_target(state, parent), tuple(WindowIdentity.of(item) for item in before["surfaces"]))
    events = []
    asyncio.run(pipeline._collect(app, "tree", False, None, before, None, None, related, on_observation=events.append))
    delivered = deliver_through_runtime(events)
    text = "\n".join(item["text"] for item in delivered["content"] if item["type"] == "text")
    assert "10:00" in text
    assert "App-level supplement" in text
    assert "newly observed menu sources:" in text
    assert "newly observed window entries:" not in text
    assert app._anchor == "Form"
    assert set(allowed(app)) == {"Form"}
    assert {call.args[1]["window"] for call in pipeline.member.call_args_list} == {"Form", "Options"}
