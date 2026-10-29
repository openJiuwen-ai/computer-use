# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Resolve requested path."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from computer_use.interaction.write.model import OpenedWrite, TargetBinding
from computer_use.native.query.path import resolve_path
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Awaitable, Callable

    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control


def create(*, native: NativePort) -> Callable[[OpenedWrite, Control], Awaitable[TargetBinding]]:
    async def bind_path(value: OpenedWrite, control: Control) -> TargetBinding:
        """Resolve the requested path or the container's current receiver.

        Use the active native lease and original query budget for lookup. Return the
        exact binding with the ancestor constraints established by that lookup.
        """
        request, lease = (value.request, value.lease)
        target, within = (lease.target, ())
        if request.forward_scope is not None:
            from computer_use.interaction.write.resolution.current_receiver import CurrentReceiverQuery

            target, within = await CurrentReceiverQuery(
                native, lease.token, request.forward_scope, cast("Callable[[], int]", control.remaining)
            ).resolve(request.intent)
        if request.query_root is not None:
            root = replace(target, node=request.query_root.node)
            target, within = await resolve_path(
                native,
                lease.token,
                root,
                control.remaining,
                [dict(segment) for segment in request.path],
                timeout_ms=request.query_timeout_ms,
            )
        return TargetBinding(value, target, within)

    return bind_path


stage = Pass.factory(
    name="resolve_requested_path",
    factory=create,
    contract=Contract(
        OpenedWrite,
        TargetBinding,
        {Effect.QUERY},
        requires=("active native lease", "original forwarding scope", "scope-bounded current receiver evidence"),
        failures=("lookup unavailable: preserve original scope", "receiver conflict: no requested input sent"),
    ),
)
