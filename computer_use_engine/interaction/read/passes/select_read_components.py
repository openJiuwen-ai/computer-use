# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select read components."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.app_read.model import ApplicationSelection, ReadComponentKind
from computer_use.interaction.read.model import PURE
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(value: ApplicationSelection, control: Control) -> ReadComponentKind:
    """Choose menu observation when requested or when the application has no working window."""
    if value.request.menu_path is not None:
        return ReadComponentKind.MENU
    if value.selection is not None:
        return ReadComponentKind.WINDOW
    return ReadComponentKind.IMAGE_UNAVAILABLE if value.request.view == "image" else ReadComponentKind.MENU


stage = Pass.define(
    name="select_read_components",
    function=execute,
    contract=Contract(ApplicationSelection, ReadComponentKind, PURE),
)
