# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Close follows the selected window and keeps one native dispatch."""

import asyncio
import sys
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.agent.scope import window_scope
from computer_use.interaction.requests import ExecuteAction
from computer_use.interaction.write.request.observation import WriteWithObservation
from computer_use.native.contracts import Intent
from computer_use.native.lifecycle import CallLifecycle
from computer_use.native.scopes import FixedWindowInvalidated, FixedWindowUnconfirmed
from computer_use.pipeline.core import Contract, Control
from computer_use.session.operations import operation_budget

from compat_application_fixtures import directory, ref, setup, surface
from compat_read_fixtures import children, element, snapshot


@pytest.mark.parametrize(
    ("scenario", "reads", "lifecycle"),
    [
        ("native_gap", 2, "closed"),
        ("fixed_gap", 2, "closed"),
        ("persistent_gap", 2, "unknown"),
        ("first_confirmed", 1, "closed"),
        ("other_binding", 1, "unknown"),
        ("local_stale", 1, "unknown"),
        ("retired_binding", 1, "unknown"),
        ("changed_session", 1, "unknown"),
        ("deadline", 1, "unknown"),
        ("macos", 1, "unknown"),
        ("linux", 1, "unknown"),
        ("different_intent", 1, "unknown"),
    ],
)
def test_windows_close_rechecks_only_an_unconfirmed_retained_lifetime(scenario, reads, lifecycle):
    controller, app = setup()
    owner = window_scope(controller, app, ref(app, "a"))
    other = window_scope(controller, app, ref(app, "b"))
    owner._strict_window = True
    target = owner._window_identity
    intent = Intent("key" if scenario == "different_intent" else "closeWindow")
    request = ExecuteAction(owner, intent, target)
    controller.port.platform = scenario if scenario in ("macos", "linux") else "windows"
    controller.port.calls = CallLifecycle(controller.port)
    controller.transport.retain_session = lambda _: nullcontext()
    body = SimpleNamespace(
        contract=Contract(ExecuteAction, dict, frozenset()),
        arun=AsyncMock(return_value={"status": "dispatched", "detail": "Native object operation returned."}),
    )
    pending = ComputerUseError(
        {
            "code": "stale_target",
            "effect": "none",
            "target": "request",
            "detail": "the observed running application no longer has an available window",
        },
        method="inspect",
    )
    ended_directory = {**directory(ended=(surface("a"),)), "platform": "windows"}
    confirmed = FixedWindowInvalidated(target, "The fixed working window lifetime ended.", directory=ended_directory)
    first = {
        "fixed_gap": FixedWindowUnconfirmed(target),
        "first_confirmed": confirmed,
        "other_binding": FixedWindowUnconfirmed(other._window_identity),
        "local_stale": ComputerUseError(
            {
                "code": "stale_target",
                "effect": "none",
                "target": "client",
                "detail": "local stale binding",
            }
        ),
    }.get(scenario, pending)

    async def observe():
        caller = asyncio.current_task()
        with operation_budget(1000) as budget:

            async def read(*_args, **_kwargs):
                assert asyncio.current_task() is caller
                if controller.interfaces.read.await_count == 1:
                    if scenario == "deadline":
                        budget._clock = lambda: budget.deadline + 1
                    elif scenario == "retired_binding":
                        owner._instance.retire(owner.identity)
                    elif scenario == "changed_session":
                        controller.transport.session_identity = (2, "replacement")
                    raise first
                # The exact-window confirmation executes inside the original
                # handled read failure, even across a suspended host effect.
                assert sys.exception() is first
                await asyncio.sleep(0)
                assert sys.exception() is first
                raise pending if scenario == "persistent_gap" else confirmed

            controller.interfaces.read = AsyncMock(side_effect=read)
            return await WriteWithObservation(controller, body).arun(request, Control())

    result = asyncio.run(observe())
    assert result.receipt["status"] == "dispatched"
    assert result.window_lifecycle == lifecycle
    assert controller.interfaces.read.await_count == reads
    view = "image" if scenario == "different_intent" else "tree"
    assert all(call.args == (owner, view, False, None) for call in controller.interfaces.read.await_args_list)
    body.arun.assert_awaited_once()

@pytest.mark.parametrize("transition", ["already-absent", "during-collection", "attached-confirmation"])
def test_close_observes_exact_document_family_without_expanding_other_windows(monkeypatch, transition, native_read):
    from computer_use.interaction.read.model import AppObservation
    from computer_use.interaction.read.presentation.document import DisplayDocument
    from computer_use.interaction.read.views.pipeline import WindowPart, member_target
    from computer_use.interaction.write.request.observation import WriteWithObservation
    from computer_use.session.observation_data import observation_data

    a, b, c = (
        {**surface("a"), "title": "Owned document"},
        {**surface("b"), "title": "Unrelated document B"},
        {**surface("c"), "title": "Unrelated document C"},
    )
    sheet = {**surface("sheet", parent="a"), "modal": True, "kind": "sheet", "title": "Save changes?"}
    other_sheet = {**surface("other-sheet", parent="b"), "modal": True, "kind": "sheet"}
    unrelated_menu = {**surface("unrelated-menu", parent_state="unknown"), "kind": "menu"}
    controller, app = setup(a, b, c)
    owner = window_scope(controller, app, ref(app, "a"))
    request = ExecuteAction(owner, Intent("closeWindow"), owner._window_identity)
    controller.port.calls.limit = lambda _: nullcontext()
    controller.transport.retain_session = lambda _: nullcontext()
    confirmation = transition == "attached-confirmation"
    after = directory(a, b, c, sheet, other_sheet, unrelated_menu, focus="sheet") if confirmation else directory(
        b, c, unrelated_menu, focus="b", ended=(a,) if transition == "during-collection" else (),
    )
    initial = directory(a, b, c) if transition == "during-collection" else after
    controller.reader.directory._query = AsyncMock(side_effect=[initial, after, after])
    controller.reader.directory.applications = AsyncMock()
    reads = []
    pipeline = controller.interfaces.read_pipeline

    async def read_member(_ui, item, _view, **_kwargs):
        reads.append(item["window"])
        target = member_target(app._instance, item)
        child = target.node + ":child"
        root = element(target.node, "AXWindow", item["title"])
        root["properties"].append(children([child]))
        tree = snapshot(target, nodes=[root, element(child, "AXButton", "Captured window")])
        return WindowPart(target, tree=tree)

    async def read_app(selection, *, source_directory, **_kwargs):
        key = selection.root.root_window
        selected = sheet if confirmation and key == "a" else app._instance.members[key]
        part = await read_member(app, selected, "tree")
        left = key == "a" and not confirmation
        return AppObservation(
            selection=selection, target=part.target, applications=None, source_directory=source_directory,
            directory=after, tree=None if left else part.tree, image=None, document=DisplayDocument.join(()),
            refs={}, windows={}, gaps=(), scope_confirmed=not left,
        )

    controller.reader.read = AsyncMock(side_effect=read_app)
    pipeline.member = AsyncMock(side_effect=read_member)
    monkeypatch.setattr("computer_use.interaction.read.views.pipeline.remember", lambda *_: None)
    monkeypatch.setattr("computer_use.interaction.read.views.collection.window_content_unexposed", lambda _: False)
    monkeypatch.setattr(
        "computer_use.interaction.read.views.pipeline.tree_document",
        lambda _controller, target, *_a, **_kw: DisplayDocument.join((f"Body of {target.root_window}",)),
    )
    receipt = {"status": "dispatched", "detail": "One close requested."}
    body = SimpleNamespace(contract=SimpleNamespace(effects=frozenset()), arun=AsyncMock(return_value=receipt))
    suffix = WriteWithObservation(controller, body)

    result = asyncio.run(suffix.run_observed(request, Control()))

    body.arun.assert_awaited_once()
    controller.reader.directory.applications.assert_not_awaited()
    assert result.receipt is receipt
    unrelated = {"b", "c", "other-sheet", "unrelated-menu"}
    assert set(reads).isdisjoint(unrelated)
    assert result.observation is not None, result.observation_error
    assert "Unrelated document B" in result.observation.text and "Unrelated document C" in result.observation.text
    assert "Body of b" not in result.observation.text and "Body of c" not in result.observation.text
    metadata = observation_data(result.observation)
    assert {item["window"] for item in metadata["window_coverage"]}.isdisjoint(unrelated)
    if confirmation:
        assert "sheet" in reads and "Body of sheet" in result.observation.text
        assert result.window_lifecycle == "observed"
    else:
        assert not metadata["content_available"]
        assert result.observation_status == "available"
        assert "Other windows are listed without expansion." in result.observation.text
        assert {choice.id for choice in result.choices} == {ref(app, key) for key in ("b", "c", "unrelated-menu")}
        assert result.window_lifecycle == ("closed" if transition == "during-collection" else "unknown")
