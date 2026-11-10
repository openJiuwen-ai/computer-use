"""Startup window choices retain their evidence without obsolete error fields."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.read.app import NativeAppReader
from computer_use.interaction.read.model import WindowSelectionRequired
from computer_use.interaction.write.pipeline import WritePipeline
from computer_use.native.contracts import Binding
from test_d34_read_contracts import directory, window
from test_observation_budget_delivery import Port


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
    monkeypatch.setattr(reader, "select_working_window", Mock(side_effect=original))
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
