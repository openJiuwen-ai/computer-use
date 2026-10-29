# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Format desktop observation."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from computer_use.interaction.read.acquisition.images.desktop.model import DesktopLease
from computer_use.interaction.read.presentation.content import DisplayText, json_value, template
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.ir import freeze
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.protocol.captures import capture_metadata

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def format_desktop_observation(value: DesktopLease, control: Control) -> DesktopLease:
    """Retain structured desktop layout and image notes for final text rendering."""
    result = cast("Record", value.result)
    parts = [DisplayText("desktop", (freeze(result["observation"]),))]
    if result["screenshot"] is not None:
        parts.extend(
            template("\nImage note: {0}", json_value(reason))
            for reason in capture_metadata(result["screenshot"]).get("stopReasons", ())
        )
    return replace(value, result={**result, "document": DisplayDocument.join(parts, separator="")})


stage = Pass.define(
    name="format_desktop_observation",
    function=format_desktop_observation,
    contract=Contract(DesktopLease, DesktopLease, {Effect.PURE}),
)
if TYPE_CHECKING:
    from computer_use.protocol.records import Record
