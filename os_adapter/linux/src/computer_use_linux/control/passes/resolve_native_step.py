# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""PURE: resolve_native_step; only explicitly borrowed capabilities are available.

Input/output follow the native stage contract. Failures propagate unchanged;
this leaf never selects another route, retries or acquires another budget.
"""

from computer_use_linux.contracts.protocol import fields, integer, require, string

from ..model import NativeInputStep


def resolve_native_step(params, session, current, finished, decode_binding):
    fields(params, ("operation", "step", "command"))
    string(params["operation"])
    integer(params["step"], 1)
    command = fields(params["command"], ("kind", "primitiveId", "target", "foreground"))
    string(command["kind"])
    string(command["primitiveId"])
    decode_binding(command["target"], session)
    # Exact types preserve scalar validation and identity contracts.
    # pylint: disable-next=unidiomatic-typecheck
    require(type(command["foreground"]) is bool, "Invalid foreground flag.")
    operation = current if current and current.token == params["operation"] else finished.get(params["operation"])
    require(operation is not None and operation.writable, "Unknown writable native operation.", "stale_target")
    return NativeInputStep(operation, params["step"], command)


CONTRACT = {
    "input": ("params", "session", "current", "finished", "decode_binding"),
    "output": "original native stage result",
    "effects": ("PURE",),
    "borrows": ("params", "session", "current", "finished", "decode_binding"),
    "writes": (),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
