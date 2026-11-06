# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Observed dropdown presentation is not a new native window lifetime."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline, WindowPart, member_target
from computer_use.interaction.read.views.related import RelatedRead
from computer_use.protocol.observation import NativeNode, NativeObservation, Relation
from computer_use.session.interfaces import WindowIdentity
from test_d34_read_contracts import directory, setup, window
from test_dynamic_observation import deliver_through_runtime, parent_result


def captured(state, item, *, expanded, complete=True):
    children = ("list",) if expanded else ()
    nodes = {
        item["node"]: NativeNode(role="window", relations={"children": Relation("children", ("opener",), total=1)}),
        "opener": NativeNode(role="menu_button", name="Time", relations={
            "children": Relation("children", children, total=len(children) if complete else 1),
        }),
    }
    if expanded:
        nodes["list"] = NativeNode(role="list", relations={"children": Relation("children", ("choice",), total=1)})
        nodes["choice"] = NativeNode(role="static_text", name="10:00")
    native = NativeObservation("macos", "session", str(expanded), (1, 2), (item["node"],), nodes)
    return WindowPart(member_target(state, item), tree=normalize_facts(native, DisplayReferences("session")))


def test_inline_menu_notification_reaches_model_without_new_directory_entry():
    form = window("Form")
    current = directory(form)
    controller, app, state = setup(current)
    result = replace(
        parent_result(state, form, current, current), tree=captured(state, form, expanded=True).tree, scope_confirmed=True,
    )
    controller.reader.read = AsyncMock(return_value=result)
    events = []
    related = RelatedRead(member_target(state, form), (WindowIdentity.of(form),))
    asyncio.run(InterfaceReadPipeline(controller)._collect(
        app, "tree", False, None, current, None, None, related, on_observation=events.append,
    ))
    delivered = deliver_through_runtime(events)
    text = "\n".join(item["text"] for item in delivered["content"] if item["type"] == "text")
    assert "Menu contents observed for window=0" in text
    assert "10:00" in text


def test_inline_menu_appearance_and_end_are_not_inferred_from_window_ids():
    from computer_use.interaction.read.views.presentations import collect_presentations

    form = window("Form")
    current = directory(form)
    _, _, state = setup(current)
    shown = collect_presentations({}, current, [captured(state, form, expanded=True)])
    assert len(shown) == 1 and shown[0]["state"] == "observed"
    prior = {item["id"]: item for item in shown}
    missing = collect_presentations(prior, current, [captured(state, form, expanded=False, complete=False)])
    assert missing[0]["state"] == "unconfirmed"
    ended = collect_presentations(prior, current, [captured(state, form, expanded=False)])
    assert ended[0]["state"] == "ended" and ended[0]["evidence"] == "openerChildrenRemoved"
    later_partial = collect_presentations(
        {item["id"]: item for item in ended}, current, [captured(state, form, expanded=False, complete=False)],
    )
    assert later_partial[0]["state"] == "ended" and later_partial[0]["transition"] is None
    reopened = collect_presentations({item["id"]: item for item in ended}, current, [captured(state, form, expanded=True)])
    assert reopened[0]["id"] == shown[0]["id"] and reopened[0]["transition"] == "reappeared"


def test_shared_popup_and_inline_menu_are_one_presentation_with_two_sources():
    from computer_use.interaction.read.views.presentations import collect_presentations

    form, menu = window("Form"), window("Popup")
    menu["content"] = {"kind": "menu", "host": "Form", "hostNode": "list", "evidence": "AXChildren"}
    current = directory(form, menu)
    _, _, state = setup(current)
    result = collect_presentations({}, current, [captured(state, form, expanded=True)])
    assert len(result) == 1
    assert {source["window"] for source in result[0]["sources"]} == {"Form", "Popup"}
    assert result[0]["host"] == "Form"


def test_unrelated_window_and_static_menu_commands_do_not_prove_presentation_end():
    from computer_use.interaction.read.views.presentations import collect_presentations

    form, other = window("Form"), window("Other")
    current = directory(form, other)
    _, _, state = setup(current)
    shown = collect_presentations({}, current, [captured(state, form, expanded=True)])
    unrelated = collect_presentations({item["id"]: item for item in shown}, current, [captured(state, other, expanded=False)])
    assert unrelated[0]["state"] == "observed"
    # An application menu command tree is readable while closed. It is not a
    # visible popup merely because the command's children were enumerated.
    part = captured(state, form, expanded=True)
    part.target = replace(part.target, root_window=None)
    assert collect_presentations({}, current, [part]) == ()


def test_historical_part_and_unreachable_menu_do_not_announce_current_presentation():
    from computer_use.interaction.read.views.presentations import collect_presentations

    form, other = window("Form"), window("Other")
    current = directory(form, other)
    _, _, state = setup(current)
    part = captured(state, form, expanded=True)
    assert collect_presentations({}, directory(other), [part]) == ()
    nodes = dict(part.tree.native.nodes)
    nodes[form["node"]] = NativeNode(role="window")
    part.tree = normalize_facts(replace(part.tree.native, nodes=nodes), DisplayReferences("session"))
    assert collect_presentations({}, current, [part]) == ()


def test_native_menu_missing_during_host_read_is_unconfirmed_not_still_observed_or_destroyed():
    from computer_use.interaction.read.views.presentations import collect_presentations

    form, menu, other = window("Form"), window("Popup"), window("Other")
    menu["content"] = {"kind": "menu", "host": "Form", "evidence": "visibleNativeMenu"}
    current = directory(form, menu, other)
    _, _, state = setup(current)
    shown = collect_presentations({}, current, [captured(state, menu, expanded=False)])
    previous = {item["id"]: item for item in shown}
    unrelated = collect_presentations(previous, directory(form, other), [captured(state, other, expanded=False)])
    assert unrelated[0]["state"] == "observed"
    missing = collect_presentations(previous, directory(form, other), [captured(state, form, expanded=False)])
    assert missing[0]["state"] == "unconfirmed"
    assert missing[0]["evidence"] == "notObservedInRead"
