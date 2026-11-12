# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import time
import weakref
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event
from types import MethodType
from uuid import uuid4

# Private variables hold mutable session state.
# pylint: disable-next=invalid-name
_guard = None
_nested = ContextVar("computer_use_nested", default=False)
# pylint: disable-next=invalid-name
_revision = 0
# pylint: disable-next=invalid-name
_active = 0
_budget = ContextVar("computer_use_operation_budget", default=None)
_cancellation_source = ContextVar("computer_use_operation_cancellation", default=None)
OPERATION_TARGET_MS = 1000
_NATIVE_CLOCK = time.monotonic
_EVENT_IS_SET = Event.is_set


class OperationDeadlineExceeded(TimeoutError):
    """The original SDK operation deadline expired, not a new component timer."""


class OperationBudget:
    """Shared timing, cancellation and an optional explicit operation deadline.

    Ordinary SDK operations have no total time cutoff. Native claims retain
    their own timeouts; an explicitly supplied enclosing deadline narrows them.
    Completed receipts and mandatory cleanup remain publishable after expiry;
    a late completed result is recorded as an overrun rather than erased.
    """

    def __init__(self, timeout_ms=None, *, cancelled=None, clock=time.monotonic):
        if timeout_ms is not None and (type(timeout_ms) is not int or timeout_ms < 1):
            raise ValueError("Invalid SDK operation budget")
        self._clock = clock
        self.started = clock()
        self.deadline = self.started + timeout_ms / 1000 if timeout_ms is not None else None
        self.timeout_ms = timeout_ms
        self.target_ms = min(OPERATION_TARGET_MS, timeout_ms) if timeout_ms is not None else OPERATION_TARGET_MS
        self.target_deadline = self.started + self.target_ms / 1000
        self.operation_id = uuid4().hex
        self.cancelled = cancelled or (lambda: False)

    @property
    def elapsed_ms(self):
        return (self._clock() - self.started) * 1000

    @property
    def expired(self):
        return self.deadline is not None and self._clock() >= self.deadline

    @property
    def target_exceeded(self):
        return self._clock() >= self.target_deadline

    def target_available_ms(self):
        """Optional settling uses the latency target, without renewing the call."""
        if self.cancelled():
            raise asyncio.CancelledError("SDK operation cancelled")
        return max(0, int((self.target_deadline - self._clock()) * 1000))

    def available_ms(self) -> int | None:
        """Return remaining explicit time, or None; always honor host cancellation."""
        if self.cancelled():
            raise asyncio.CancelledError("SDK operation cancelled")
        if self.deadline is None:
            return None
        return max(0, int((self.deadline - self._clock()) * 1000))

    def remaining(self) -> int | None:
        value = self.available_ms()
        if value is not None and value < 1:
            raise OperationDeadlineExceeded(f"SDK operation exhausted its original {self.timeout_ms} ms budget")
        return value

    def checkpoint(self):
        """Check before new work or within a bounded pure-work batch."""
        self.remaining()


def current_operation_budget():
    """Return this call's clock without creating or extending a deadline."""
    return _budget.get()


_BUDGET_REMAINING = OperationBudget.remaining
_BUDGET_AVAILABLE = OperationBudget.available_ms


def _native_checkpoint(checkpoint):
    """Identify SDK budget callbacks whose behavior is independent of caller frames."""
    if checkpoint is None:
        return True
    if type(checkpoint) is not MethodType or checkpoint.__func__ is not _BUDGET_REMAINING:
        return False
    budget = checkpoint.__self__
    if type(budget) is not OperationBudget or budget is not _budget.get():
        return False
    available = budget.available_ms
    return (
        type(available) is MethodType
        and available.__func__ is _BUDGET_AVAILABLE
        and budget._clock is _NATIVE_CLOCK
        and budget.cancelled is getattr(budget, "_native_cancelled", None)
    )


@contextmanager
def cancellation_scope(cancelled):
    """Borrow the host's original thread-safe cancellation event predicate."""
    token = _cancellation_source.set(cancelled or _cancellation_source.get())
    try:
        yield
    finally:
        _cancellation_source.reset(token)


@contextmanager
def operation_budget(timeout_ms=None):
    """Share one call context; impose a total deadline only when explicitly supplied."""
    existing = current_operation_budget()
    if existing is not None:
        yield existing
        return
    source = _cancellation_source.get()
    task = asyncio.current_task()
    task_ref = weakref.ref(task) if task is not None else lambda: None

    def cancelled():
        active = task_ref()
        return bool(source is not None and source() or active is not None and active.cancelling())

    budget = OperationBudget(timeout_ms, cancelled=cancelled)
    if (task is None or type(task) is asyncio.Task) and (
        source is None
        or (
            type(source) is MethodType
            and source.__func__ is _EVENT_IS_SET
            and type(source.__self__) is Event
        )
    ):
        budget._native_cancelled = cancelled
    token = _budget.set(budget)
    try:
        yield current_operation_budget()
    finally:
        _budget.reset(token)


def operation_checkpoint():
    captured, idle = _revision, _active == 0
    return lambda: idle and _active == 0 and _revision == captured


def set_execution_guard(guard):
    global _guard
    _guard = guard


async def operation(run):
    global _active, _revision
    if _nested.get():
        return await run()
    done = _guard() if _guard is not None else None
    _active += 1
    _revision += 1
    token = _nested.set(True)
    try:
        return await run()
    finally:
        _nested.reset(token)
        _active -= 1
        _revision += 1
        if done:
            done()
