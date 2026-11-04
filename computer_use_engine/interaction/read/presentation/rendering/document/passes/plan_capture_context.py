# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Label fully evidenced capture companions before any snapshot becomes text."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.content import DisplayText
from computer_use.interaction.read.presentation.model import PURE
from computer_use.interaction.read.presentation.rendering.document.capture_context import capture_companions
from computer_use.interaction.read.presentation.rendering.document.model import DocumentRenderRequest
from computer_use.pipeline import Contract, Pass
from computer_use.pipeline.core import Control


def execute(request: DocumentRenderRequest, control: Control) -> DocumentRenderRequest:
    """Add a render-local region label while retaining every original tree and header."""
    companions = capture_companions(request.document)
    if not companions:
        return request
    parts = []
    changed = False
    for position, part in enumerate(request.document.parts):
        if position in companions:
            previous = request.document.parts[position - 1] if position else None
            if not (
                isinstance(previous, DisplayText)
                and previous.kind == "capture_status"
                and previous.values == (companions[position],)
            ):
                parts.append(DisplayText("capture_status", (companions[position],)))
                changed = True
        parts.append(part)
    if not changed:
        return request
    return replace(request, document=replace(request.document, parts=tuple(parts)))


stage = Pass.define(
    name="plan_capture_context",
    function=execute,
    contract=Contract(
        DocumentRenderRequest,
        DocumentRenderRequest,
        PURE,
        requires=("complete before/after directories, capture intervals and native control signature",),
        writes=("render-local capture-status region labels; original sources remain unchanged",),
    ),
)
