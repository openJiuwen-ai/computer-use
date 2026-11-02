# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""QUERY/RESOURCE: collect_pointer_contracts; only explicitly borrowed capabilities are available.

Input/output follow the native stage contract. Independent object actions
survive local optional-query failures; this leaf never selects a route,
retries or acquires another budget.
"""

from computer_use_linux.contracts.protocol import Failure

from ..optional_candidates import can_keep_object_contracts


def collect_pointer_contracts(scope, result, ctx, element_pointer):
    operation, target, request = scope.operation, scope.target, scope.request
    # Declare both native routes. Common Python policy chooses
    # semantics and fallback; geometry gaps must not hide actions.
    try:
        pointer = element_pointer.inspect(operation, target, request["within"], ctx)
    except (Failure, element_pointer.ax.glib.Error) as error:
        ctx.check()
        if not can_keep_object_contracts(result, error, ctx.effect, "native_action"):
            raise
        # The action still owns its fresh native target/scope/permission check.
        # An unavailable optional pointer query neither grants that action nor
        # turns its established contract into an unavailable mechanism.
        result["unavailableReason"] = (
            "The optional pointer query is unavailable; the independent object action remains available. " + str(error)
        )
    else:
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
    "failure_boundary": "retain independent object contracts only for local no-effect query failures; never retry",
}
