from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.native.contracts import Binding
    from computer_use.native.lifecycle import OperationLease
    from computer_use.protocol.records import Record


@dataclass(frozen=True)
class DesktopReadRequest:
    """Desktop image size and call-time limits.

    Attributes:
        max_dimension: Maximum image edge length in pixels, preserving aspect ratio.
        timeout_ms: Maximum duration in milliseconds, bounded by the enclosing call deadline.
    """

    max_dimension: int = 4096
    timeout_ms: int = 30000


@dataclass(frozen=True)
class DesktopLease:
    """Desktop read request and native operation, with its captured result when available.

    Attributes:
        request: Parsed request carrying the original operation scope and options.
        read: Open native operation lease; owns the token and original millisecond deadline.
        result: Native desktop capture payload after pixel collection.
    """

    request: DesktopReadRequest
    read: OperationLease[Binding]
    result: Record | None = None
