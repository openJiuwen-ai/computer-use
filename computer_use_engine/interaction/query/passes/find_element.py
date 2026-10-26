"""Find element."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.query.model import FindLease
from computer_use.native.query.path import resolve_path
from computer_use.pipeline import Contract, Effect, Pass

if TYPE_CHECKING:
    from typing import Awaitable, Callable

    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control


def create(*, port: NativePort) -> Callable[[FindLease, Control], Awaitable[FindLease]]:

    async def resolve(value: FindLease, control: Control) -> FindLease:
        """Resolve the requested path and record the matched binding in the lease.

        The open native lease supplies the query token and remaining call budget.
        The request's timeout bounds waiting for an absent match.
        """
        request, read = (value.plan.request, value.read)
        selected, _ = await resolve_path(
            port,
            read.token,
            value.plan.target,
            read.remaining,
            [dict(segment) for segment in request.path],
            timeout_ms=request.timeout_ms,
        )
        return replace(value, selected=selected)

    return resolve


stage = Pass.factory(
    name="find_element",
    factory=create,
    contract=Contract(FindLease, FindLease, {Effect.QUERY}),
)
