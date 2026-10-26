from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.delivery.model import PURE, ObservationDraft
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.pipeline import Contract, Pass

"Append window overview."


if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def overview(draft: ObservationDraft, control: Control) -> ObservationDraft:
    "Append window previews, noting catalogue changes that occurred after they were captured."
    acquired_value, overview_value = draft.source.acquired, draft.overview
    if overview_value is None:
        return draft
    if {item["window"] for item in acquired_value.components.source_directory["surfaces"]} != {
        item["window"] for item in acquired_value.directory["surfaces"]
    }:
        overview_value = replace(
            overview_value,
            document=DisplayDocument.join(
                (
                    overview_value.document,
                    (
                        "Window catalogue changed after these thumbnails; some pictured windows may "
                        "have closed and new windows may not be pictured. Use the current Windows "
                        "list."
                    ),
                )
            ),
        )
    return replace(
        draft, overview=overview_value, document=DisplayDocument.join((draft.document, overview_value.document))
    )


stage = Pass.define(
    name="append_window_overview",
    function=overview,
    contract=Contract(ObservationDraft, ObservationDraft, PURE),
)
