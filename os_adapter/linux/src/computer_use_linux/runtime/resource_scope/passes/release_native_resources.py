# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""RESOURCE: release_native_resources; only explicitly borrowed capabilities are available.

Input/output follow the native stage contract. Failures propagate unchanged;
this leaf never selects another route, retries or acquires another budget.
"""

from computer_use_linux.contracts.protocol import require


def release_native_resources(operation, owner, store):
    if owner.current and owner.current.token == operation:
        closed = owner.current
        closed.close()
        owner.current = None
        store.pins.clear()
        owner.finished[operation] = closed
        while len(owner.finished) > 8:
            owner.finished.popitem(last=False)
    require(operation in owner.finished, "Unknown or expired native operation.", "stale_target")
    return dict(cleanup=owner.finished[operation].cleanup)


CONTRACT = {
    "input": ("operation", "owner", "store"),
    "output": "original native stage result",
    "effects": ("RESOURCE",),
    "borrows": ("operation", "owner", "store"),
    "writes": ("original operation Owner/registry",),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
