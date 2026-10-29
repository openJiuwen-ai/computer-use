# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Resolve replacement."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

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
    async def resolve_replacement(frame: ResolutionFrame, _: object) -> ResolutionFrame:
        """Resolve the editing target for full-text replacement.

        Inspect the addressed control and, when needed, resolve its confirmed
        internal editor. Record any remaining need for editing-capability evidence
        and attach the selected actuator and evidence to the scope, marking facts
        for reinspection when an internal editor is selected.
        """
        intent, target, operation, remaining = parts(frame)
        capabilities = getattr(native, "capabilities", ())
        facts = await native.inspect(operation, target, remaining(), **frame.scope)
        if not facts.binding.same_target(target):
            raise ValueError("Native replacement inspection returned a different target")
        validate_target(facts, native.policy)
        frame = replace(frame, facts=facts)
        # A declared object edit already owns its native recipient and mutation
        # contract. Editor discovery is evidence for contextual input, not an
        # extra prerequisite imposed by the driver's actuator capability.
        if not has_independent_text_route(facts, intent, frame.request.history):
            if cast("Binding", target).node is None:
                raise ValueError("Full-text replacement requires a specified text control")
            query = ReceiverQuery(native, operation, target, remaining)
            receiver = await query.resolve(
                require_editable=True,
                allow_preparation="nativeEditorPreparation" in capabilities,
                focused=facts.input.focused_node if "nativeActuators" in capabilities else None,
                allow_internal="nativeActuators" in capabilities,
            )
            frame = replace(frame, needs_editing_evidence=receiver.needs_editing_evidence)
            if receiver.actuator is not None:
                require_actuators(capabilities, facts)
                frame = replace(
                    frame, facts=None, scope={**frame.scope, "actuator": receiver.actuator, "basis": query.basis}
                )
        return frame

    return resolve_replacement


stage = Pass.factory(
    name="ReplacementReceiverPipeline",
    factory=create,
    contract=Contract(
        ResolutionFrame,
        ResolutionFrame,
        {Effect.QUERY},
        requires=("pinned original target", "active native lease"),
        failures=("query", "target boundary"),
    ),
)
if TYPE_CHECKING:
    from computer_use.native.contracts import Binding
