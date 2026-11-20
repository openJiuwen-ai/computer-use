"""Normal close follows the selected window's declaration, never focus or pixels."""

import asyncio
from contextlib import asynccontextmanager, nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from regression.test_application_window_lifetimes import directory, ref, setup, surface
from regression.test_text_input_routes import RecordingPort, observed

from computer_use.errors import ComputerUseError
from computer_use.interaction.agent.scope import window_scope
from computer_use.interaction.query.instances.scope import adopt
from computer_use.interaction.requests import ExecuteAction
from computer_use.interaction.write.engine import Engine
from computer_use.interaction.write.request.model import ActionPlan
from computer_use.interaction.write.request.observation import WriteWithObservation
from computer_use.interaction.write.request.passes.resolve_window_close import create
from computer_use.interaction.write.request.pipeline import ExecuteNativeWrite
from computer_use.native.contracts import (
    Binding,
    Channel,
    Dispatch,
    HostPolicy,
    Intent,
    Knowledge,
    Primitive,
    StepResult,
)
from computer_use.pipeline.core import Control
from computer_use.session.observation import CapturedTextDocument, lazy_observation
from computer_use.session.observation_data import retain_observation_data

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


def resolve_close_control(query):
    controller, app = setup()
    owner = window_scope(controller, app, ref(app, "a"))
    request = ExecuteAction(owner, Intent("closeWindow"), owner._window_identity)

    async def refresh(_controller, owner):
        adopt(controller, owner, directory({**surface("a"), "node": "root:a"}))

    @asynccontextmanager
    async def opened(root, timeout):
        assert root == ROOT and timeout == 30000
        yield SimpleNamespace(token="lease", remaining=lambda: 500)

    controller.port.platform = "macos"
    controller.port.calls.open = opened
    controller.port.query_relation = query
    with patch("computer_use.interaction.write.request.passes.resolve_window_close.refresh", refresh):
        return asyncio.run(create(controller=controller)(ActionPlan(request, target=request.target), None)).target


def test_close_uses_window_relation_without_tree_or_visible_button_geometry():
    query = AsyncMock(return_value=relation())
    target = resolve_close_control(query)
    assert target == replace(ROOT, node="hidden-close")
    query.assert_awaited_once_with("lease", ROOT, "AXCloseButton", None, 1, 500)


@pytest.mark.parametrize(
    "data",
    [
        relation(state="unsupported", targets=[]),
        relation(state="absent", targets=[]),
        relation(targets=[], total=0),
    ],
)
def test_no_declared_close_does_not_guess_a_shortcut_or_another_window(data):
    query = AsyncMock(return_value=data)
    with pytest.raises(ComputerUseError) as error:
        resolve_close_control(query)
    assert error.value.code == "input_unavailable" and error.value.effect == "none"


@pytest.mark.parametrize(
    "data",
    [
        relation(state="partial"),
        relation(certainty="unknown"),
        relation(targets=["a", "b"], total=2),
        relation(offset=1),
        relation(cursor="next"),
    ],
)
def test_incomplete_close_relation_cannot_authorize_input(data):
    query = AsyncMock(return_value=data)
    with pytest.raises(ComputerUseError, match="could not be confirmed"):
        resolve_close_control(query)


def test_relation_from_another_window_is_not_a_close_target():
    query = AsyncMock(return_value={**relation(), "surface": "surface:b"})
    with pytest.raises(ValueError, match="source or scope"):
        resolve_close_control(query)


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

    steps = []
    controller.port.platform = "macos"

    async def resolve():
        task = asyncio.current_task()

        def remaining():
            assert asyncio.current_task() is task
            steps.append("remaining")
            return 500

        @asynccontextmanager
        async def opened(root, timeout):
            assert root == ROOT and timeout == 30000
            assert asyncio.current_task() is task
            steps.append("open")
            try:
                yield SimpleNamespace(token="lease", remaining=remaining)
            finally:
                assert asyncio.current_task() is task
                steps.append("close")

        async def query(*args):
            assert asyncio.current_task() is task
            assert args == ("lease", ROOT, "AXCloseButton", None, 1, 500)
            steps.append("query")
            await asyncio.sleep(0)
            return relation()

        controller.port.calls.open = opened
        controller.port.query_relation = query
        return await create(controller=controller)(ActionPlan(request, target=request.target), None)

    with patch("computer_use.interaction.write.request.passes.resolve_window_close.refresh", refresh):
        plan = asyncio.run(resolve())
    assert steps == ["open", "remaining", "query", "close"]
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


@pytest.mark.parametrize("confirm", [False, True])
def test_windows_close_dispatches_once_to_exact_window_and_reports_observed_lifetime(confirm):
    controller, app = setup()
    app._instance.input_context = window_scope(controller, app, ref(app, "b")).identity
    a, b = {**surface("a"), "node": ROOT.node}, surface("b")
    current = {**directory(a, b, focus="b"), "platform": "windows"}
    close = Primitive("close", "native_action", Channel.OBJECT, Knowledge.YES, Knowledge.YES, action="Window.Close")
    port = RecordingPort(
        [observed(close, binding=ROOT, platform="windows")],
        [StepResult(ROOT, Dispatch.DISPATCHED, "Close request sent")],
        policy=HostPolicy(allow_global_input=False),
    )
    controller.port.platform = "windows"
    controller.port.calls = port.calls
    controller.transport.retain_session = lambda _: nullcontext()
    controller.engine = Engine(port)

    async def refresh(_controller, owner):
        adopt(controller, owner, current)

    dialog = {**surface("confirm", parent="a"), "modal": True}
    after = directory(a, b, dialog, focus="confirm") if confirm else directory(b, focus="b", ended=(a,))
    observation = lazy_observation(CapturedTextDocument("Save changes?" if confirm else "Remaining window"))
    retain_observation_data(observation, (), (), directory={**after, "platform": "windows"})
    controller.interfaces.read = AsyncMock(return_value=observation)

    with patch("computer_use.interaction.write.request.passes.resolve_window_close.refresh", refresh):
        result = app.close_window(ref(app, "a"))

    assert len(port.commands) == 1
    command = port.commands[0]
    assert command.target == ROOT and command.primitive == close
    assert not command.prepare and not command.foreground and not command.intent.global_input
    assert port.finished == ["operation"]
    assert result.intent == "closeWindow" and result.target == f"Window {ref(app, 'a')}"
    assert result.status == "dispatched" and result.observation is observation
    assert result.window_lifecycle == ("observed" if confirm else "closed")
    controller.interfaces.read.assert_awaited_once()


def test_unimplemented_platform_does_not_fall_back_to_global_close_shortcut():
    controller, app = setup()
    scope = window_scope(controller, app, ref(app, "a"))
    request = ExecuteAction(scope, Intent("closeWindow"), scope._window_identity)
    controller.port.platform = "linux"
    with (
        patch(
            "computer_use.interaction.write.request.passes.resolve_window_close.refresh", new_callable=AsyncMock
        ) as refresh,
        pytest.raises(ComputerUseError) as error,
    ):
        asyncio.run(create(controller=controller)(ActionPlan(request, target=request.target), None))
    assert error.value.code == "input_unavailable" and error.value.effect == "none"
    refresh.assert_not_called()


@pytest.mark.parametrize(
    ("stage", "proof", "lifecycle"),
    [
        ("recheck", "ended", "closed"),
        ("initial", "ended", "closed"),
        ("recheck", "other-incarnation", "unknown"),
        ("recheck", "other-session", "unknown"),
        ("recheck", "timeout", "unknown"),
    ],
)
def test_close_uses_exact_application_retirement_after_stale_family_directory(stage, proof, lifecycle):
    from contextlib import nullcontext

    from computer_use.interaction.read.acquisition.components.support import ObservationGap
    from computer_use.interaction.read.views.pipeline import WindowPart, member_target

    a, companion, peer = surface("a", accessible=False), surface("child", parent="a"), surface("b")
    controller, app = setup(a, companion, peer)
    owner = window_scope(controller, app, ref(app, "a"))
    request = ExecuteAction(owner, Intent("closeWindow"), owner._window_identity)
    controller.port.calls.limit = lambda _: nullcontext()
    controller.transport.retain_session = lambda _: nullcontext()
    stale = ComputerUseError(
        {
            "code": "stale_target",
            "effect": "none",
            "target": "native request",
            "detail": "target process exited or changed",
        }
    )
    last_seen = directory(a, companion, peer)
    controller.reader.directory._query = AsyncMock(side_effect=[last_seen, stale] if stage == "recheck" else [stale])
    lifetime_catalogue = {
        "session": "other-session" if proof == "other-session" else "session",
        "endedApplications": ["editor", "another-app"] if proof != "other-incarnation" else ["another-app"],
        "applications": [{"id": "editor:replacement", "displayName": "Editor", "isRunning": True}],
    }
    controller.reader.directory.applications = AsyncMock(
        return_value=lifetime_catalogue,
        side_effect=TimeoutError("directory budget") if proof == "timeout" else None,
    )
    controller.interfaces.windows.register_directory(directory(surface("unrelated"), application="another-app"))

    async def read_member(_ui, item, _view, **_kwargs):
        assert item["window"] in {"a", "child"}
        return WindowPart(
            member_target(app._instance, item),
            gaps=(ObservationGap("tree", "stale_target", stale.detail),),
        )

    controller.interfaces.read_pipeline.member = AsyncMock(side_effect=read_member)
    receipt = {"status": "dispatched", "detail": "AX close invoked."}
    body = SimpleNamespace(contract=SimpleNamespace(effects=frozenset()), arun=AsyncMock(return_value=receipt))

    result = asyncio.run(WriteWithObservation(controller, body).run_observed(request, Control()))

    body.arun.assert_awaited_once()
    assert result.receipt is receipt
    assert result.window_lifecycle == lifecycle
    assert not result.choices
    assert result.observation is None
    assert controller.reader.directory._query.await_count == (2 if stage == "recheck" else 1)
    controller.reader.directory.applications.assert_awaited_once_with(include_ended=True)
    windows = controller.interfaces.windows
    assert windows.application_ended("session", "editor") is (lifecycle == "closed")
    assert not windows.application_ended("session", "another-app")
    assert not windows.application_ended("session", "editor:replacement")
