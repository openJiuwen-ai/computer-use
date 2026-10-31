# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""An explicit image request cannot be fulfilled by collecting a menu tree."""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

from computer_use.interaction.read.app import failure
from computer_use.interaction.read.app_read.model import ApplicationSelection
from computer_use.interaction.read.model import PURE
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(value: ApplicationSelection, control: Control) -> NoReturn:
    raise failure(
        "observation_unavailable",
        "No observable working window is available for this image request. "
        "Read the desktop or observe the same App when its window appears.",
    )


stage = Pass.define(
    name="reject_windowless_image",
    function=execute,
    contract=Contract(ApplicationSelection, object, PURE),
)
