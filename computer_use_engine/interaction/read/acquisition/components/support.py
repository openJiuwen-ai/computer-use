"""Local observation gaps must not hide lost sessions or uncertain cleanup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.errors import ComputerUseError

if TYPE_CHECKING:
    from typing import Awaitable, Callable

    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import T


@dataclass(frozen=True)
class ObservationGap:
    """A component collection failure and the next available observation step.

    Attributes:
        component: Observation component that could not be fully collected.
        code: Structured collection error code.
        detail: Description of the collection failure or incomplete evidence.
        next: Suggested next observation step.
    """

    component: str
    code: str
    detail: str
    next: str


def observation_gap(component: str, error: ComputerUseError) -> ObservationGap:
    return ObservationGap(component, error.code, error.detail, error.next)


async def collect_component(
    port: NativePort, name: str, collect: Callable[[], Awaitable[T]]
) -> tuple[T | None, ObservationGap | None]:
    """Collect one component and convert supported failures into observation gaps.

    Args:
        port: Native I/O port; the stage declares when queries or dispatch may occur.
        collect: Bound dependency for collect; invoked only at its existing stage.
    """
    generation = port.connection.sessionIdentity
    try:
        return await collect(), None
    except ComputerUseError as error:
        if (
            port.connection.sessionIdentity != generation
            or port.calls.retired
            or error.effect != "none"
            or error.code
            not in (
                "observation_incomplete",
                "execution_failed",
                "stale_target",
                "snapshot_expired",
                "mode_unsupported",
            )
        ):
            raise
        return None, observation_gap(name, error)
