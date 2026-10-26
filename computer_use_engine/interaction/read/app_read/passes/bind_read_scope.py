"""Bind read scope."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.app_read.model import RESOURCE, ApplicationSelection
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def bind(value: ApplicationSelection, control: Control) -> ApplicationSelection:
    """Notify the request owner when a working-window selection has been established."""
    if value.selection is not None and value.request.on_scope is not None:
        value.request.on_scope(value.selection)
    return value


stage = Pass.define(
    name="bind_read_scope",
    function=bind,
    contract=Contract(
        ApplicationSelection,
        ApplicationSelection,
        RESOURCE,
        writes=("established application/window identity",),
    ),
)
