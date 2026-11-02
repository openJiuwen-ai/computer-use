# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native stage check_dispatch_conditions; bounded explicit dependencies, no route fallback.

Effect and failure behavior remain the original native stage contract.
"""

from computer_use_linux.contracts.protocol import require


def check_dispatch_conditions(contract, selected, target, within, index, ctx, target_service):
    # The catalogue query can run callbacks. Check the original target
    # again, then the current index immediately before its native call.
    role_required = contract.get("roleRequired", True)
    obj, _, interfaces, role, enabled = target_service.read(target, within, ctx, include_role=role_required)
    require(
        enabled and "Action" in interfaces and (not role_required or role == contract["role"]),
        "The original action target changed.",
        "input_unavailable",
    )
    target_service.validate_scope(target, within, ctx)
    if "description" in selected:
        require(
            target_service.call("Action", "get_action_description", obj, index) == selected["description"],
            "The action description changed before dispatch.",
            "input_unavailable",
        )
    # Scope/description queries can run provider callbacks. The last native
    # identity read must still name the selected action at this exact index.
    require(
        target_service.call("Action", "get_action_name", obj, index) == selected["name"],
        "The action index changed before dispatch.",
        "input_unavailable",
    )
    return obj


CONTRACT = {
    "input": ("contract", "selected", "target", "within", "index", "ctx", "target_service"),
    "output": "original native stage result",
    "effects": ("QUERY",),
    "borrows": ("contract", "selected", "target", "within", "index", "ctx", "target_service"),
    "writes": (),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
