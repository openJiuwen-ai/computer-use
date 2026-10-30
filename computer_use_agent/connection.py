# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""One stdio MCP session, owned and closed by the same asyncio task."""

import asyncio
import logging
import os
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

from .config import McpConfig

logger = logging.getLogger(__name__)
_DESKTOP_ENV = (
    "SSH_AUTH_SOCK",
    "DISPLAY",
    "XAUTHORITY",
    "DBUS_SESSION_BUS_ADDRESS",
    "XDG_RUNTIME_DIR",
    "XDG_SESSION_TYPE",
    "WAYLAND_DISPLAY",
    "COMPUTER_USE_PACKAGES_DIR",
    "COMPUTER_USE_FOREGROUND_ASSIST",
)


class McpConnection:
    def __init__(self, config: McpConfig, *, stderr=None):
        self.config, self.stderr = config, stderr
        self.session = None
        self.info = None
        self._task = None
        self._ready = None
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()
        self._active = None
        self._closed = False
        self._needs_reset = False

    async def start(self):
        if self._closed:
            raise RuntimeError("Computer Use connection is closed; create a new toolkit.")
        if self._task is None:
            self._ready = asyncio.get_running_loop().create_future()
            self._task = asyncio.create_task(self._serve())
        await asyncio.shield(self._ready)
        if self._task.done():
            await self._task
            raise RuntimeError("Computer Use connection ended; previous actions may have executed.")

    async def _serve(self):
        environment = {
            **get_default_environment(),
            **{key: os.environ[key] for key in _DESKTOP_ENV if key in os.environ},
            **self.config.env,
        }
        params = StdioServerParameters(
            command=self.config.command,
            args=list(self.config.args),
            env=environment,
            cwd=self.config.cwd,
        )
        try:
            async with AsyncExitStack() as stack:
                streams = await stack.enter_async_context(
                    stdio_client(params, **({"errlog": self.stderr} if self.stderr is not None else {}))
                )
                self.session = await stack.enter_async_context(ClientSession(*streams))
                self.info = await self.session.initialize()
                self._ready.set_result(None)
                await self._stop.wait()
        except BaseException as error:
            if not self._ready.done():
                self._ready.set_exception(error)
            elif not isinstance(error, asyncio.CancelledError):
                logger.exception("Computer Use MCP connection ended")
                raise RuntimeError("Computer Use MCP connection ended; previous actions may have executed.") from error
        finally:
            self.session = None

    async def call(self, name, arguments):
        async with self._lock:
            await self.start()
            if self._needs_reset and name != "computer_use_reset":
                reset = await self._invoke("computer_use_reset", {})
                if reset.isError:
                    return reset
            return await self._invoke(name, arguments)

    async def _invoke(self, name, arguments):
        self._active = asyncio.create_task(self.session.call_tool(name, arguments=arguments))
        try:
            result = await self._active
            if name == "computer_use_reset" and not result.isError:
                self._needs_reset = False
            return result
        except asyncio.CancelledError:
            # Cancellation notifications have no cleanup acknowledgement. The
            # reset request joins server cleanup before a later script is sent.
            self._needs_reset = True
            raise
        finally:
            self._active = None

    async def close(self):
        if self._closed:
            return
        self._closed = True
        await self.cancel_current()
        self._stop.set()
        if self._task is not None:
            try:
                async with asyncio.timeout(10):
                    await asyncio.shield(self._task)
            except TimeoutError:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
            finally:
                if self._ready.done() and not self._ready.cancelled():
                    self._ready.exception()

    async def cancel_current(self):
        active = self._active
        if active is not None:
            active.cancel()
            await asyncio.gather(active, return_exceptions=True)
