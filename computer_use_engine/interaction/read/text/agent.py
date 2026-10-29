# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Current text capture, then immutable code-point pages with opaque cursors."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.identity import FrozenRecord
from computer_use.diagnostics import local_failure
from computer_use.interaction.read.acquisition.text.model import CapturedTextRequest
from computer_use.interaction.read.acquisition.text.pipeline import CapturedTextPipeline
from computer_use.interaction.read.element.agent import AgentElementReadPipeline
from computer_use.interaction.read.facts.text_source import text_source_label
from computer_use.session.results import text_page_completed

if TYPE_CHECKING:
    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.read.acquisition.reader import CapturedField
    from computer_use.interaction.records import ElementSnapshot, UIHandle
    from computer_use.session.cursors import TextCursors
    from computer_use.types import TextPage


class AgentTextReadPipeline:
    """Provide public character-based text pages and continuation cursors.

    For a first page, refresh the selected element and assemble its immutable captured text.
    For a continuation, reuse the retained text and position; return a Python-character
    slice and the next public cursor.

    Attributes:
        controller: Session services used to refresh and validate the selected text element.
        cursors: Retained text and continuation positions bounded by the public capture
            cache.
    """

    def __init__(self, controller: SessionController, cursors: TextCursors) -> None:
        self.controller, self.cursors = controller, cursors

    async def run(self, ui: UIHandle, snapshot: ElementSnapshot, cursor: str | None, length: int) -> TextPage:
        """Return a public code-point page from current text or a retained continuation.

        Args:
            ui: App or Window scope that owns the selected element.
            snapshot: Element binding and its retained source metadata.
            cursor: Public text cursor, or None to capture the element's current text.
            length: Maximum number of Python characters to include in the returned page.
        """
        self.controller.interfaces.validate_snapshot(ui, snapshot)
        target = snapshot["binding"]
        source = snapshot.get("source", {})
        if cursor is not None:
            continuation = self.cursors.resolve(cursor, target)
            value, position = continuation.value, continuation.position
            source = continuation.source or source
        else:
            _, fresh = await AgentElementReadPipeline(self.controller).collect(ui, snapshot, max_nodes=1)
            captured = fresh.get("text")
            source = fresh.get("source", {})
            if captured is None:
                raise local_failure(
                    "this element has no complete captured text source",
                    "read the element or select its explicit text control",
                    "observation_incomplete",
                )
            value, position = await self.complete(captured), 0
        end = min(len(value), position + length)
        following = self.cursors.publish(target, value, end, source) if end < len(value) else None
        result = FrozenRecord(
            dict(text=value[position:end], total=len(value), next=following),
            display=text_source_label(source)
            + "\n"
            + repr(dict(text=value[position:end], total=len(value), next=following)),
        )
        text_page_completed(source, result)
        return cast("TextPage", result)

    async def complete(self, source: CapturedField) -> str:
        """Assemble the complete captured text from its local value or native pages.

        Native pages use provider text units; public pagination starts after the immutable
        capture has been assembled as a Python string.
        """
        if source.value is not None:
            return source.value
        # Native continuations remain UTF-16 inside the private adapter. Count
        # Python characters only after reading this single immutable textRef;
        # never infer a code-point total from UTF-16 units or a preview.
        collector = CapturedTextPipeline(self.controller.port)
        chunks, offset, total, characters = [], 0, source.total_units, 0
        while True:
            page = await collector.arun(CapturedTextRequest(source, offset=offset, length=16384))
            if total is not None and total != page["total"]:
                raise local_failure(
                    "captured text changed its declared range", "start a new read_text()", "snapshot_expired"
                )
            total = page["total"]
            chunks.append(page["text"])
            characters += len(page["text"])
            if characters > self.cursors.max_characters:
                raise local_failure(
                    "text exceeds the bounded public capture cache",
                    "read a smaller explicit text region",
                    "observation_incomplete",
                )
            if page["next"] is None:
                return "".join(chunks)
            if page["next"] <= offset:
                raise local_failure(
                    "native text continuation made no progress", "read this element again", "snapshot_expired"
                )
            offset = page["next"]
