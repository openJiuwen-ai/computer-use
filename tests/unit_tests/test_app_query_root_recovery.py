# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A closing popup's AX proxy cannot permanently pin an App name query."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.query.instances.scope import adopt, display_selection
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.views.pipeline import WindowPart, member_target, remember
from computer_use.interaction.read.views.publication import publish
from computer_use.native.contracts import Binding
from computer_use.native.query.path import TargetQuery
from computer_use.native.scopes import QueryRootUnavailable
from computer_use.protocol.observation.adapters import decode_query_facts
from computer_use.session.events import observation_delivered, set_observation_handler
from computer_use.session.interfaces import WindowIdentity, WindowLifetime
from computer_use.session.operations import current_operation_budget, operation_budget
from unit_tests.test_app_current_scope_recovery import rendered_tree
from unit_tests.test_application_window_lifetimes import directory, ref, setup, surface


def popup_context():
    form, popup = surface("form"), {**surface("popup"), "parentState": "unknown"}
    controller, app = setup(form, popup)
    current = directory(form, popup, focus="form")
    adopt(controller, app, current)
    app._instance.anchor = display_selection(controller, app._instance, current)
    targets = tuple(member_target(app._instance, item) for item in (form, popup))
    for target in targets:
        remember(controller, app, WindowPart(target))
    rendered = tuple((target, *rendered_tree(target)) for target in targets)
    app._context = "popup"
    publish(
        controller,
        app,
        DisplayDocument.join(document for _, _, document in rendered),
        directory=current,
        trees=tuple((target, snapshot) for target, snapshot, _ in rendered),
    )
    fixed = app.window(ref(app, "popup"))
    return controller, app, fixed, form, popup, targets


@pytest.mark.parametrize("same_window", [False, True])
def test_app_refreshes_once_after_failed_root_and_preserves_fixed_popup_identity(same_window):
    controller, app, fixed, form, popup, targets = popup_context()
    original = fixed._window_identity
    fresh_popup = (
        {**popup, "node": "popup-new-proxy"} if same_window else {**popup, "node": None, "accessibility": False}
    )
    after = directory(form, fresh_popup, focus="popup" if same_window else "form")
    budgets = []

    async def directory_query(*_args):
        budgets.append(current_operation_budget())
        return after

    controller.reader.directory._query = AsyncMock(side_effect=directory_query)
    controller.engine.execute = AsyncMock()
    seen, controls = [], []
    control = object()

    async def lookup(request, actual_control):
        budgets.append(current_operation_budget())
        root = controller._query_root(request.owner)
        seen.append(root)
        controls.append(actual_control)
        assert request.timeout_ms == 0
        if len(seen) == 1:
            raise QueryRootUnavailable(root, native_domain="AXError", native_code=-25202)
        return {"binding": replace(root, node="repeat-control")}

    controller.interfaces.lookup = SimpleNamespace(arun=AsyncMock(side_effect=lookup))

    async def run():
        with operation_budget() as budget:
            result = await controller.interfaces.element(app, "Never", 0, control)
            assert len(budgets) == 3 and all(value is budget for value in budgets)
            return result

    result = asyncio.run(run())
    assert seen[0] == targets[1]
    assert seen[1].root_window == ("popup" if same_window else "form")
    assert seen[1].node == ("popup-new-proxy" if same_window else targets[0].node)
    assert result["binding"].root_window == seen[1].root_window
    assert controls == [control, control]
    controller.reader.directory._query.assert_awaited_once()
    controller.engine.execute.assert_not_awaited()
    assert fixed._window_identity == original
    assert app._instance.evidence[WindowIdentity.of(popup)].lifetime is WindowLifetime.RETAINED


@pytest.mark.parametrize("kind", ["fixed", "branch", "second_root_failure", "budget", "cancel", "foreign"])
def test_fixed_window_other_incompleteness_and_exhaustion_do_not_authorize_unbounded_recovery(kind):
    controller, app, fixed, form, popup, _targets = popup_context()
    fixed._lookup_context = app._instance.lookup_context
    after = directory(form, {**popup, "node": None, "accessibility": False}, focus="form")
    controller.reader.directory._query = AsyncMock(return_value=after)
    if kind == "budget":
        controller.reader.directory._query.side_effect = TimeoutError("original operation budget exhausted")
    if kind == "cancel":
        controller.reader.directory._query.side_effect = asyncio.CancelledError()
    errors = []

    async def lookup(request, _control):
        root = controller._query_root(request.owner)
        error = (
            ComputerUseError(
                {
                    "code": "observation_incomplete",
                    "effect": "none",
                    "target": "element path",
                    "detail": "Required native relation is incomplete: children",
                }
            )
            if kind == "branch"
            else QueryRootUnavailable(
                replace(root, session="foreign") if kind == "foreign" else root,
                native_domain="AXError",
                native_code=-25202,
            )
        )
        errors.append(error)
        raise error

    controller.interfaces.lookup = SimpleNamespace(arun=AsyncMock(side_effect=lookup))
    expected = TimeoutError if kind == "budget" else asyncio.CancelledError if kind == "cancel" else ComputerUseError
    with pytest.raises(expected) as caught:
        asyncio.run(controller.interfaces.element(fixed if kind == "fixed" else app, "Never", 0, None))
    if kind not in ("budget", "cancel"):
        assert caught.value is errors[-1]
    assert controller.reader.directory._query.await_count == (0 if kind in ("fixed", "branch", "foreign") else 1)
    assert len(errors) == (2 if kind == "second_root_failure" else 1)


def test_captured_but_unshown_or_pending_tree_does_not_grant_query_scope():
    controller, app = setup(surface("form"))
    target = member_target(app._instance, surface("form"))
    snapshot, shown = rendered_tree(target)
    app._context = "form"
    set_observation_handler(lambda _event: None)
    try:
        unshown = publish(controller, app, "Image only", trees=((target, snapshot),), directory=app._instance.directory)
        observation_delivered(unshown)
        assert not app._instance.delivered_lookup_windows
        pending = publish(controller, app, shown, trees=((target, snapshot),), directory=app._instance.directory)
        assert not app._instance.delivered_lookup_windows
        observation_delivered(pending)
        assert app._instance.delivered_lookup_windows == {WindowIdentity.of(surface("form"))}
    finally:
        set_observation_handler(None)


@pytest.mark.parametrize("relationship", ["known", "unknown", "independent"])
def test_app_display_uses_one_confirmed_window_family_without_guessing_unrelated_peers(relationship):
    form = surface("form")
    child = surface("child", parent="form") if relationship == "known" else surface("child")
    if relationship == "unknown":
        child["parentState"] = "unknown"
    controller, app = setup(form, child)
    app._instance.anchor = None
    current = directory(form, child, focus=None)
    adopt(controller, app, current)
    selected = display_selection(controller, app._instance, current)
    assert (selected.root.root_window if selected is not None else None) == (
        "form" if relationship == "known" else None
    )


def test_recorded_invalid_root_is_classified_structurally_without_weakening_children_checks():
    payload = json.loads((Path(__file__).parents[1] / "fixtures/recorded/live9-invalid-popup-root.json").read_text())
    facts = decode_query_facts(payload)

    class RecordedPort:
        platform = "macos"
        capabilities = ("nativeQueryActionProfile",)
        query_nodes = AsyncMock(return_value=facts)

    root = Binding(facts["session"], "captured-instance", "6", facts["surface"], facts["roots"][0])
    with pytest.raises(QueryRootUnavailable) as caught:
        asyncio.run(TargetQuery(RecordedPort(), "lease", root, lambda: 1000).resolve([{"name": "Never"}]))
    assert caught.value.binding == root
    assert (caught.value.native_domain, caught.value.native_code) == ("AXError", -25202)


def test_invalid_descendant_relation_is_still_incomplete_not_a_root_recovery_license():
    from unit_tests.test_lookup_query_profile import PathPort

    class BrokenBranch(PathPort):
        async def query_nodes(self, *args, **options):
            batch = await super().query_nodes(*args, **options)
            for relation in batch["relations"]:
                if relation["source"] == "button" and relation["property"] == "AXChildren":
                    relation.update(state="error", certainty="unknown", detail="AXError -25202")
            return batch

    with pytest.raises(ComputerUseError) as caught:
        asyncio.run(BrokenBranch().find(["Never"]))
    assert caught.value.code == "observation_incomplete"
    assert not isinstance(caught.value, QueryRootUnavailable)
