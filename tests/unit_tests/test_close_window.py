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


@pytest.mark.parametrize("transition", ["already-absent", "during-collection", "attached-confirmation"])
def test_close_observes_exact_document_family_without_expanding_other_windows(monkeypatch, transition):
    from contextlib import nullcontext

    from computer_use.interaction.read.model import AppObservation
    from computer_use.interaction.read.presentation.document import DisplayDocument
    from computer_use.interaction.read.views.pipeline import WindowPart, member_target
    from computer_use.interaction.write.request.observation import WriteWithObservation
    from computer_use.session.observation_data import observation_data
    from unit_tests.test_dynamic_observation import tree

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
        return WindowPart(member_target(app._instance, item), tree=tree(item, "Captured window"))

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
    pipeline.preparation.run = lambda _value, _control: SimpleNamespace(roots=())
    receipt = {"status": "dispatched", "detail": "One close requested."}
    body = SimpleNamespace(contract=SimpleNamespace(effects=frozenset()), arun=AsyncMock(return_value=receipt))
    suffix = WriteWithObservation(controller, body)

    result = asyncio.run(suffix.run_observed(request, Control()))

    body.arun.assert_awaited_once()
    controller.reader.directory.applications.assert_not_awaited()
    assert result.receipt is receipt
    unrelated = {"b", "c", "other-sheet", "unrelated-menu"}
    assert set(reads).isdisjoint(unrelated)
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
    from computer_use.interaction.write.request.observation import WriteWithObservation

    a, companion, peer = surface("a", accessible=False), surface("child", parent="a"), surface("b")
    controller, app = setup(a, companion, peer)
    owner = window_scope(controller, app, ref(app, "a"))
    request = ExecuteAction(owner, Intent("closeWindow"), owner._window_identity)
    controller.port.calls.limit = lambda _: nullcontext()
    controller.transport.retain_session = lambda _: nullcontext()
    stale = ComputerUseError(
        {"code": "stale_target", "effect": "none", "target": "native request", "detail": "target process exited or changed"}
    )
    last_seen = directory(a, companion, peer)
    controller.reader.directory._query = AsyncMock(
        side_effect=[last_seen, stale] if stage == "recheck" else [stale]
    )
    lifetime_catalogue = {
        "session": "other-session" if proof == "other-session" else "session",
        "endedApplications": ["editor", "another-app"] if proof != "other-incarnation" else ["another-app"],
        "applications": [{"id": "editor:replacement", "displayName": "Editor", "isRunning": True}],
    }
    controller.reader.directory.applications = AsyncMock(
        return_value=lifetime_catalogue, side_effect=TimeoutError("directory budget") if proof == "timeout" else None,
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
