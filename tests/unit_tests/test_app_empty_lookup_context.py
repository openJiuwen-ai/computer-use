"""Historical popup delivery cannot empty and poll the App's current scope."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.diagnostics import local_failure
from computer_use.errors import ComputerUseError
from computer_use.interaction.query.instances.scope import adopt
from computer_use.interaction.read.views.publication import publish
from computer_use.pipeline.core import Control
from computer_use.session.interfaces import LookupContext, WindowIdentity, WindowLifetime
from computer_use.session.operations import OperationBudget, OperationDeadlineExceeded
from unit_tests.test_app_current_scope_recovery import rendered_tree
from unit_tests.test_app_query_root_recovery import popup_context
from unit_tests.test_application_window_lifetimes import directory
from unit_tests.test_member_lookup_scheduling import install_lookup, lookup_fixture


@pytest.mark.parametrize("form_delivered", [True, False])
def test_historical_popup_with_no_current_lookup_context_uses_delivered_form_only(monkeypatch, form_delivered):
    controller, app, fixed, form, popup, targets = popup_context()
    original = fixed._window_identity
    after = directory(form, focus="form")
    adopt(controller, app, after)
    app._context = "popup"
    snapshot, document = rendered_tree(targets[1])
    # The old popup's facts remain readable history; the latest catalogue
    # confirms the form but has no current popup or current input context.
    publish(controller, app, document, directory=after, trees=((targets[1], snapshot),))
    assert app._instance.lookup_context.window is None
    if not form_delivered:
        app._instance.delivered_lookup_windows.discard(WindowIdentity.of(form))
    seen = []

    async def lookup(request, _control):
        root = controller._query_root(request.owner)
        seen.append(root.root_window)
        return {"binding": replace(root, node="hour-combo")}

    controller.interfaces.lookup = SimpleNamespace(arun=AsyncMock(side_effect=lookup))
    controller.engine.execute = AsyncMock()
    sleep = AsyncMock(side_effect=AssertionError("a missing App context must not poll an empty owner list"))
    monkeypatch.setattr("computer_use.interaction.agent.pipeline.asyncio.sleep", sleep)
    if form_delivered:
        found = asyncio.run(controller.interfaces.element(app, "10:00", 3000, None))
        assert found["binding"].root_window == "form" and seen == ["form"]
    else:
        with pytest.raises(ComputerUseError) as caught:
            asyncio.run(controller.interfaces.element(app, "10:00", 3000, None))
        assert caught.value.code == "observation_incomplete" and not seen
    assert fixed._window_identity == original
    evidence = app._instance.evidence[WindowIdentity.of(popup)]
    assert evidence.lifetime is WindowLifetime.RETAINED
    assert evidence.membership.value == "unknown"
    fixed._lookup_context = LookupContext(WindowIdentity.of(popup))
    with pytest.raises(ComputerUseError) as caught:
        asyncio.run(controller.interfaces.element(fixed, "10:00", 3000, None))
    assert caught.value.code == "observation_incomplete"
    controller.engine.execute.assert_not_awaited()
    sleep.assert_not_awaited()


def test_empty_completed_menu_scope_does_not_spin_without_an_owner(monkeypatch):
    controller, app, _fixed, _form, _popup, _targets = popup_context()
    app._instance.lookup_context = LookupContext(None, True, ())
    controller.interfaces.lookup = SimpleNamespace(arun=AsyncMock())
    sleep = AsyncMock(side_effect=AssertionError("there is no native owner to poll"))
    monkeypatch.setattr("computer_use.interaction.agent.pipeline.asyncio.sleep", sleep)
    with pytest.raises(ComputerUseError) as caught:
        asyncio.run(controller.interfaces.element(app, "Missing", 3000, None))
    assert caught.value.code == "element_not_found"
    controller.interfaces.lookup.arun.assert_not_awaited()
    sleep.assert_not_awaited()


def bounded_lookup(monkeypatch, find, *, cancelled=None):
    controller, fixed = lookup_fixture()
    now, sleeps = [0.0], []
    budget = OperationBudget(2000, clock=lambda: now[0])
    control = Control(remaining=budget.remaining, cancelled=cancelled)
    calls = install_lookup(controller, find)

    async def sleep(delay):
        sleeps.append(delay)
        now[0] += delay

    monkeypatch.setattr("computer_use.interaction.agent.pipeline.monotonic", lambda: now[0])
    monkeypatch.setattr("computer_use.interaction.agent.pipeline.asyncio.sleep", sleep)
    return controller, fixed, control, now, sleeps, calls


def test_absent_target_polling_uses_original_completion_budget_not_default_three_seconds(monkeypatch):
    controller, fixed, control, now, sleeps, calls = bounded_lookup(monkeypatch, lambda *_: False)
    with pytest.raises(OperationDeadlineExceeded):
        asyncio.run(controller.interfaces.element(fixed, "Missing", 3000, control))
    assert 1.9 <= now[0] <= 2 and sleeps and calls
    assert max(sleeps) <= 0.025
    assert [key for key, _timeout in calls[:2]] == ["toolbar", "a"]


def test_target_can_still_appear_within_original_budget_and_all_members_are_fair(monkeypatch):
    controller, fixed, control, now, sleeps, calls = bounded_lookup(
        monkeypatch, lambda key, calls: key == "a" and len(calls) >= 4
    )
    result = asyncio.run(controller.interfaces.element(fixed, "Appearing", 3000, control))
    assert result["binding"].root_window == "a"
    assert calls == [("toolbar", 0), ("a", 0), ("toolbar", 0), ("a", 0)]
    assert sleeps == [0.025] and now[0] == 0.025


def test_cancellation_during_python_wait_does_not_start_another_native_lookup(monkeypatch):
    cancelled = [False]
    controller, fixed, control, _now, _sleeps, calls = bounded_lookup(
        monkeypatch, lambda *_: False, cancelled=lambda: cancelled[0]
    )

    async def cancel(_delay):
        cancelled[0] = True

    monkeypatch.setattr("computer_use.interaction.agent.pipeline.asyncio.sleep", cancel)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(controller.interfaces.element(fixed, "Missing", 3000, control))
    assert calls == [("toolbar", 0), ("a", 0)]


def test_budget_expiring_in_one_member_prevents_starting_the_next_member(monkeypatch):
    controller, fixed, control, now, _sleeps, calls = bounded_lookup(monkeypatch, lambda *_: False)

    async def lookup(request, _control):
        root = controller._query_root(request.owner)
        calls.append((root.root_window, request.timeout_ms))
        now[0] = 2
        raise local_failure("not here", "element_not_found")

    controller.interfaces.lookup = SimpleNamespace(arun=AsyncMock(side_effect=lookup))
    with pytest.raises(OperationDeadlineExceeded):
        asyncio.run(controller.interfaces.element(fixed, "Missing", 3000, control))
    assert calls == [("toolbar", 0)]
