# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Assemble rendered lines."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.model import PURE
from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.records import RenderedTree


def execute(frame: LayoutFrame, _: object) -> RenderedTree:
    """Finalize the laid-out semantic lines and accumulated layout records into a rendered
    tree.
    """
    result = frame.finalize(frame.lines, list(frame.records))
    if frame.field_prefix_replacements and result["text"] == "\n".join(frame.lines):
        result["fieldPrefixReplacements"] = [asdict(item) for item in frame.field_prefix_replacements]
    if frame.role_label_replacements and result["text"] == "\n".join(frame.lines):
        result["roleLabelReplacements"] = [asdict(item) for item in frame.role_label_replacements]
    if frame.local_field_suffix_replacements and result["text"] == "\n".join(frame.lines):
        result["localFieldSuffixReplacements"] = [asdict(item) for item in frame.local_field_suffix_replacements]
    if frame.shared_description_replacements and result["text"] == "\n".join(frame.lines):
        result["sharedDescriptionReplacements"] = [asdict(item) for item in frame.shared_description_replacements]
    return result


stage = Pass.define(
    name="assemble_rendered_lines",
    function=execute,
    contract=Contract(
        LayoutFrame,
        dict,
        PURE,
        requires=("packed semantic lines", "actual field records"),
        writes=(
            "final rendered tree",
            "actual public field prefix destinations",
            "actual public role spelling destinations",
            "actual local URL field destinations",
            "actual shared Description field destinations",
        ),
    ),
)
