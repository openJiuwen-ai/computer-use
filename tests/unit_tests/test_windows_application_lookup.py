# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest
from computer_use.errors import ComputerUseError
from computer_use.native.query.directory import select_application, supports_windows_launch_lookup
from computer_use.protocol.directory import decode_directory
from computer_use.protocol.execution import decode_inspect_query


def test_windows_launch_lookup_uses_complete_win32_scope_when_uia_is_partial():
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
    with pytest.raises(ComputerUseError) as explicit:
        select_application(directory, "Example", allow_windows_lookup=True)
    assert explicit.value.code == "element_not_found"

    directory.pop("nativeProcessNames")
    with pytest.raises(ComputerUseError) as incomplete:
        select_application(directory, "Example", allow_windows_lookup=True)
    assert incomplete.value.code == "observation_incomplete"


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
    with pytest.raises(ComputerUseError) as caught:
        select_application(directory, "Example")
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
