# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""QUERY: validate_element_scope using only the named query service."""


def validate_element_scope(scope, ctx, directory):
    directory.validate(scope.target, ctx)


CONTRACT = {
    "input": ("scope", "ctx", "directory"),
    "output": "native result or original validation result",
    "effects": ("QUERY",),
    "borrows": ("scope", "ctx", "directory"),
    "writes": (),
    "failure_boundary": "original failure propagation; no retry or input",
}
