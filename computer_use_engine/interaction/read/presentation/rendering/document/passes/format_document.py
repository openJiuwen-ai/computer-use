# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Format full or incremental text from final snapshot renders."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.model import PURE
from computer_use.interaction.read.presentation.rendering.document.formatting import format_document
from computer_use.interaction.read.presentation.rendering.document.model import RenderedDocumentInput
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(value: RenderedDocumentInput, control: Control) -> str:
    """Format captured sections and planned node changes from the supplied rendered map."""
    request = value.request
    return format_document(request.document, value.snapshots, request.plan, public_api=request.public_api)


stage = Pass.define(name="format_document", function=execute, contract=Contract(RenderedDocumentInput, str, PURE))
