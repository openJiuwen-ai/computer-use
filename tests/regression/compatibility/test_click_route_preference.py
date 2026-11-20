# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Windows click preference does not override explicit action or input authorization."""

import pytest
from computer_use.interaction.write.routes.policy import Attempts, choose
from computer_use.native.contracts import HostPolicy, InputContext, Intent, Knowledge

from regression.compatibility.click_route_fixtures import CLICK, TARGET, click_route, facts


@pytest.mark.parametrize(
    "platform,role,action",
    [
        ("macos", "AXButton", "AXPress"),
        ("windows", "ControlType.Button", "Invoke.Invoke"),
        ("linux", "push button", "click"),
    ],
)
def test_ready_pointer_preference_respects_platform_and_authority(platform, role, action):
    native, pointer = click_route("object_action", action=action), click_route("desktop_pointer")
    current = facts(
        native,
        pointer,
        platform=platform,
        native_role=role,
        input=InputContext(foreground_surface=TARGET.surface, pointer_hit=TARGET.node, geometry_current=Knowledge.YES),
    )
    expected = pointer if platform == "windows" else native
    assert choose(current, CLICK, HostPolicy(), Attempts()).primitive == expected
    assert choose(current, CLICK, HostPolicy(False), Attempts()).primitive == native
    explicit = Intent("action", {"action": action})
    assert choose(current, explicit, HostPolicy(), Attempts()).primitive == native
