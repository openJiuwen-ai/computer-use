# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.read.app import NativeAppReader
from computer_use.interaction.read.model import MenuAppObservation
from computer_use.interaction.read.views import pipeline
from computer_use.native.query.directory import (
    select_application,
    should_lookup_windows_launch,
    supports_windows_launch_lookup,
)
from computer_use.protocol.directory import decode_directory
from computer_use.protocol.execution import decode_inspect_query


def test_windows_launch_lookup_preserves_uncertainty_when_uia_is_partial():
    directory = {
        "platform": "windows",
        "scope": "runningWindows",
        "applications": [],
        "stopReasons": ["UIA desktop enrichment did not finish."],
        "nativeProcessNames": [],
    }
    assert supports_windows_launch_lookup(directory, {"nativeWindowsApplicationLookup"})
    assert not supports_windows_launch_lookup(directory, ())

    with pytest.raises(ComputerUseError) as ordinary:
        select_application(directory, "Example")
    assert ordinary.value.code == "observation_incomplete"
    assert not should_lookup_windows_launch(ordinary.value)
    with pytest.raises(ComputerUseError) as explicit:
        select_application(directory, "Example", allow_windows_lookup=True)
    assert explicit.value.code == "observation_incomplete"
    assert should_lookup_windows_launch(explicit.value)

    directory.pop("nativeProcessNames")
    with pytest.raises(ComputerUseError) as incomplete:
        select_application(directory, "Example", allow_windows_lookup=True)
    assert incomplete.value.code == "observation_incomplete"
    assert should_lookup_windows_launch(incomplete.value)


def test_windows_launch_lookup_reports_exact_choices_for_same_name():
    directory = decode_directory(
        {
            "kind": "applications",
            "platform": "windows",
            "session": "session",
            "observed": {"id": "observation", "startedAt": 1, "completedAt": 2},
            "stopReasons": [],
            "launchSelector": "Example",
            "applications": [
                {
                    "id": f"installation:{package}",
                    "displayName": "Example",
                    "isRunning": False,
                    "path": None,
                    "identifier": f"{package}!App",
                }
                for package in ("First", "Second")
            ],
        }
    )
    with pytest.raises(ComputerUseError) as gated:
        select_application(directory, "Example")
    assert gated.value.code == "invalid_request"
    assert gated.value.effect == "none"
    with pytest.raises(ComputerUseError, match="for this selector"):
        select_application(directory, "Another app", allow_windows_lookup=True)
    with pytest.raises(ComputerUseError) as caught:
        select_application(directory, "Example", allow_windows_lookup=True)
    assert caught.value.code == "ambiguous_target"
    assert caught.value.effect == "none"
    assert "First!App" in caught.value.detail
    assert "Second!App" in caught.value.detail

    for platform in ("macos", "linux"):
        with pytest.raises(ValueError, match="Windows launch candidate scope"):
            decode_directory({**directory, "platform": platform})


def test_windows_launch_lookup_inspect_accepts_only_one_selector():
    query = {"kind": "applications", "launchSelector": "Example"}
    assert decode_inspect_query(query) == query
    with pytest.raises(ValueError, match="single application selector"):
        decode_inspect_query({**query, "installation": "installation:other"})


def test_windows_menu_observation_failure_keeps_the_launch_receipt(monkeypatch):
    directory = {"platform": "windows"}
    launch = {
        "application": {"id": "app:example", "displayName": "Example"},
        "dispatch": "dispatched",
        "detail": "Windows opened Example.",
    }
    observed = MenuAppObservation("app:example", "Example", None, None, directory, {}, None, launch)
    monkeypatch.setattr(pipeline, "resolve", AsyncMock(return_value=(directory, observed)))
    interface = pipeline.InterfaceReadPipeline(SimpleNamespace(reader=NativeAppReader))
    monkeypatch.setattr(interface, "_collect", AsyncMock(side_effect=RuntimeError("menu collection failed")))

    with pytest.raises(ComputerUseError, match="menu collection failed") as error:
        asyncio.run(interface.run(SimpleNamespace(_instance=object()), "tree", False, None))
    assert error.value.launch is launch
    assert error.value.effect == "possible"
