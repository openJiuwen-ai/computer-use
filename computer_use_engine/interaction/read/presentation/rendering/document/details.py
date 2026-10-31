# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Collect typed detail continuations for one observation's terminal footer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.content import DisplayText, live_subtree_hint, text
from computer_use.interaction.read.presentation import content

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.records import RenderedTree


def separate_details(value: DisplayText, snapshots: Mapping[int, RenderedTree]) -> tuple[DisplayText, tuple[str, ...]]:
    """Retain captured text and extract only generated native-detail instructions.

    Args:
        value: One structured document field, including optional nested formatting.
        snapshots: Already-rendered sources used to determine whether extra detail exists.

    Returns:
        The original field with typed hints removed, and its exact public root references.
        Literal application content and ordinary continuation messages remain unchanged.
    """
    if value.kind == "details_hint":
        prepared, root = value.values
        rendered = snapshots[id(prepared)]
        if content.omit_public_details_hint(prepared):
            return text(""), ()
        return text(""), (str(root),) if rendered["details"] != rendered["text"] else ()

    roots: list[str] = []

    def visit(item: object) -> object:
        if isinstance(item, DisplayText):
            retained, refs = separate_details(item, snapshots)
            roots.extend(refs)
            return retained
        if isinstance(item, tuple):
            return tuple(visit(child) for child in item)
        return item

    return replace(value, values=tuple(visit(item) for item in value.values)), tuple(roots)


def detail_footer(roots: list[str]) -> str:
    """Share the read instruction while retaining every displayed root number."""
    return live_subtree_hint(roots)
