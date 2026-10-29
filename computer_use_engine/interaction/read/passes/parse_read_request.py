# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Parse read request."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.app_read.helpers import parse_read_request
from computer_use.interaction.read.app_read.model import ReadInvocation
from computer_use.interaction.read.model import PURE
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(value: ReadInvocation, control: Control) -> ReadInvocation:
    """Validate and normalize the application read options against the current control."""
    return replace(value, request=parse_read_request(value.request, control))


stage = Pass.define(
    name="parse_read_request",
    function=execute,
    contract=Contract(ReadInvocation, ReadInvocation, PURE),
)
