"""Observed member scopes share a lookup deadline rather than serial waits."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.diagnostics import local_failure
from computer_use.errors import ComputerUseError
from unit_tests.test_application_alias_lookup import observed_query_roots
from unit_tests.test_application_window_lifetimes import ref, setup, surface


def lookup_fixture():
    controller, app = setup(surface("toolbar", parent="a"), surface("a"))
    observed_query_roots(controller, app)
    return controller, app.window(ref(app, "a"))


def install_lookup(controller, find):
    calls = []

    async def lookup(request, _control):
        binding = controller._query_root(request.owner)
        calls.append((binding.root_window, request.timeout_ms))
        if not find(binding.root_window, calls):
            raise local_failure("not here", "inspect scope", "element_not_found")
        return {"binding": replace(binding, node="matched-control")}

    controller.interfaces.lookup = SimpleNamespace(arun=AsyncMock(side_effect=lookup))
    return calls


def test_toolbar_miss_does_not_wait_before_searching_observed_form(monkeypatch):
    controller, window = lookup_fixture()
    calls = install_lookup(controller, lambda key, _: key == "a")
    delay = AsyncMock(side_effect=AssertionError("unnecessary wait"))
    monkeypatch.setattr("computer_use.interaction.agent.pipeline.asyncio.sleep", delay)

    result = asyncio.run(controller.interfaces.element(window, "Meeting repeat", 3000, None))

    assert result["binding"].root_window == "a"
    assert calls == [("toolbar", 0), ("a", 0)]
    delay.assert_not_awaited()


def test_fair_scan_still_rejects_matches_in_both_member_windows():
    controller, window = lookup_fixture()
    calls = install_lookup(controller, lambda *_: True)

    with pytest.raises(ComputerUseError) as caught:
        asyncio.run(controller.interfaces.element(window, "Duplicate", 3000, None))

    assert caught.value.code == "ambiguous_target"
    assert calls == [("toolbar", 0), ("a", 0)]


def test_waiting_rechecks_all_members_when_target_has_not_appeared(monkeypatch):
    controller, window = lookup_fixture()
    calls = install_lookup(controller, lambda key, calls: key == "a" and len(calls) >= 4)
    delay = AsyncMock()
    monkeypatch.setattr("computer_use.interaction.agent.pipeline.asyncio.sleep", delay)

    result = asyncio.run(controller.interfaces.element(window, "Late control", 3000, None))

    assert result["binding"].root_window == "a"
    assert calls == [("toolbar", 0), ("a", 0), ("toolbar", 0), ("a", 0)]
    delay.assert_awaited_once()


def test_zero_timeout_still_searches_each_scope_once():
    controller, window = lookup_fixture()
    calls = install_lookup(controller, lambda *_: False)

    with pytest.raises(ComputerUseError) as caught:
        asyncio.run(controller.interfaces.element(window, "Missing", 0, None))

    assert caught.value.code == "element_not_found"
    assert calls == [("toolbar", 0), ("a", 0)]
