from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.choices.model import WindowChoiceDraft
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.pipeline import Contract, Effect, Pass

"Format window choices."


if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.interaction.read.model import WindowSelectionRequired
    from computer_use.pipeline.core import Control


def create(*, services: NativeAppReader) -> Callable[[WindowChoiceDraft, Control], WindowSelectionRequired]:
    def format_choice(value: WindowChoiceDraft, control: Control) -> WindowSelectionRequired:
        """Build the current window-selection response from catalogue and preview evidence.

        Validate any fixed root against the refreshed directory. When the catalogue changed,
        annotate the previews and selection error before returning the available choices.
        """
        from computer_use.interaction.read.app import failure
        from computer_use.interaction.read.model import WindowSelectionRequired

        request, overview, current = value.request, value.overview, value.current
        if request.selection is not None:
            services._selection(
                request.application,
                request.name,
                current,
                request.selection.root.root_window,
                previous=request.selection,
            )
        error = request.error
        if request.directory["surfaces"] != current["surfaces"]:
            if overview is not None:
                overview = replace(
                    overview,
                    document=DisplayDocument.join(
                        (
                            overview.document,
                            (
                                "Window catalogue changed after these thumbnails; some pictured windows may "
                                "have closed and new windows may not be pictured. Use the current Windows "
                                "list."
                            ),
                        )
                    ),
                )
            error = failure(
                error.code,
                ("The window catalogue changed during observation; select an entry from the current Windows list."),
            )
        return WindowSelectionRequired(
            error, request.application, request.name, current, overview=overview, selection=request.selection
        )

    return format_choice


stage = Pass.factory(
    name="format_window_choices",
    factory=create,
    contract=Contract(WindowChoiceDraft, object, {Effect.PURE}),
)
