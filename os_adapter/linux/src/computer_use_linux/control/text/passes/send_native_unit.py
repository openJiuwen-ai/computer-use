# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native stage send_native_unit; bounded explicit dependencies, no route fallback.

Effect and failure behavior remain the original native stage contract.
"""

from __future__ import annotations

from computer_use_linux.contracts.protocol import Failure


def send_native_unit(method, obj, args, native_call):
    ok = native_call("EditableText", method, obj, *args)
    if not ok:
        raise Failure("The native text call did not confirm success; inspect before continuing.", "execution_unknown")


CONTRACT = {
    "input": ("method", "obj", "args", "native_call"),
    "output": "original native stage result",
    "effects": ("DISPATCH",),
    "borrows": ("method", "obj", "args", "native_call"),
    "writes": (),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
