# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""App observation/lookup advances without rebinding historical Window handles."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.query.instances.scope import adopt, display_selection
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline, WindowPart, member_target, remember
from computer_use.interaction.read.views.publication import publish
from computer_use.native.contracts import Knowledge
from computer_use.native.scopes import FixedWindowInvalidated, FixedWindowUnconfirmed
from computer_use.session.events import observation_delivered, set_observation_handler
from computer_use.session.interfaces import LookupContext, WindowIdentity, WindowLifetime
from unit_tests.test_application_window_lifetimes import directory, ref, setup, surface
from unit_tests.test_d34_read_contracts import directory as read_directory
from unit_tests.test_d34_read_contracts import setup as read_setup
from unit_tests.test_d34_read_contracts import window


def test_app_display_selects_new_ax_form_over_historical_native_only_launcher():
    controller, app = setup(surface("launcher"))
    fixed = app.window(ref(app, "launcher"))
    original = fixed._window_identity
    updated = directory(surface("launcher", accessible=False), surface("form"), focus=None)
    adopt(controller, app, updated)
    selected = display_selection(controller, app._instance, updated)
    assert selected.root.root_window == "form"
    assert fixed._window_identity == original
    assert app._instance.evidence[WindowIdentity.of(surface("launcher"))].lifetime is WindowLifetime.RETAINED


@pytest.mark.parametrize("ended", [False, True])
def test_app_read_reselects_current_form_after_inner_fixed_launcher_disappears(monkeypatch, ended):
    launcher, form = window("Launcher"), window("Form")
    before, after = read_directory(launcher), read_directory(form, ended=(launcher,) if ended else ())
    controller, app, state = read_setup(before)
    old = controller.reader._selection("application", "Fixture", before, "Launcher")
    state.anchor = old
    failure = (
        FixedWindowInvalidated(old.root, "launcher ended", directory=after)
        if ended
        else FixedWindowUnconfirmed(old.root, directory=after)
    )
    controller.reader.read = AsyncMock(side_effect=failure)
    pipeline = InterfaceReadPipeline(controller)
    tree = SimpleNamespace(
        native=SimpleNamespace(session="session", roots=(form["node"],), issues=()), refs={}, roots=()
    )
    pipeline.member = AsyncMock(return_value=WindowPart(member_target(state, form), tree=tree))
    pipeline.preparation.run = lambda _value, _control=None: SimpleNamespace(roots=())
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.remember", lambda *_: None)
    monkeypatch.setattr(
        "computer_use.interaction.read.views.pipeline.tree_document",
        lambda *_a, **_kw: DisplayDocument.join(("New form controls",)),
    )
    result = asyncio.run(pipeline._collect(app, "tree", False, None, before, None, None, None))
    assert "New form controls" in result.text
    controller.reader.read.assert_awaited_once()
    assert pipeline.member.await_count == 1 and pipeline.member.call_args.args[1]["window"] == "Form"
    assert state.anchor.root.root_window == "Form"
    assert state.input_context == WindowIdentity.of(form)
    identity = WindowIdentity.of(launcher)
    assert state.evidence[identity].lifetime is (WindowLifetime.RETIRED if ended else WindowLifetime.RETAINED)
    assert state.evidence[identity].membership is (Knowledge.NO if ended else Knowledge.UNKNOWN)
    with pytest.raises(FixedWindowInvalidated if ended else FixedWindowUnconfirmed):
        controller.reader._selection("application", "Fixture", after, "Launcher", previous=old)


@pytest.mark.parametrize("delivered, duplicate", [(True, False), (False, False), (True, True)])
def test_app_lookup_after_native_only_popup_uses_only_actually_delivered_current_form(delivered, duplicate):
    form, popup = surface("form"), {**surface("popup", accessible=False), "parentState": "unknown"}
    attached = (surface("child", parent="form"),) if duplicate else ()
    controller, app = setup(form, popup, *attached)
    current = directory(form, popup, *attached, focus="form")
    adopt(controller, app, current)
    app._instance.anchor = display_selection(controller, app._instance, current)
    target = member_target(app._instance, form)
    targets = (target, *(member_target(app._instance, item) for item in attached))
    for query in targets:
        remember(controller, app, WindowPart(query))
    app._context = "form"
    set_observation_handler(lambda _event: None)
    try:
        pending = publish(
            controller,
            app,
            "Form controls",
            directory=current,
            trees=tuple(
                (query, SimpleNamespace(roots=(), refs={}, native=SimpleNamespace(issues=()))) for query in targets
            ),
        )
        if delivered:
            observation_delivered(pending)
        fixed = app.window(ref(app, "popup"))
        original = fixed._window_identity
        fixed._context = "popup"
        visual = directory(
            *({**item, "node": None, "accessibility": False} for item in (form, popup, *attached)), focus="form"
        )
        adopt(controller, app, visual)
        popup_observation = publish(controller, fixed, "Popup pixels", directory=visual)
        observation_delivered(popup_observation)
        queries = []

        async def lookup(request, _control):
            root = controller._query_root(request.owner)
            queries.append(root.root_window)
            return {"binding": replace(root, node="duration-control")}

        controller.interfaces.lookup = SimpleNamespace(arun=AsyncMock(side_effect=lookup))
        if delivered and not duplicate:
            result = asyncio.run(controller.interfaces.element(app, "30 minutes", 0, None))
            assert result["binding"].root_window == "form"
            assert queries == ["form"]
        else:
            with pytest.raises(ComputerUseError) as caught:
                asyncio.run(controller.interfaces.element(app, "30 minutes", 0, None))
            assert caught.value.code == ("ambiguous_target" if duplicate else "observation_incomplete")
            assert queries == (["form", "child"] if duplicate else [])
        assert fixed._window_identity == original
        with pytest.raises(ComputerUseError) as caught:
            asyncio.run(controller.interfaces.element(fixed, "30 minutes", 0, None))
        assert caught.value.code == "observation_incomplete"
    finally:
        set_observation_handler(None)


def test_app_lookup_reselects_a_delivered_new_lifetime_in_the_same_window_slot_without_rebinding_old_window():
    controller, app = setup(surface("slot"))
    fixed = app.window(ref(app, "slot"))
    original = fixed._window_identity
    old_identity = WindowIdentity.of(surface("slot"))
    fresh = surface("slot", scope="new-lifetime")
    current = directory(fresh, focus="slot")
    adopt(controller, app, current)
    target = member_target(app._instance, fresh)
    remember(controller, app, WindowPart(target))
    # A retained observation source can name the prior generation while the
    # caller has also received the current generation's semantic query root.
    publish(
        controller,
        app,
        "Current slot content",
        directory=current,
        trees=((target, SimpleNamespace(roots=(), refs={}, native=SimpleNamespace(issues=()))),),
        lookup_context=LookupContext(old_identity),
    )
    controller.interfaces.lookup = SimpleNamespace(
        arun=AsyncMock(return_value={"binding": replace(target, node="current-control")})
    )
    result = asyncio.run(controller.interfaces.element(app, "current input", 0, None))
    assert result["binding"].surface == fresh["surface"]
    assert fixed._window_identity == original
    assert app._instance.evidence[old_identity].lifetime is WindowLifetime.RETIRED
    with pytest.raises(ComputerUseError) as caught:
        asyncio.run(controller.interfaces.element(fixed, "current input", 0, None))
    assert caught.value.code == "stale_target"
