# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Resolve text."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.write.model import ResolutionFrame
from computer_use.interaction.write.resolution.checks import parts, require_actuators
from computer_use.interaction.write.resolution.receiver import ReceiverQuery
from computer_use.interaction.write.routes.policy import (
    has_exact_keyboard_edit_route,
    has_independent_text_route,
    route_key,
    validate_target,
)
from computer_use.native.contracts import Channel, Knowledge, UnitCommand
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

        def ready_direct_unit(unit: str) -> bool:
            return facts.actuator is None and facts.receiver is None and any(
                primitive.unit == unit
                and primitive.supported == Knowledge.YES
                and primitive.readiness == Knowledge.YES
                and not primitive.pending
                and route_key(UnitCommand(primitive, target, intent)) not in frame.request.history.unavailable
                for primitive in facts.primitives
            )

        if intent.kind in ("key", "focus") and ready_direct_unit(
            "key_sequence" if intent.kind == "key" else "prepare_receiver"
        ):
            # Unrelated child-tree completeness is not a prerequisite for
            # a key or focus contract already bound to the exact recipient.
            return replace(frame, facts=facts)
        # Native insertion may address the object and its own known selection
        # directly. Do not require an unrelated keyboard/editor tree for that
        # contract. Candidate filtering preserves paste/key/focus semantics.
        if has_independent_text_route(facts, intent, frame.request.history) or has_exact_keyboard_edit_route(
            facts, intent, frame.request.history
        ):
            return replace(frame, facts=facts)
        exact_ready_keyboard = (
            facts.platform == "windows"
            and intent.kind in ("insert", "paste")
            and facts.native_role == "ControlType.Text"
            and facts.input.focused_node == target.node
            and facts.input.keyboard_surface == target.surface
            and ready_direct_unit("key_sequence" if intent.kind == "insert" else "clipboard_paste")
        )
        query = ReceiverQuery(native, operation, target, remaining)
        receiver = await query.resolve(
            require_editable=intent.kind in ("insert", "paste"),
            allow_preparation="nativeEditorPreparation" in capabilities and intent.kind in ("insert", "paste"),
            focused=facts.input.focused_node if "nativeActuators" in capabilities else None,
            allow_internal="nativeActuators" in capabilities,
            keyboard_input=intent.kind in ("insert", "paste"),
            declared_cell_preparation=intent.kind == "insert"
            and facts.platform == "macos"
            and facts.native_role == "AXCell"
            and any(
                primitive.unit == "prepare_receiver"
                and primitive.channel == Channel.EDITOR
                and primitive.supported == Knowledge.YES
                and primitive.background_preparation == Knowledge.YES
                and route_key(UnitCommand(primitive, target, intent)) not in frame.request.history.unavailable
                for primitive in facts.primitives
            ),
            exact_ready_keyboard=exact_ready_keyboard,
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
