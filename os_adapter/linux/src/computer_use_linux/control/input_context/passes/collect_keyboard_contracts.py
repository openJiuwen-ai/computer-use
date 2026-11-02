# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""QUERY/RESOURCE: collect_keyboard_contracts; only explicitly borrowed capabilities are available.

Input/output follow the native stage contract. Independent object edits
survive local optional-query failures; this leaf never selects a route,
retries or acquires another budget.
"""

from computer_use_linux.contracts.protocol import Failure

from ..optional_candidates import can_keep_object_contracts


def collect_keyboard_contracts(scope, options, result, ctx, typed_text):
    operation, target, request = scope.operation, scope.target, scope.request
    # Declare independent object and keyboard contracts. The
    # common policy owns preference, preparation and fallback.
    try:
        keyboard = typed_text.inspect(operation, target, request["within"], ctx, **options)
    except (Failure, typed_text.ax.glib.Error) as error:
        ctx.check()
        # Optional receiver/desktop observations cannot erase
        # an independently established object edit. Cancellation,
        # stale identity and modal/scope errors still propagate;
        # execution retains the object's normal final guards.
        if not can_keep_object_contracts(result, error, ctx.effect, "native_text_edit"):
            raise
        result["unavailableReason"] = (
            "The optional keyboard query is unavailable; the independent object edit remains available. " + str(error)
        )
    else:
        keyboard["primitives"] = result["primitives"] + keyboard["primitives"]
        result = keyboard
    return result


CONTRACT = {
    "input": ("scope", "options", "result", "ctx", "typed_text"),
    "output": "original native stage result",
    "effects": ("QUERY", "RESOURCE"),
    "borrows": ("scope", "options", "result", "ctx", "typed_text"),
    "writes": ("original operation Owner/registry",),
    "failure_boundary": "retain independent object contracts only for local no-effect query failures; never retry",
}
