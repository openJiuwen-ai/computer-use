"""Startup window choices retain their evidence without obsolete error fields."""

import asyncio
import sys
from contextvars import ContextVar
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.read.app import NativeAppReader
from computer_use.interaction.read.app_read.model import ReadCall, ReadRequest, _StartupReceipt
from computer_use.interaction.read.app_read.pipeline import AppReadPreparationPipeline
from computer_use.interaction.read.model import WindowSelectionRequired
from computer_use.interaction.write.pipeline import WritePipeline
from computer_use.native.contracts import Binding
from computer_use.native.query.directory import SurfaceReadProfile
from computer_use.pipeline import Control
from regression.test_compat_d34_read_contracts import directory, window
from regression.test_compat_observation_budget_delivery import Port


pytestmark = pytest.mark.usefixtures("native_read")


def test_startup_window_choice_preserves_directory_and_launch_receipt():
    impact = {"attempts": []}
    error = ComputerUseError(
        {
            "code": "ambiguous_target", "effect": "none", "target": "window",
            "detail": "Several windows are available.", "impact": impact,
        }
    )
    directory = {"application": "instance:fixture", "surfaces": [{"window": "one"}, {"window": "two"}]}
    launch = {
        "application": {"id": "instance:fixture", "displayName": "Fixture"},
        "dispatch": "dispatched",
        "detail": "The selected application started.",
    }

    choice = WindowSelectionRequired(error, "instance:fixture", "Fixture", directory, launch)
    assert choice.impact is impact
    assert choice.__cause__ is error
    annotated = NativeAppReader._after_launch(choice, launch)

    assert isinstance(annotated, WindowSelectionRequired)
    assert annotated.application == "instance:fixture"
    assert annotated.directory is directory
    assert annotated.launch is launch
    assert annotated.code == "ambiguous_target"
    assert annotated.effect == "possible"
    assert annotated.impact is impact
    assert "The selected application started." in annotated.detail
    assert "Several windows are available." in annotated.detail


def test_first_read_startup_preserves_window_choice_impact_after_launch(monkeypatch):
    port = Port(SimpleNamespace(now=100.0), expired_at="none")
    port.connect = AsyncMock(return_value=Binding("session", None, None, None))
    reader = NativeAppReader(port)
    installed = {
        "id": "installed", "displayName": "Fixture", "isRunning": False,
        "identifier": "fixture.app", "path": "/Applications/Fixture.app",
    }
    running = {"id": "application", "displayName": "Fixture", "isRunning": True}
    choices = directory(window("one"), window("two"))
    reader.directory.collect = AsyncMock(side_effect=[
        {"session": "session", "applications": [installed], "stopReasons": []}, choices,
    ])
    reader._startup_directory = AsyncMock(return_value=(running, choices))
    impact = {"attempts": [{"dispatch": "dispatched", "cleanup": "complete"}]}
    original = ComputerUseError({
        "code": "ambiguous_target", "effect": "none", "target": "window",
        "detail": "Several windows are available.", "impact": impact,
    })
    reader.select_working_window = Mock(side_effect=original)
    launch = {
        "application": running,
        "dispatch": "dispatched",
        "detail": "The selected application started.",
    }
    dispatch = AsyncMock(return_value=launch)
    monkeypatch.setattr(WritePipeline, "run_in_call", dispatch)

    with pytest.raises(WindowSelectionRequired) as caught:
        asyncio.run(reader.read("Fixture", view="tree", overview=False))

    error = caught.value
    assert error.application == "application" and error.directory is choices
    assert error.launch["dispatch"] == "dispatched" and error.launch["application"] == running
    assert error.effect == "possible" and error.code == "ambiguous_target"
    assert error.impact is impact
    assert isinstance(error.__cause__, WindowSelectionRequired)
    assert error.__cause__.__cause__ is original
    assert "The selected application started." in error.detail
    assert "Several windows are available." in error.detail
    dispatch.assert_awaited_once()
    assert port.finished == ["1", "2"] and not port.calls.busy
    running["displayName"] = "Changed outside the saved receipt"
    assert error.launch["application"]["displayName"] == "Fixture"


@pytest.mark.parametrize("outcome", ["choice", "cancelled", "fixed"])
def test_window_choice_keeps_active_error_and_original_task_and_budget(outcome):
    port = Port(SimpleNamespace(now=100.0), expired_at="none")
    port.connect = AsyncMock(return_value=Binding("session", None, None, None))
    reader = NativeAppReader(port)
    running = {"id": "application", "displayName": "Fixture", "isRunning": True}
    surfaces = directory(window("one"), window("two"))
    catalogue = {"session": "session", "applications": [running], "stopReasons": []}
    source = ReadRequest(
        "application", window="one" if outcome == "fixed" else None,
        view="image", overview=False, source_applications=catalogue,
    )
    reader.directory.collect = AsyncMock(return_value=surfaces)
    original = ComputerUseError({
        "code": "ambiguous_target", "effect": "none", "target": "window", "detail": "Several windows",
    })
    reader.select_working_window = Mock(side_effect=original)
    choice = WindowSelectionRequired(original, "application", "Fixture", surfaces)
    cancellation = asyncio.CancelledError("caller cancelled while resolving choices")
    context = ContextVar("read_context")
    events = []

    async def run():
        token = context.set("same context")
        task = asyncio.current_task()

        async def choices(error, application, name, observed, budget, *, _control, include_overview, profile):
            assert sys.exception() is error is original
            assert asyncio.current_task() is task and context.get() == "same context"
            assert (application, name) == ("application", "Fixture") and observed is surfaces
            assert budget is remaining and not include_overview and profile is SurfaceReadProfile.VISUAL
            assert _control.path[-1] == "SelectWorkingScope" and _control.call_id == "original call"
            await asyncio.sleep(0)
            assert sys.exception() is original
            if outcome == "cancelled":
                raise cancellation
            return choice

        reader._window_choices = AsyncMock(side_effect=choices)
        try:
            with port.calls.claim(1000) as remaining:
                call = ReadCall(source, remaining, _StartupReceipt())
                control = Control(remaining=remaining, diagnostic=events.append, call_id="original call")
                return await AppReadPreparationPipeline(reader).arun(call, control)
        finally:
            context.reset(token)

    expected = {"choice": choice, "fixed": original, "cancelled": cancellation}[outcome]
    with pytest.raises(type(expected)) as caught:
        asyncio.run(run())
    assert caught.value is expected
    if outcome == "fixed":
        reader._window_choices.assert_not_awaited()
    else:
        reader._window_choices.assert_awaited_once()
        assert (expected.__context__ if outcome == "cancelled" else expected.__cause__) is original
    assert [e.path[-1] for e in events if e.phase == "enter" and e.path[-1] in {
        "ResolveApplicationIdentity", "CollectApplicationSurfaces", "SelectWorkingScope",
    }] == ["ResolveApplicationIdentity", "CollectApplicationSurfaces", "SelectWorkingScope"]
    assert reader.directory.collect.await_args.args[1] == {"kind": "surfaces", "application": "application"}
    assert port.finished == ["1"] and not port.calls.busy


@pytest.mark.parametrize("end", ["empty", "partial", "stale"])
def test_startup_observation_keeps_the_five_round_budget_and_original_stale_error(monkeypatch, end):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr("computer_use.native.lifecycle.time.monotonic", lambda: clock.now)
    port = Port(clock, expired_at="none")
    port.connect = AsyncMock(return_value=Binding("session", None, None, None))
    port.connection.capabilities.add("nativeVisualSurfaceQuery")
    reader = NativeAppReader(port)
    starting = {"id": "application", "displayName": "Fixture", "isRunning": True}
    recipe = {"id": "installed", "displayName": "Fixture", "isRunning": False}
    catalogue = {"session": "session", "applications": [recipe], "stopReasons": []}
    expected = directory(reasons=["coverage is partial"] if end == "partial" else [])
    stale = ComputerUseError({
        "code": "stale_target", "effect": "none", "target": "application", "detail": "Not ready",
    })
    sleeps, queries, diagnostics = [], [], []
    ordinary_sleep = asyncio.sleep

    async def run():
        task = asyncio.current_task()

        async def sleep(delay):
            assert asyncio.current_task() is task
            sleeps.append(delay)
            await ordinary_sleep(0)

        async def collect(read, query, *, _control):
            assert asyncio.current_task() is task and read.remaining() == 250
            queries.append((query, _control.attempt, _control.path[-1]))
            if query["kind"] == "applications":
                return catalogue
            if end == "stale":
                raise stale
            return directory() if end == "partial" and _control.attempt == 1 else expected

        monkeypatch.setattr(asyncio, "sleep", sleep)
        reader.directory.collect = collect
        with port.calls.claim(250) as remaining:
            return await reader._startup_directory(
                starting, "session", remaining, launched=True,
                installation="installed" if end == "stale" else None,
                profile=SurfaceReadProfile.VISUAL,
                _control=Control(remaining=remaining, diagnostic=diagnostics.append, call_id="startup"),
            )

    if end == "stale":
        with pytest.raises(ComputerUseError) as caught:
            asyncio.run(run())
        assert caught.value is stale
    else:
        application, result = asyncio.run(run())
        assert application is starting and result is expected

    count = 2 if end == "partial" else 5
    assert sleeps == [0.1, 0.2, 0.25, 0.25][:count - 1]
    assert [(attempt, stage) for _, attempt, stage in queries if stage == "CollectStartupSurfaces"] == [
        (i, "CollectStartupSurfaces") for i in range(1, count + 1)
    ]
    assert all(query == {"kind": "surfaces", "application": "application", "profile": "visual"}
               for query, _, stage in queries if stage == "CollectStartupSurfaces")
    assert [e.attempt for e in diagnostics if e.phase == "attempt"] == list(range(1, count + 1))
    assert port.finished == [str(i) for i in range(1, count + 1)] and not port.calls.busy
