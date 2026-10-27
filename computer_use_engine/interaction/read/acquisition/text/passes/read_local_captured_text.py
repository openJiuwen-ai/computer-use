"""Read local captured text."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.text.model import PURE, LocalCapturedTextRead
from computer_use.pipeline import Contract, Pass
from computer_use.protocol.text import text_page

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import TextPage as NativeTextPage


def local_page(value: LocalCapturedTextRead, control: Control) -> NativeTextPage:
    """Slice a page from the retained local text using the requested offset and length."""
    request = value.plan.request
    return text_page(request.source.value, request.offset, request.length)


stage = Pass.define(
    name="read_local_captured_text",
    function=local_page,
    contract=Contract(LocalCapturedTextRead, dict, PURE, requires=("immutable captured string",)),
)
