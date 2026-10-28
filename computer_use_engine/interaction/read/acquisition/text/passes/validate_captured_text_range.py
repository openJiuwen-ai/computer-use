"""Validate captured text range."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.text.model import PURE, CapturedTextRequest
from computer_use.pipeline import Contract, Pass
from computer_use.protocol.primitives import safe_integer

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def validate_captured_range(request: CapturedTextRequest, control: Control) -> CapturedTextRequest:
    """Validate nonnegative offset, text page length, and the millisecond call budget."""
    offset, length, timeout_ms = request.offset, request.length, request.timeout_ms
    invalid_offset = (
        True
        if (
            not safe_integer(offset)
            or offset < 0
            or not safe_integer(length)
            or not 2 <= length <= 16384
            # Exact types preserve scalar validation and identity contracts.
            # pylint: disable-next=unidiomatic-typecheck
            or type(timeout_ms) is not int
            or not 1 <= timeout_ms <= 120000
        )
        else False
    )
    if invalid_offset:
        raise ValueError("Invalid captured text range or budget")
    return request


stage = Pass.define(
    name="validate_captured_text_range",
    function=validate_captured_range,
    contract=Contract(CapturedTextRequest, CapturedTextRequest, PURE),
)
