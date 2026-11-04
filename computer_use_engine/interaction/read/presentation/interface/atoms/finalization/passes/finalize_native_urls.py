# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Finalize native URL detail destinations after caption disclosure decisions."""

from __future__ import annotations

from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control
from ...model import AtomDocument, AtomFinalizeRequest
from ...native_urls import finalize_native_urls


def execute(request: AtomFinalizeRequest, control: Control) -> AtomDocument:
    """Apply exact-caption and peer-ambiguity evidence to native URL slots."""
    return finalize_native_urls(request)


stage = Pass.define(
    name="finalize_interface_native_urls",
    function=execute,
    contract=Contract(
        AtomFinalizeRequest,
        AtomDocument,
        {Effect.PURE},
        requires=("prepared atomic fields", "selected disclosure", "complete native URL evidence"),
        writes=("native URL detail destinations", "final atomic content"),
    ),
)
