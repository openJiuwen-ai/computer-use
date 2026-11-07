# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Window-local menu choices may implement an ordinary click through AXPick."""

import asyncio
from dataclasses import replace

import pytest
from computer_use.interaction.write.model import (
    AttemptView,
    BoundWrite,
    OperationSpec,
    ResolutionFrame,
    ResolutionInput,
    ResolutionMemory,
)
from computer_use.interaction.write.resolution.passes import resolve_menu
from computer_use.interaction.write.routes.policy import Attempts, choose
from computer_use.native.contracts import HostPolicy, Intent
from computer_use.native.semantics import action_for_intent, control_capabilities
from unit_tests.test_unified_write_routing import CLICK, click_route, facts


def test_menu_pick_click_dispatches_an_independent_native_choice():
    pick = replace(click_route("object_action", action="AXPick"), id="pick")
    no_pointer = click_route("surface_pointer", "insufficientEvidence")
    observed = facts(pick, no_pointer, native_role="AXMenuItem")
    command = choose(observed, CLICK, HostPolicy(), Attempts())
    assert command.primitive == pick
    assert command.target == observed.binding
    assert not command.prepare and not command.foreground


def test_press_and_pointer_intents_retain_their_own_meaning():
    pick = replace(click_route("object_action", action="AXPick"), id="pick")
    press = click_route("object_action", action="AXPress")
    pointer = click_route("surface_pointer")
    observed = facts(pick, press, pointer, native_role="AXMenuItem")
    assert choose(observed, CLICK, HostPolicy(), Attempts()).primitive == press
    for args in ({"button": "right"}, {"count": 2}, {"x": 4, "y": 9}):
        assert choose(observed, Intent("click", args), HostPolicy(), Attempts()).primitive == pointer


@pytest.mark.parametrize(
    ("role", "actions", "complete", "intent"),
    [
        ("AXButton", ["AXPick"], True, "click"),
        ("AXMenuItem", ["AXPick", "AXPick"], True, "click"),
        ("AXMenuItem", ["AXPick"], False, "click"),
        ("AXMenuItem", ["AXPick"], True, "openMenu"),
    ],
)
def test_pick_requires_a_complete_unique_leaf_choice(role, actions, complete, intent):
    assert action_for_intent("macos", role, actions, complete, intent) is None


def test_pick_declaration_does_not_promise_global_menu_delivery():
    node = {
        "values": {"AXRole": "AXMenuItem", "AXEnabled": True},
        "fieldStatus": {},
        "actions": {"state": "complete", "items": [{"name": "AXPick"}]},
    }
    features = ("nativeActionIntents", "nativeMenuActions")
    assert control_capabilities("macos", node, features, scope_kind="applicationMenu")["click"] == "unknown"


def test_application_menu_mouse_route_keeps_the_original_native_ancestry(monkeypatch):
    pointer = click_route("surface_pointer")
    observed = replace(facts(pointer, native_role="AXMenuItem"), application_menu=True)
    within = ("menu-bar", "file", "file-menu")

    class Native:
        capabilities = frozenset({"nativeMenuActions"})
        policy = HostPolicy()

        async def inspect(self, operation, target, remaining, **scope):
            assert (operation, target, remaining, scope) == ("operation", observed.binding, 500, {"within": within})
            return observed

    class MenuQuery:
        def __init__(self, port, operation, target, remaining):
            assert (operation, target, remaining()) == ("operation", observed.binding, 500)

        async def resolve(self, anchors, *, inspect_submenu):
            assert anchors == () and not inspect_submenu
            return within, False

    monkeypatch.setattr(resolve_menu, "MenuContextQuery", MenuQuery)
    spec = OperationSpec(CLICK, observed.binding, (), HostPolicy())
    request = ResolutionInput(BoundWrite(spec, "operation", lambda: 500), AttemptView(), ResolutionMemory())
    frame = asyncio.run(resolve_menu.create(native=Native())(ResolutionFrame(request, facts=observed), None))
    assert frame.scope == {"within": within}
    assert frame.request.memory.menu_context == (within, False)
    assert choose(frame.facts, CLICK, HostPolicy(), Attempts()).primitive == pointer
