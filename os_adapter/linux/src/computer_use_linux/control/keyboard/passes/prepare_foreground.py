# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native stage prepare_foreground; bounded explicit dependencies, no route fallback.

Effect and failure behavior remain the original native stage contract.
"""

from computer_use_linux.contracts.protocol import require


def prepare_foreground(operation, contract, foreground, ctx, preparation, r, key, mode, desktop, guard):
    if foreground and not r["route"]:
        require(
            mode == "exclusive" or desktop.assist,
            "Host policy does not permit foreground assistance.",
            "mode_unsupported",
        )
        require(
            not r["can_grab"] or key in operation.background_unavailable,
            "Background preparation has not been attempted for this receiver.",
            "invalid_request",
        )
        preparation.append("foreground")
        desktop.activate(r["window"], ctx)
        r = guard(contract, ctx)
    return r


CONTRACT = {
    "input": ("operation", "contract", "foreground", "ctx", "preparation", "r", "key", "mode", "desktop", "guard"),
    "output": "original native stage result",
    "effects": ("PREPARE", "QUERY"),
    "borrows": ("operation", "contract", "foreground", "ctx", "preparation", "r", "key", "mode", "desktop", "guard"),
    "writes": (),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
