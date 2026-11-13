# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Captured-detail documents and exact native read-issue records."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.content import DisplayText, text
from computer_use.interaction.read.presentation.document import DisplayDocument, DisplayTree

if TYPE_CHECKING:
    pass


DETAILS_HINT = "Captured details available."


def captured_details_document(document: DisplayDocument) -> DisplayDocument:
    """Select complete captured supplements while retaining window and source boundaries.

    Typed detail hints disappear from this view. Application strings and genuine
    continuation commands remain unchanged; creating the document performs no rendering.
    """

    def retained(value: object) -> object:
        if isinstance(value, DisplayText):
            if value.kind == "details_hint":
                return text("")
            if value.kind in ("interface_header", "image_header"):
                return replace(value, kind="captured_metadata")
            return replace(value, values=tuple(retained(item) for item in value.values))
        if isinstance(value, tuple):
            return tuple(retained(item) for item in value)
        return value

    return replace(
        document,
        parts=tuple(
            replace(part, details=True) if isinstance(part, DisplayTree) else retained(part) for part in document.parts
        ),
    )
