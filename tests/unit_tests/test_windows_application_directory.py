# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest
from computer_use import ComputerUseError
from computer_use.native.query.directory import select_application
from computer_use.protocol.directory import decode_directory


def directory(**updates):
    return {
        "kind": "applications",
        "session": "session",
        "platform": "windows",
        "scope": "runningWindows",
        "observed": {"id": "observation", "startedAt": 0, "completedAt": 1},
        "stopReasons": ["Unrelated supplementary discovery is unavailable"],
        "nativeProcessNames": [{"application": "application:fixture", "name": "WindowsTerminal"}],
        "applications": [
            {
                "id": "application:fixture", "displayName": "WindowsTerminal", "isRunning": True,
                "path": None, "identifier": None,
                "windows": [{"selector": "instance:fixture", "window": "window:fixture"}],
            },
        ],
        **updates,
    }


def test_exact_native_process_name_survives_supplementary_directory_failure():
    value = directory()
    assert select_application(value, "windowsterminal")["id"] == "application:fixture"
    assert decode_directory(value) == value


@pytest.mark.parametrize(
    "change", ["primary_incomplete", "supplementary_only", "alias", "duplicate", "native_name_conflict", "missing"]
)
def test_directory_warning_does_not_authorize_uncertain_windows_process_names(change):
    value = directory()
    selector = "WindowsTerminal"
    if change == "primary_incomplete":
        value.pop("nativeProcessNames")
    elif change == "supplementary_only":
        value["nativeProcessNames"] = []
    elif change == "alias":
        value["applications"][0]["aliases"] = ["alternate"]
        selector = "alternate"
    elif change in ("duplicate", "native_name_conflict"):
        value["applications"].append(
            {**value["applications"][0], "id": "application:second", "windows": [
                {"selector": "instance:second", "window": "window:second"},
            ]}
        )
        if change == "native_name_conflict":
            value["applications"][-1]["displayName"] = "Different hosted app label"
            value["nativeProcessNames"].append({"application": "application:second", "name": "WindowsTerminal"})
    else:
        selector = "missing"
    with pytest.raises(ComputerUseError) as caught:
        select_application(value, selector)
    assert caught.value.code == "observation_incomplete"


def test_non_windows_cannot_claim_primary_windows_process_name_evidence():
    value = directory(platform="macos")
    with pytest.raises(ComputerUseError) as caught:
        select_application(value, "WindowsTerminal")
    assert caught.value.code == "observation_incomplete"
    with pytest.raises(ValueError):
        decode_directory(value)
