# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native stage resolve_native_target; bounded explicit dependencies, no route fallback.

Effect and failure behavior remain the original native stage contract.
"""

from typing import Any, NamedTuple

from computer_use_linux.contracts.protocol import fields, require, string


class ResolvedNativeTarget(NamedTuple):
    """One native action's checked contract, identity, and target boundary."""

    contract: dict[str, Any]
    identity: Any
    target: dict[str, Any]
    within: Any


def resolve_native_target(operation, command) -> ResolvedNativeTarget:
    fields(command, ("kind", "primitiveId", "target", "foreground"))
    require(
        command["kind"] == "native_action" and command["foreground"] is False,
        "This native action unit does not perform foreground preparation.",
        "invalid_request",
    )
    string(command["primitiveId"])
    contract = operation.contracts.get(command["primitiveId"])
    require(
        contract is not None
        and contract["unit"] == "native_action"
        and contract["usable"]
        and command["target"] == contract["target"],
        "The native action contract expired or changed its target.",
        "stale_target",
    )
    selected = contract["action"]
    if operation.intent["kind"] == "action":
        require(
            operation.intent["parameters"]["action"] in selected.values(),
            "The native action does not match the original request.",
            "invalid_request",
        )
    selected = contract["identity"]
    target, within = contract["target"], contract["within"]
    return ResolvedNativeTarget(contract, selected, target, within)


CONTRACT = {
    "input": ("operation", "command"),
    "output": "original native stage result",
    "effects": ("PURE",),
    "borrows": ("operation", "command"),
    "writes": (),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
