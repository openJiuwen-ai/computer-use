# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Session permission for global injection, independent of target and focus."""

from computer_use_linux.contracts.protocol import Failure


def require_global_input(desktop):
    if getattr(desktop, "allow_global_input", False) is not True:
        raise Failure(
            "This route requires global keyboard or pointer input permission; no input was sent.",
            "permission_required",
            next="Obtain global input authorization and create an explicitly authorized SDK session.",
        )
