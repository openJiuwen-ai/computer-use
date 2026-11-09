# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""MCP protocol endpoint in the parent process: tools, capabilities, and request validation."""

import argparse
import asyncio
import logging
import signal
import sys

from computer_use.diagnostics import SDK_VERSION
from computer_use.native.preview import latest_preview_sink
from computer_use.protocol.primitives import trim
from computer_use.protocol.schema import obj, string

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

from .instructions import CODE_DESCRIPTION, DESCRIPTION, RESET_DESCRIPTION, TITLE_DESCRIPTION
from .session import ExecutionSession

_input = obj(dict(title=string(min=1, max=200).optional(), code=string(min=1, max=65536)))
_empty = obj({})

_LOG = logging.getLogger(__name__)


def capabilities():
    platform = "macos" if sys.platform == "darwin" else "windows" if sys.platform == "win32" else sys.platform
    return types.ServerCapabilities(
        tools=types.ToolsCapability(),
        experimental={
            "computer-use-host": dict(version=1, platform=platform),
            "computer-use-preview": dict(version=2),
            "computer-use-demo-foreground": dict(version=1),
        },
    )


def _supports(client_capabilities, key, version):
    value = client_capabilities.get(key, {})
    return isinstance(value, dict) and type(value.get("version")) in (int, float) and value["version"] == version


class ComputerUseServer:
    def __init__(self, *, allow_global_input=True):
        if allow_global_input is not True and allow_global_input is not False:
            raise TypeError("allow_global_input must be a boolean")
        self.allow_global_input = allow_global_input
        self.server = Server("computer-use", version=SDK_VERSION)
        self.session = None

        @self.server.list_tools()
        async def list_tools():
            return [
                types.Tool(
                    name="computer_use",
                    description=DESCRIPTION,
                    inputSchema=dict(
                        type="object",
                        properties=dict(
                            title=dict(
                                type="string",
                                minLength=1,
                                maxLength=200,
                                description=TITLE_DESCRIPTION,
                            ),
                            code=dict(
                                type="string",
                                minLength=1,
                                maxLength=65536,
                                description=CODE_DESCRIPTION,
                            ),
                        ),
                        required=["code"],
                        additionalProperties=False,
                    ),
                    annotations=types.ToolAnnotations(
                        readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
                    ),
                ),
                types.Tool(
                    name="computer_use_reset",
                    description=RESET_DESCRIPTION,
                    inputSchema=dict(type="object", properties={}, additionalProperties=False),
                    annotations=types.ToolAnnotations(
                        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
                    ),
                ),
            ]

        @self.server.call_tool(validate_input=False)
        async def call_tool(name, arguments):
            if name == "computer_use_reset":
                _empty.parse(arguments)
                result = (
                    await self.session.reset()
                    if self.session is not None
                    else dict(
                        content=[
                            dict(
                                type="text",
                                text="Execution session: inactive. Capture streams: inactive.",
                            )
                        ]
                    )
                )
                return types.CallToolResult.model_validate(result)
            if name != "computer_use":
                raise ValueError("Unknown tool: " + name)
            # Validate UTF-16 lengths consistently with the existing protocol;
            # the SDK's JSON Schema validation uses Python character counts.
            arguments = dict(arguments)
            if isinstance(arguments.get("title"), str):
                arguments["title"] = trim(arguments["title"])
            args = _input.parse(arguments)
            if not trim(args["code"]):
                raise ValueError("code must not be blank")
            context = self.server.request_context
            client = context.session.client_params
            experimental = client.capabilities.experimental or {} if client is not None else {}
            if self.session is None:

                async def preview(event):
                    notice = types.ServerNotification.model_construct(
                        root=types.Notification(method="notifications/computer-use/preview", params=event)
                    )
                    await context.session.send_notification(notice)

                self.session = ExecutionSession(
                    45000,
                    latest_preview_sink(preview) if _supports(experimental, "computer-use-preview", 2) else None,
                    _supports(experimental, "computer-use-demo-foreground", 1)
                    and experimental["computer-use-demo-foreground"].get("enabled") is True,
                    allow_global_input=self.allow_global_input,
                )
            metadata = context.meta.model_dump() if context.meta is not None else {}
            progress_token = metadata.get("progressToken")
            progress = 0

            async def report_progress():
                nonlocal progress
                progress += 1
                try:
                    await context.session.send_progress_notification(
                        progress_token, progress, related_request_id=context.request_id
                    )
                except Exception:
                    _LOG.debug("Progress notification could not be delivered.", exc_info=True)

            result = await self.session.execute(
                args["code"],
                title=args.get("title"),
                on_progress=report_progress if progress_token is not None else None,
            )
            return types.CallToolResult.model_validate(result)

    async def close(self):
        if self.session is not None:
            await asyncio.shield(self.session.close())

    async def run(self):
        try:
            async with stdio_server() as (read, write):
                await self.server.run(
                    read,
                    write,
                    InitializationOptions(
                        server_name="computer-use", server_version=SDK_VERSION, capabilities=capabilities()
                    ),
                )
        finally:
            await self.close()


async def main(*, allow_global_input=True):
    host = ComputerUseServer(allow_global_input=allow_global_input)
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, task.cancel)
        except RuntimeError:
            _LOG.debug("Progress notification could not be delivered.", exc_info=True)
    try:
        await host.run()
    except asyncio.CancelledError:
        _LOG.debug("Progress notification could not be delivered.", exc_info=True)


def cli(argv=None):
    parser = argparse.ArgumentParser(description="Computer Use stdio MCP host")
    parser.add_argument(
        "--allow-global-input",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Allow global mouse and keyboard input (default: enabled); qualified low-impact routes remain preferred",
    )
    args = parser.parse_args(argv)
    asyncio.run(main(allow_global_input=args.allow_global_input))


if __name__ == "__main__":
    cli()
