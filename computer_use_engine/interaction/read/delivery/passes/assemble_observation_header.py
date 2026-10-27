"""Assemble observation header."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.delivery.model import ObservationDraft
from computer_use.interaction.read.numbering.observation.model import RenderedObservation
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.pipeline.core import Control


def create(*, services: NativeAppReader) -> Callable[[RenderedObservation, Control], ObservationDraft]:
    def header(value: RenderedObservation, control: Control) -> ObservationDraft:
        """Format the acquisition header and build its element-binding and window-alias maps."""
        acquired, rendered = value.acquired, value.rendered
        plan, components = acquired.plan, acquired.components
        target, directory = acquired.target, acquired.directory
        refs = {
            ref: replace(target, node=node, capture=None)
            for ref, node in (rendered.snapshot.refs.items() if rendered else ())
        }
        windows = services.window_aliases(directory)
        # The pipeline shares retained bindings with their owning service.
        # pylint: disable-next=protected-access
        document = services._document(
            plan.selection,
            target,
            components.source_directory,
            directory,
            windows,
            rendered,
            components.image,
            acquired.gaps,
            acquired.scope_confirmed,
            plan.launch,
            image_precedes_tree=components.image_precedes_tree,
        )
        return ObservationDraft(value, refs, windows, document, components.overview)

    return header


stage = Pass.factory(
    name="assemble_observation_header",
    factory=create,
    contract=Contract(
        RenderedObservation,
        ObservationDraft,
        {Effect.PURE, Effect.RESOURCE},
        writes=("session window aliases",),
    ),
)
