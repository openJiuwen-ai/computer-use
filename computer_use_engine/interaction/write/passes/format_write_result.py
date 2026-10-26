"""Preserve the already-complete write receipt."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import Record


def format_write_result(result: Record, control: Control) -> Record:
    """Pass the completed write receipt to the caller."""
    return result


stage = Pass.define(
    name="format_write_result",
    function=format_write_result,
    contract=Contract(dict, dict),
)
