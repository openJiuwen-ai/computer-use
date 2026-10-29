# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""QUERY/RESOURCE: collect_launch_facts using only the named query service."""


def collect_launch_facts(scope, ctx, inspect):
    return inspect(scope.operation, scope.target, ctx)


CONTRACT = {
    "input": ("scope", "ctx", "inspect"),
    "output": "native result or original validation result",
    "effects": ("QUERY", "RESOURCE"),
    "borrows": ("scope", "ctx", "inspect"),
    "writes": ("original registry during collection",),
    "failure_boundary": "original failure propagation; no retry or input",
}
