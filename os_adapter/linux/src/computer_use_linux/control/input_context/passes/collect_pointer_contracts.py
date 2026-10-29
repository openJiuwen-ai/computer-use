# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""QUERY/RESOURCE: collect_pointer_contracts; only explicitly borrowed capabilities are available.

Input/output follow the native stage contract. Failures propagate unchanged;
this leaf never selects another route, retries or acquires another budget.
"""


def collect_pointer_contracts(scope, result, ctx, element_pointer):
    operation, target, request = scope.operation, scope.target, scope.request
    # Declare both native routes. Common Python policy chooses
    # semantics and fallback; geometry gaps must not hide actions.
    pointer = element_pointer.inspect(operation, target, request["within"], ctx)
    pointer["primitives"] = result["primitives"] + pointer["primitives"]
    pointer["actionsComplete"] = result["actionsComplete"]
    result = pointer
    return result


CONTRACT = {
    "input": ("scope", "result", "ctx", "element_pointer"),
    "output": "original native stage result",
    "effects": ("QUERY", "RESOURCE"),
    "borrows": ("scope", "result", "ctx", "element_pointer"),
    "writes": ("original operation Owner/registry",),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
