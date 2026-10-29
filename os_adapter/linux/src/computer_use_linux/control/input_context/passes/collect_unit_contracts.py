# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""QUERY/RESOURCE: inspect exactly the unit selected from retained evidence."""


def collect_unit_contracts(unit, scope, selection, ctx):
    options = {} if selection is None else dict(selection=selection)
    return unit.inspect(scope.operation, scope.target, scope.request["within"], ctx, **options)


CONTRACT = {
    "input": ("unit", "scope", "selection", "ctx"),
    "output": "original native stage result",
    "effects": ("QUERY", "RESOURCE"),
    "borrows": ("unit", "scope", "selection", "ctx"),
    "writes": ("original operation Owner/registry",),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
