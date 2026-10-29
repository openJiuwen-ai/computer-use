# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Encode accessibility collections as the shared NativeObservation graph."""

from computer_use_linux.read.native_observation import encode_observation


def encode_native_facts(result, ctx):
    ctx.check()
    return encode_observation(result) if result.get("kind") in ("nodes", "relation") else result


CONTRACT = {
    "input": ("result", "ctx"),
    "output": "typed graph or independent native directory result",
    "effects": ("PURE",),
    "borrows": ("result", "ctx"),
    "writes": (),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
