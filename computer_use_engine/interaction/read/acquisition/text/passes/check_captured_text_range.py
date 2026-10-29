# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Check captured text range."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.text.model import PURE, CapturedTextPage
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import TextPage as NativeTextPage


def check_captured_page(value: CapturedTextPage, control: Control) -> NativeTextPage:
    """Check the returned page offset and captured total before delivering the native text
    page.
    """
    source, offset, page = value.plan.request.source, value.plan.request.offset, value.page
    if page["offset"] != offset or source.total_units is not None and page["total"] != source.total_units:
        raise ValueError("Native text page changed its immutable capture range")
    return page


stage = Pass.define(
    name="check_captured_text_range",
    function=check_captured_page,
    contract=Contract(CapturedTextPage, dict, PURE),
)
