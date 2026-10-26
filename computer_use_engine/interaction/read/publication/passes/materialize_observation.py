"""Materialize observation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.publication.model import PublicationText, PublishedObservation
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.protocol.primitives import frozen
from computer_use.session.observation import lazyObservation
from computer_use.session.observation_data import retainObservationData

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.acquisition.images.overview import WindowOverview
    from computer_use.native.contracts import Binding
    from computer_use.pipeline.core import Control
    from computer_use.protocol.png import PNG
    from computer_use.session.state import AppState


def create(
    *,
    overview_image: Callable[[WindowOverview | None], PNG | None],
    window_ref: Callable[[AppState, Binding], str | None],
) -> Callable[[PublicationText, Control], PublishedObservation]:

    def materialize(value: PublicationText, control: Control) -> PublishedObservation:
        """Create the public observation and its publication event.

        Retain the supplied text, images, source roots, element aliases, and window mappings.
        Build event metadata identifying the owner, scope, requested view, and source capture.
        """
        source, request = (value.source, value.source.request)
        result = lazyObservation(
            value.document, request.image, details_document=value.details, overview=overview_image(request.overview)
        )
        trees = tuple((snapshot for _, snapshot in source.observations))
        refs = {ref: node for snapshot in trees for ref, node in snapshot.refs.items()}
        retainObservationData(
            result,
            [root for snapshot in trees for root in snapshot.roots],
            refs,
            request.state.windows,
            request.state.selection.root.root_window if request.state.selection else None,
        )
        event = dict(
            owner=request.owner,
            scope=request.target.surface,
            instance=request.target.root_window,
            selector=request.state.selector,
            window=window_ref(request.state, request.target),
            view=request.view,
            state=result,
            refs=frozen(refs),
            full=request.full,
            document=value.document,
        )
        if request.tree is not None:
            event["observed"] = request.tree.facts["observed"]
        return PublishedObservation(result, event)

    return materialize


stage = Pass.factory(
    name="materialize_observation",
    factory=create,
    contract=Contract(PublicationText, PublishedObservation, {Effect.RESOURCE}),
)
