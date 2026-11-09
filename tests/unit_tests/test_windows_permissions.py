# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from computer_use_native_tools.native import permission_state


def test_windows_readiness_is_not_an_operation_permission_claim():
    status = {"platform": "windows", "ready": True, "desktopState": "interactive"}
    assert permission_state(status, platform="win32") == (True, [])


def test_windows_noninteractive_state_names_the_missing_precondition():
    status = {"platform": "windows", "ready": False, "desktopState": "differentInputDesktop"}
    assert permission_state(status, platform="win32") == (False, ["interactiveDesktop"])
