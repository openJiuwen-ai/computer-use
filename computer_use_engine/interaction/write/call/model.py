from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.interaction.records import ElementPath as LookupPath
    from computer_use.interaction.records import Remaining
    from computer_use.native.contracts import Intent, OperationTarget


@dataclass(frozen=True)
class EngineRequest:
    """Write invocation with a new-call timeout or a borrowed call budget.

    Attributes:
        intent: Requested operation and its parameters.
        target: Native target or observed application installation.
        timeout_ms: New-call deadline in milliseconds, used when remaining is absent.
        path: Optional semantic lookup components within the target.
        query_timeout_ms: Maximum element-lookup wait in milliseconds.
        remaining: Borrowed original-call budget callback, when already inside a claim.
        forward_current: Whether to resolve the selected container's current keyboard receiver.
    """

    intent: Intent
    target: OperationTarget
    timeout_ms: int = 30000
    path: LookupPath | None = None
    query_timeout_ms: int = 0
    remaining: Remaining | None = None
    forward_current: bool = False
