# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native stage check_dispatch_conditions; bounded explicit dependencies, no route fallback.

Effect and failure behavior remain the original native stage contract.
"""

from __future__ import annotations

from computer_use_linux.contracts.protocol import Failure


def check_dispatch_conditions(contract, ctx, guard):
    target, within, kind = contract["target"], contract["within"], contract["kind"]
    selection = contract.get("selection")
    obj, enabled, editable, interfaces, _ = guard(target, within, ctx, selection, check=True)
    invalid_enabled = True if not enabled or not editable or kind == "insert" and "Text" not in interfaces else False
    if invalid_enabled:
        raise Failure("The original control no longer supports this native text operation.", "input_unavailable")
    return obj


CONTRACT = {
    "input": ("contract", "ctx", "guard"),
    "output": "original native stage result",
    "effects": ("QUERY",),
    "borrows": ("contract", "ctx", "guard"),
    "writes": (),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
