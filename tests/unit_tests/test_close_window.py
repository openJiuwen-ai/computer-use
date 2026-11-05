"""Normal close follows the selected window's declaration, never focus or pixels."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.agent.scope import window_scope
from computer_use.interaction.query.instances.scope import adopt
from computer_use.interaction.requests import ExecuteAction
from computer_use.interaction.write.request.model import ActionPlan
from computer_use.interaction.write.request.passes.resolve_window_close import create
from computer_use.interaction.write.request.pipeline import ExecuteNativeWrite
from computer_use.native.contracts import Binding, Intent
from computer_use.native.query.window_close import close_control
from computer_use.pipeline.core import Control
from unit_tests.test_application_window_lifetimes import directory, ref, setup, surface

ROOT = Binding("session", "owner:a", "a", "surface:a", "root:a")


def relation(**updates):
    return {
        "session": ROOT.session,
        "surface": ROOT.surface,
        "platform": "macos",
        "relation": {
            "source": ROOT.node,
            "property": "AXCloseButton",
            "state": "complete",
            "certainty": "confirmed",
            "targets": ["hidden-close"],
            "total": 1,
            "offset": 0,
            **updates,
        },
    }


def test_close_uses_window_relation_without_tree_or_visible_button_geometry():
    native = SimpleNamespace(query_relation=AsyncMock(return_value=relation()))
    target = asyncio.run(close_control(native, "lease", ROOT, lambda: 500))
    assert target == replace(ROOT, node="hidden-close")
    native.query_relation.assert_awaited_once_with("lease", ROOT, "AXCloseButton", None, 1, 500)


@pytest.mark.parametrize(
    "data",
    [
        relation(state="unsupported", targets=[]),
        relation(state="absent", targets=[]),
        relation(targets=[], total=0),
    ],
)
def test_no_declared_close_does_not_guess_a_shortcut_or_another_window(data):
    native = SimpleNamespace(query_relation=AsyncMock(return_value=data))
    with pytest.raises(ComputerUseError) as error:
        asyncio.run(close_control(native, "lease", ROOT, lambda: 500))
    assert error.value.code == "input_unavailable" and error.value.effect == "none"


@pytest.mark.parametrize(
    "data",
    [
        relation(state="partial"),
        relation(certainty="unknown"),
        relation(targets=["a", "b"], total=2),
        relation(offset=1),
    ],
)
def test_incomplete_close_relation_cannot_authorize_input(data):
    native = SimpleNamespace(query_relation=AsyncMock(return_value=data))
    with pytest.raises(ComputerUseError, match="could not be confirmed"):
        asyncio.run(close_control(native, "lease", ROOT, lambda: 500))


def test_relation_from_another_window_is_not_a_close_target():
    native = SimpleNamespace(query_relation=AsyncMock(return_value={**relation(), "surface": "surface:b"}))
    with pytest.raises(ValueError, match="source or scope"):
        asyncio.run(close_control(native, "lease", ROOT, lambda: 500))


@pytest.mark.parametrize("value", [None, True, -1, "1", 1.5])
def test_close_window_requires_one_published_numeric_reference(value):
    controller, app = setup()
    controller.run = AsyncMock()
    with pytest.raises(ComputerUseError):
        app.close_window(value)
    controller.run.assert_not_called()


def test_public_close_pins_a_even_after_observing_b():
    controller, app = setup()
    app._instance.input_context = window_scope(controller, app, ref(app, "b")).identity
    body = AsyncMock(return_value="close-result")
    controller.interfaces.write = SimpleNamespace(arun=body)
    assert app.close_window(ref(app, "a")) == "close-result"
    request = body.await_args.args[0]
    assert request.intent.kind == "closeWindow" and request.target.root_window == "a"
    assert request.target.node is None


def test_close_resolves_current_a_proxy_but_retains_window_receipt_and_observation_target():
    controller, app = setup()
    scope = window_scope(controller, app, ref(app, "a"))
    request = ExecuteAction(scope, Intent("closeWindow"), scope._window_identity)
    refreshed = directory({**surface("a"), "node": "root:a"}, surface("b"), focus="b")

    async def refresh(_controller, owner):
        adopt(controller, owner, refreshed)

    @asynccontextmanager
    async def opened(root, timeout):
        assert root == ROOT
        yield SimpleNamespace(token="lease", remaining=lambda: 500)

    controller.port.platform = "macos"
    controller.port.calls.open = opened
    controller.port.query_relation = AsyncMock(return_value=relation())
    with patch("computer_use.interaction.write.request.passes.resolve_window_close.refresh", refresh):
        plan = asyncio.run(create(controller=controller)(ActionPlan(request, target=request.target), None))
    assert plan.request is request and request.target.node is None
    assert plan.target == replace(ROOT, node="hidden-close") and plan.mechanism == Intent("click")
    controller.engine.execute = AsyncMock(return_value={"status": "dispatched", "detail": "click sent"})
    receipt = asyncio.run(ExecuteNativeWrite(controller).arun(plan, Control()))
    assert controller.engine.execute.await_args.args == (Intent("click"), plan.target)
    assert receipt["intent"] == "closeWindow" and receipt["target"] == "Window 0"


def test_closed_a_does_not_lower_close_to_remaining_b():
    controller, app = setup()
    scope = window_scope(controller, app, ref(app, "a"))
    request = ExecuteAction(scope, Intent("closeWindow"), scope._window_identity)
    controller.port.platform = "macos"
    controller.port.query_relation = AsyncMock()

    async def refresh(_controller, owner):
        adopt(controller, owner, directory(surface("b"), focus="b", ended=(surface("a"),)))

    with (
        patch("computer_use.interaction.write.request.passes.resolve_window_close.refresh", refresh),
        pytest.raises(ComputerUseError) as error,
    ):
        asyncio.run(create(controller=controller)(ActionPlan(request, target=request.target), None))
    assert error.value.code == "stale_target" and error.value.effect == "none"
    controller.port.query_relation.assert_not_called()


@pytest.mark.parametrize("platform", ["windows", "linux"])
def test_unimplemented_platform_does_not_fall_back_to_global_close_shortcut(platform):
    controller, app = setup()
    scope = window_scope(controller, app, ref(app, "a"))
    request = ExecuteAction(scope, Intent("closeWindow"), scope._window_identity)
    controller.port.platform = platform
    with patch(
        "computer_use.interaction.write.request.passes.resolve_window_close.refresh", new_callable=AsyncMock
    ) as refresh, pytest.raises(ComputerUseError) as error:
        asyncio.run(create(controller=controller)(ActionPlan(request, target=request.target), None))
    assert error.value.code == "input_unavailable" and error.value.effect == "none"
    refresh.assert_not_called()
