# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""RESOURCE: acquire_native_resources; only explicitly borrowed capabilities are available.

Input/output follow the native stage contract. Failures propagate unchanged;
this leaf never selects another route, retries or acquires another budget.
"""


def acquire_native_resources(operation, ctx, coordinate):
    if operation.writable:
        lease = coordinate(ctx)
        lease.__enter__()

        def release():
            return lease.__exit__(None, None, None)

        operation.release = release


CONTRACT = {
    "input": ("operation", "ctx", "coordinate"),
    "output": "original native stage result",
    "effects": ("RESOURCE",),
    "borrows": ("operation", "ctx", "coordinate"),
    "writes": ("original operation Owner/registry",),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
