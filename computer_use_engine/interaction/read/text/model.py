from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.interaction.requests import ReadText

if TYPE_CHECKING:
    from computer_use.interaction.read.acquisition.reader import CapturedField
    from computer_use.protocol.records import TextPage as NativeTextPage


@dataclass(frozen=True)
class TextReadPlan:
    """Captured-text request paired with its retained source and collected page.

    Attributes:
        request: Parsed request carrying the original operation scope and options.
        source: Retained text value or native reference identified by the request.
        page: Collected native text page and its range metadata.
    """

    request: ReadText
    source: CapturedField | None
    page: NativeTextPage | None = None
