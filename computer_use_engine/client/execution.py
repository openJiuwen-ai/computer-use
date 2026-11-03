# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Synchronous public calls over one persistent, owned asynchronous session."""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import threading
import weakref
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

_executor = ContextVar("computer_use_executor", default=None)
_cancellation = ContextVar("computer_use_cancellation", default=None)
_executors = weakref.WeakSet()


class ExecutionCancellation:
    def __init__(self):
        self.cancelled = threading.Event()
        self._tasks = set()

    def cancel(self):
        if self.cancelled.is_set():
            return
        self.cancelled.set()
        for task in tuple(self._tasks):
            task.get_loop().call_soon_threadsafe(task.cancel)


@contextmanager
def execution_scope(executor, cancellation=None):
    owner = _executor.set(executor)
    cancel = _cancellation.set(cancellation)
    try:
        yield
    finally:
        _cancellation.reset(cancel)
        _executor.reset(owner)


def session_executor():
    return _executor.get() or SessionExecutor()


class SessionExecutor:
    def __init__(self, loop=None):
        self.loop = loop
        self._external = loop is not None
        self._thread = None
        self._start_lock = threading.Lock()
        self._closed = False
        self._closing = False
        self._tasks = set()
        self._resources = {}
        _executors.add(self)

    @property
    def is_owner(self):
        try:
            return asyncio.get_running_loop() is self.loop
        except RuntimeError:
            return False

    def _start(self):
        with self._start_lock:
            if self._closed or self._closing:
                raise RuntimeError("Computer session is closed")
            if self.loop is not None:
                return
            ready = threading.Event()

            def serve():
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)
                ready.set()
                try:
                    self.loop.run_forever()
                finally:
                    self.loop.run_until_complete(self.loop.shutdown_asyncgens())
                    self.loop.close()

            self._thread = threading.Thread(target=serve, name="computer-use-session", daemon=True)
            self._thread.start()
            ready.wait()

    def _wait(self, factory):
        self._start()
        cancellation = _cancellation.get()
        if cancellation is not None and cancellation.cancelled.is_set():
            raise asyncio.CancelledError()

        async def invoke():
            if self._closed or self._closing:
                raise RuntimeError("Computer session is closed")
            if cancellation is not None and cancellation.cancelled.is_set():
                raise asyncio.CancelledError()
            task = asyncio.current_task()
            self._tasks.add(task)
            if cancellation is not None:
                cancellation._tasks.add(task)
            try:
                return await factory()
            finally:
                self._tasks.discard(task)
                if cancellation is not None:
                    cancellation._tasks.discard(task)

        future = asyncio.run_coroutine_threadsafe(invoke(), self.loop)
        try:
            return future.result()
        except concurrent.futures.CancelledError:
            raise asyncio.CancelledError() from None
        except KeyboardInterrupt:
            future.cancel()
            raise

    def run(self, factory):
        if self.is_owner:
            raise RuntimeError(
                "Synchronous SDK calls cannot block their session event loop; run the script in a worker thread"
            )
        return self._wait(factory)

    def call(self, function, *args, **kwargs):
        if self._closed:
            raise RuntimeError("Computer session is closed")
        if self.is_owner:
            return function(*args, **kwargs)

        async def invoke():
            return function(*args, **kwargs)

        return self._wait(invoke)

    def register(self, controller):
        if self._closed or self._closing:
            raise RuntimeError("Computer session is closed")
        self._resources[id(controller)] = controller

    async def close_resource(self, controller):
        if id(controller) not in self._resources:
            return
        controller._sdk_closed = True
        transport = controller.transport
        if hasattr(transport, "close"):
            transport.close()
        if hasattr(transport, "wait_closed"):
            await transport.wait_closed()
        self._resources.pop(id(controller), None)

    async def drain(self, cancellation):
        while cancellation._tasks:
            await asyncio.gather(*tuple(cancellation._tasks), return_exceptions=True)

    async def aclose(self):
        if self._closed:
            return
        self._closing = True
        current = asyncio.current_task()
        tasks = [task for task in self._tasks if task is not current]
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            for controller in tuple(self._resources.values()):
                await self.close_resource(controller)
            self._closed = True
        finally:
            self._closing = False

    def close(self):
        if self._closed:
            return
        if self.is_owner:
            raise RuntimeError("Use aclose() from the session event loop")
        if self.loop is not None and not self.loop.is_closed():
            self.run(self.aclose)
            if not self._external:
                self.loop.call_soon_threadsafe(self.loop.stop)
                self._thread.join()
        self._closed = True


def synchronous(function):
    """A public method returns a value, never a coroutine or a Future."""

    @wraps(function)
    def invoke(self, *args, **kwargs):
        async def operation():
            if getattr(self._controller, "_sdk_closed", False):
                raise RuntimeError("Computer session is closed")
            return await function(self, *args, **kwargs)

        return self._executor.run(operation)

    return invoke


def on_session(function):
    @wraps(function)
    def invoke(self, *args, **kwargs):
        def call():
            if getattr(self._controller, "_sdk_closed", False):
                raise RuntimeError("Computer session is closed")
            return function(self, *args, **kwargs)

        return self._executor.call(call)

    return invoke


@atexit.register
def _close_executors():
    for executor in tuple(_executors):
        if not executor._external:
            executor.close()
