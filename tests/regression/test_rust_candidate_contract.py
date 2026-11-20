# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Behavior shared by the original and Rust-backed action candidate phases."""

from dataclasses import replace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.routes.selection.candidates import CANDIDATE_PHASES, enumerate_candidates
from computer_use.interaction.write.routes.selection.model import SelectionRequest
from computer_use.native.contracts import Channel, Intent, Knowledge, Primitive
from computer_use.native.semantics import action_for_intent
from regression.test_text_input_routes import KEYS
from regression.test_unified_write_routing import CLICK, click_route, facts
from regression.test_windows_replacement_candidates import COLD_KEYS, VALUE


def action(name, identifier, *, description=None, supported=Knowledge.YES):
    return Primitive(
        identifier,
        "native_action",
        Channel.OBJECT,
        supported,
        Knowledge.YES,
        action=name,
        action_description=description,
    )


def test_exact_action_keeps_unavailable_primitive_ahead_of_label_matches():
    available_label = action("other", "available", description="AXPress")
    unavailable_exact = action("AXPress", "unavailable", supported=Knowledge.NO)
    observed = facts(available_label, unavailable_exact)
    request = SelectionRequest(observed, Intent("action", {"action": "AXPress"}))

    current = request
    for index, (_, phase, _, _, _) in enumerate(CANDIDATE_PHASES):
        value = phase(current)
        assert value.request is request
        if index >= 2:
            assert value is current
        current = value

    assert len(current.primitives) == 1
    assert current.primitives[0] is unavailable_exact
    assert enumerate_candidates(observed, request.intent) == current.primitives


def test_missing_action_preserves_refusal_payload_and_observed_names():
    declared = tuple(action(name, name) for name in ("ω", "g", "f", "e", "d", "c", "b", "a", "h", "\ud800"))
    observed = facts(*declared, within_scope=Knowledge.NO)

    with pytest.raises(ComputerUseError) as caught:
        enumerate_candidates(observed, Intent("action", {"action": "\udc00"}))

    assert caught.value.code == "input_unavailable"
    assert caught.value.effect == "none"
    assert caught.value.target == "target"
    assert caught.value.detail == (
        "No native action matches '\\udc00' on this element."
        " Observed names: a, b, c, d, e, f, g, h; more in the element observation."
    )
    assert caught.value._observation_reason == "targetState"


def test_equal_pointer_priorities_keep_original_declaration_order():
    first = replace(click_route("surface_pointer"), id="z")
    second = replace(first, id="a")

    ordered = enumerate_candidates(facts(first, second), CLICK)

    assert ordered[0] is first
    assert ordered[1] is second


def test_windows_ready_text_keys_and_independent_setter_both_keep_their_rank():
    observed = facts(replace(COLD_KEYS, id="cold"), VALUE, KEYS, platform="windows")

    ordered = enumerate_candidates(observed, Intent("replace", {"text": "value"}))

    assert tuple(primitive.id for primitive in ordered) == (VALUE.id, KEYS.id, "cold")


@pytest.mark.parametrize(
    "platform,role,actions,complete,expected",
    [
        ("linux", "push button", ("Click", "CLICK", "activate"), True, None),
        ("linux", "push button", ("ACTIVATE",), True, "ACTIVATE"),
        ("windows", "ControlType.ListItem", ("Invoke.Invoke",), True, None),
        ("macos", "AXMenuItem", ("AXPick",), True, "AXPick"),
        ("macos", "AXMenuItem", ("AXPick",), False, None),
    ],
)
def test_native_action_meaning_preserves_absence_and_ambiguity(platform, role, actions, complete, expected):
    assert action_for_intent(platform, role, actions, complete, "click") == expected
