# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Enrich focus."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from computer_use.interaction.write.model import ResolutionFrame
from computer_use.interaction.write.resolution.checks import parts
from computer_use.interaction.write.resolution.focus_member import FocusMemberQuery
from computer_use.interaction.write.resolution.focus_scope import FocusScopeQuery
from computer_use.interaction.write.resolution.virtual_focus import VirtualFocusQuery
from computer_use.interaction.write.routes.policy import has_independent_text_route, has_ready_route, validate_target
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Awaitable, Callable

    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control


def create(*, native: NativePort) -> Callable[[ResolutionFrame, Control], Awaitable[ResolutionFrame]]:
    async def enrich_focus(frame: ResolutionFrame, _: object) -> ResolutionFrame:
        """Enrich keyboard input facts with a confirmed focus relation.

        When the target lacks a ready or independent editing route, check supported
        member, popup, and ancestor focus relations. Reinspect the target using the
        first confirmed receiver and retain its evidence in the resolution scope.
        """
        intent, target, operation, remaining = parts(frame)
        facts, scope = (frame.facts, dict(frame.scope))
        capabilities = getattr(native, "capabilities", ())
        matches_intent = (
            True
            if (
                intent.kind in ("focus", "key", "insert", "replace", "paste")
                and {"nativeVirtualFocus", "nativeFocusMembers", "nativeFocusScopes"} & set(capabilities)
                and (cast("Binding", target).node is not None)
                and (not (intent.kind in ("insert", "paste") and intent.parameters.get("text") == ""))
                and (facts.input.focused_node is not None)
                and (not has_ready_route(facts, intent))
                and (not has_independent_text_route(facts, intent, frame.request.history))
            )
            else False
        )
        if matches_intent:
            editor = replace(target, node=facts.actuator.node) if facts.actuator else target
            for capability, query_type in (
                ("nativeFocusMembers", FocusMemberQuery),
                ("nativeVirtualFocus", VirtualFocusQuery),
                ("nativeFocusScopes", FocusScopeQuery),
            ):
                if capability not in capabilities:
                    continue
                query = query_type(native, operation, editor, remaining)
                receiver = await query.resolve(facts.input.focused_node)
                if receiver is None:
                    continue
                scope["receiver"] = receiver
                scope["basis"] = scope.get("basis", ()) + query.basis
                facts = await native.inspect(operation, target, remaining(), **scope)
                if not facts.binding.same_target(target):
                    raise ValueError("Native receiver inspection changed the requested owner")
                validate_target(facts, native.policy)
                break
        return replace(frame, facts=facts, scope=scope)

    return enrich_focus


stage = Pass.factory(
    name="FocusTargetPipeline",
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
