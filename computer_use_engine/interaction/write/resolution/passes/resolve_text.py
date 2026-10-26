"""Resolve text."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.write.model import ResolutionFrame
from computer_use.interaction.write.resolution.checks import parts, require_actuators
from computer_use.interaction.write.resolution.receiver import ReceiverQuery
from computer_use.interaction.write.routes.policy import has_independent_text_route, validate_target
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Awaitable, Callable

    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control


def create(*, native: NativePort) -> Callable[[ResolutionFrame, Control], Awaitable[ResolutionFrame]]:
    async def resolve_text(frame: ResolutionFrame, _: object) -> ResolutionFrame:
        """Resolve the receiver of an addressed keyboard operation.

        Inspect the control, requiring editing semantics for insertion and paste, and
        resolve its internal editor when supported. Record any remaining need for
        editing-capability evidence and the selected actuator, marking facts for
        reinspection when an internal editor is selected.
        """
        intent, target, operation, remaining = parts(frame)
        capabilities = getattr(native, "capabilities", ())
        facts = await native.inspect(operation, target, remaining(), **frame.scope)
        if not facts.binding.same_target(target):
            raise ValueError("Native receiver inspection returned a different target")
        validate_target(facts, native.policy)
        # Native insertion may address the object and its own known selection
        # directly. Do not require an unrelated keyboard/editor tree for that
        # contract. Candidate filtering preserves paste/key/focus semantics.
        if has_independent_text_route(facts, intent, frame.request.history):
            return replace(frame, facts=facts)
        query = ReceiverQuery(native, operation, target, remaining)
        receiver = await query.resolve(
            require_editable=intent.kind in ("insert", "paste"),
            allow_preparation="nativeEditorPreparation" in capabilities and intent.kind in ("insert", "paste"),
            focused=facts.input.focused_node if "nativeActuators" in capabilities else None,
            allow_internal="nativeActuators" in capabilities,
        )
        frame = replace(frame, facts=facts, needs_editing_evidence=receiver.needs_editing_evidence)
        if receiver.actuator is not None:
            require_actuators(capabilities, facts)
            frame = replace(
                frame, facts=None, scope={**frame.scope, "actuator": receiver.actuator, "basis": query.basis}
            )
        return frame

    return resolve_text


stage = Pass.factory(
    name="TextReceiverPipeline",
    factory=create,
    contract=Contract(
        ResolutionFrame,
        ResolutionFrame,
        {Effect.QUERY},
        requires=("pinned original target", "active native lease"),
        failures=("query", "target boundary"),
    ),
)
