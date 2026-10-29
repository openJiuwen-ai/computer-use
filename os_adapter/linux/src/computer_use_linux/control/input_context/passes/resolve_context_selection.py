# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""QUERY/RESOURCE: resolve the existing operation-local selection evidence."""


def resolve_context_selection(scope):
    return (
        scope.operation.selection_facts.select(scope.target, scope.request["actuator"], scope.request["basis"])
        if "actuator" in scope.request
        else None
    )


CONTRACT = {
    "input": ("scope",),
    "output": "original native stage result",
    "effects": ("QUERY", "RESOURCE"),
    "borrows": ("scope",),
    "writes": ("original operation Owner/registry",),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
