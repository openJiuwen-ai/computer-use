"""Normalize menu depth."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.menus.model import PURE, MenuCollectionRequest
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def normalize_depth(request: MenuCollectionRequest, control: Control) -> MenuCollectionRequest:
    """Use menu depth 40 when the request leaves the native depth limit unspecified."""
    return replace(request, max_depth=40 if request.max_depth is None else request.max_depth)


stage = Pass.define(
    name="normalize_menu_depth",
    function=normalize_depth,
    contract=Contract(MenuCollectionRequest, MenuCollectionRequest, PURE),
)
