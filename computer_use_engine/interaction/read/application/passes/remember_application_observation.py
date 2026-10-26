"""Remember application observation."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.interaction.read.application.model import AppReadResult
from computer_use.interaction.read.model import MenuAppObservation
from computer_use.interaction.read.publication.model import PublicationRequest
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Callable, Mapping

    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.native.contracts import Binding
    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts
    from computer_use.session.state import AppState


def create(
    *,
    catalogue: Callable[[AppState, DirectoryFacts, Mapping[str, str]], None],
    register_window_image: Callable[[AppState, AppObservation], None],
    remember_snapshot: Callable[[AppState, Binding, SemanticSnapshot], None],
) -> Callable[[AppReadResult, Control], PublicationRequest]:

    def remember(value: AppReadResult, control: Control) -> PublicationRequest:
        """Retain acquired application or menu evidence and prepare its publication inputs.

        Update scope, catalogue, snapshot, and image state for the observed result. Return the
        primary target/tree and any additional menu trees needed by observation publication.
        """
        state, observed, request = (
            value.source.state,
            cast("AppObservation | MenuAppObservation", value.observed),
            value.source.request,
        )
        if isinstance(observed, MenuAppObservation):
            state.selection = observed.selection
            catalogue(state, observed.directory, observed.windows)
            state.query_target, state.query_scope_kind = (observed.menu.target, observed.menu.scope_kind)
            for part in observed.menu.parts:
                remember_snapshot(state, part.target, part.tree)
            first = observed.menu.parts[0]
            return PublicationRequest(
                request.owner,
                state,
                observed.document,
                first.tree,
                first.target,
                full=True,
                view="menu",
                extra_trees=observed.menu.observations[1:],
            )
        state.selection, state.observation = (observed.selection, observed)
        state.query_target, state.query_scope_kind = (observed.target, "window")
        catalogue(state, observed.directory, observed.windows)
        state.delivered, state.capture_expired = (None, False)
        if observed.tree is not None:
            remember_snapshot(state, observed.target, observed.tree)
        if observed.menus is not None:
            for part in observed.menus.parts:
                remember_snapshot(state, part.target, part.tree)
        if observed.image is not None:
            register_window_image(state, observed)
        return PublicationRequest(
            request.owner,
            state,
            observed.document,
            observed.tree,
            observed.target,
            observed.image.image if observed.image else None,
            full=request.full,
            extra_trees=observed.menus.observations if observed.menus else (),
            overview=observed.overview,
        )

    return remember


stage = Pass.factory(
    name="remember_application_observation",
    factory=create,
    contract=Contract(AppReadResult, PublicationRequest, {Effect.RESOURCE}),
)
if TYPE_CHECKING:
    from computer_use.interaction.read.model import AppObservation
