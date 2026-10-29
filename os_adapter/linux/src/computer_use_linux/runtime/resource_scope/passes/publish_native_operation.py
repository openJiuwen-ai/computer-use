# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""RESOURCE: publish_native_operation; only explicitly borrowed capabilities are available.

Input/output follow the native stage contract. Failures propagate unchanged;
this leaf never selects another route, retries or acquires another budget.
"""


def publish_native_operation(operation, target, ctx, owner, store):
    owner.current = operation
    if "application" not in target and target["node"] is not None:
        store.pins.add(target["node"])
    result = dict(operation=operation.token, target=dict(target), remainingMs=ctx.remaining_ms)
    if operation.click_contract is not None:
        result["clickContract"] = operation.click_contract
    return result


CONTRACT = {
    "input": ("operation", "target", "ctx", "owner", "store"),
    "output": "original native stage result",
    "effects": ("RESOURCE",),
    "borrows": ("operation", "target", "ctx", "owner", "store"),
    "writes": ("original operation Owner/registry",),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
