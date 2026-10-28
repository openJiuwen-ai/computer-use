"""One call budget, stage ownership and cleanup state for native reads and writes."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Generic, Self, TypeVar
from uuid import uuid4

from computer_use.errors import ComputerUseError
from computer_use.native.contracts import Cleanup, OperationTarget
from computer_use.pipeline import Control, Effect

LeaseTarget = TypeVar("LeaseTarget", bound=OperationTarget)


@dataclass(frozen=True)
class OperationLease(Generic[LeaseTarget]):
    """Retain an admitted native operation, its target, and its call budget.

    Attributes:
        token: Opaque operation identity allocated by the driver.
        target: Exact canonical target admitted for this operation.
        remaining: Milliseconds left on the original call; raises on expiry.
    """

    token: str
    target: LeaseTarget
    remaining: Callable[[], int]

    def bounded(self, timeout_ms: int) -> Self:
        """Limit a component to the shorter of its timeout and the call budget.

        Args:
            timeout_ms: Maximum component duration in milliseconds.

        Returns:
            A lease with the same target/token and a narrower budget callback.
        """
        # Exact types preserve scalar validation and identity contracts.
        # pylint: disable-next=unidiomatic-typecheck
        if type(timeout_ms) is not int or timeout_ms < 1:
            raise ValueError("Invalid observation component budget")
        deadline = time.monotonic() + timeout_ms / 1000

        def remaining() -> int:
            value = min(self.remaining(), int((deadline - time.monotonic()) * 1000))
            if value < 1:
                raise TimeoutError("Native observation component budget exhausted")
            return value

        return replace(self, remaining=remaining)


class CallLifecycle:
    """Coordinate call deadlines, native operation admission, and cleanup.

    A claim assigns one task a shared budget. Its read and write operations use
    that budget and release their native resources on exit. Uncertain admission
    or cleanup retires the session so later calls start with a fresh driver.
    """

    def __init__(self, port: NativePort) -> None:
        self.port = port
        self.busy = self.retired = False
        self._remaining = None
        self._active = False
        self._owner = None
        self._admission = None
        self._call_id = None

    def control(self, remaining: Callable[[], int]) -> Control:
        try:
            sink = getattr(self.port, "pipeline_diagnostic", None)
        except BaseException:
            sink = None
        return Control(remaining=remaining, diagnostic=sink if callable(sink) else None, call_id=self._call_id)

    @contextmanager
    def claim(self, timeout_ms: int) -> Iterator[Callable[[], int]]:
        # pylint: disable-next=unidiomatic-typecheck
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= 120000:
            raise ValueError("Invalid native call time budget")
        if self.retired:
            raise RuntimeError("Native session is retired; prior admission or cleanup was unconfirmed")
        if self.busy:
            raise RuntimeError("Another native call is busy")
        self.busy = True
        self._owner = asyncio.current_task()
        deadline = time.monotonic() + timeout_ms / 1000

        def remaining() -> int:
            value = int((deadline - time.monotonic()) * 1000)
            if value < 1:
                raise TimeoutError("Native call budget exhausted")
            return value

        self._remaining = remaining
        # Every stage within this claim shares the same diagnostic call ID.
        try:
            self._call_id = uuid4().hex
        except Exception:
            self._call_id = None
        trace = self.control(remaining).child("CallScope")
        trace.emit("enter", frozenset({Effect.RESOURCE}))
        try:
            yield remaining
        finally:
            trace.emit("exit", frozenset({Effect.RESOURCE}))
            self._call_id = None
            self._remaining = None
            self._owner = None
            self.busy = False

    def validate_claim(self, remaining: Callable[[], int]) -> None:
        invalid_remaining = (
            True
            if (
                self.retired
                or self._active
                or remaining is not self._remaining
                or not self.busy
                or self._owner is not asyncio.current_task()
            )
            else False
        )
        if invalid_remaining:
            raise RuntimeError("Native stage requires its active, idle call claim and owning task")

    def check_begin(self, intent: str, mode: str, target: OperationTarget) -> None:
        """Consume the active task's admission for its intent, mode, and target."""
        if self.retired or self.busy and self._admission != (asyncio.current_task(), intent, mode, target):
            raise ComputerUseError(
                dict(
                    code="execution_unknown" if self.retired else "invalid_request",
                    effect="none",
                    target="native call",
                    detail="The native session is retired." if self.retired else "Another native call owns this stage.",
                    next="close the old driver and observe a new session" if self.retired else "await the active call",
                )
            )
        self._admission = None

    @asynccontextmanager
    async def open(self, target: OperationTarget | None, timeout_ms: int) -> AsyncIterator[OperationLease[Any]]:
        with self.claim(timeout_ms) as remaining:
            async with self.operation(target, remaining) as read:
                yield read

    @asynccontextmanager
    async def operation(
        self,
        target: OperationTarget | None,
        remaining: Callable[[], int],
        *,
        intent: Intent | None = None,
    ) -> AsyncIterator[OperationLease[Any]]:
        """Open and finish a native stage within the active call claim.

        Args:
            target: Observed scope, or None to connect before opening a read.
            remaining: Budget callback yielded by this task's active claim.
            intent: Requested write intent; None opens a read operation.

        Yields:
            The admitted target, native operation token, and shared budget.
        """
        self.validate_claim(remaining)
        if intent is not None and target is None:
            raise ValueError("A write operation needs an observed target")
        self._active = True
        operation = None
        primary_error = None
        try:
            if target is None:
                target = await self.port.connect(remaining())
            # Check time before begin so local expiry leaves admission known.
            budget = remaining()
            try:
                self._admission = (
                    asyncio.current_task(),
                    intent.kind if intent else "read",
                    "write" if intent else "read",
                    target,
                )
                opened = (
                    await self.port.begin(intent, target, budget)
                    if intent is not None
                    else await self.port.begin_read(target, budget)
                )
                operation = opened.token
                target = target.canonical(opened.target)
            except BaseException as error:
                # Retire the connection when native admission may have occurred.
                if not isinstance(error, ComputerUseError) or error.effect != "none":
                    self.retired = True
                raise
            finally:
                self._admission = None
            yield OperationLease(operation, target, remaining)
            if intent is None:
                remaining()
        except BaseException as error:
            primary_error = error
            raise
        finally:
            try:
                if operation is not None:
                    cleanup = await self.port.finish(operation)
                    self.control(remaining).child("NativeOperationScope").emit(
                        "cleanup",
                        frozenset({Effect.RESOURCE}),
                        cleanup=cleanup.value
                        if isinstance(cleanup, Cleanup)
                        else cleanup
                        if isinstance(cleanup, str)
                        else None,
                    )
                    if cleanup != Cleanup.COMPLETE:
                        self.retired = True
                        raise RuntimeError("Native operation cleanup is unconfirmed")
            except BaseException as error:
                self.control(remaining).child("NativeOperationScope").emit(
                    "cleanup_error", frozenset({Effect.RESOURCE}), error
                )
                self.retired = True
                if primary_error is None:
                    raise
                primary_error.add_note("Native operation cleanup unconfirmed: " + str(error))
            finally:
                self._active = False


if TYPE_CHECKING:
    from typing import Any, AsyncIterator, Iterator

    from computer_use.native.contracts import Intent
    from computer_use.native.port import NativePort
