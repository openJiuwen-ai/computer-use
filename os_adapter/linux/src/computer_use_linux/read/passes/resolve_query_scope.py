# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""PURE: resolve_query_scope; only explicitly borrowed capabilities are available.

Input/output follow the native stage contract. Failures propagate unchanged;
this leaf never selects another route, retries or acquires another budget.
"""

from computer_use_linux.contracts.protocol import fields, string

from ..model import NativeQueryScope


def resolve_query_scope(params, session, current, decode_binding, decode_query):
    fields(params, ("operation", "target", "query"))
    string(params["operation"])
    target = decode_binding(params["target"], session)
    request = decode_query(params["query"])
    operation = current
    kind = request["kind"]
    return NativeQueryScope(
        operation,
        params["operation"],
        target,
        request,
        kind,
        "application" in target,
        kind in ("applications", "surfaces", "capturedText"),
    )


CONTRACT = {
    "input": ("params", "session", "current", "decode_binding", "decode_query"),
    "output": "original native stage result",
    "effects": ("PURE",),
    "borrows": ("params", "session", "current", "decode_binding", "decode_query"),
    "writes": (),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
