"""Small stdio client fixture using the official MCP SDK."""

from contextlib import AsyncExitStack
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client


class MCPClient:
    def __init__(self, capabilities):
        self.capabilities = capabilities
        self.stack = AsyncExitStack()

    async def connect(self, command):
        read, write = await self.stack.enter_async_context(stdio_client(StdioServerParameters(**command)))
        self.session = await self.stack.enter_async_context(ClientSession(read, write))
        send = self.session.send_request

        async def send_request(request, *args, **kwargs):
            if isinstance(request.root, types.InitializeRequest):
                request.root.params.capabilities.experimental = self.capabilities.get("experimental")
            return await send(request, *args, **kwargs)

        self.session.send_request = send_request
        await self.session.initialize()

    async def callTool(self, request):
        result = await self.session.call_tool(request["name"], request.get("arguments"), meta=request.get("_meta"))
        return result.model_dump(by_alias=True, exclude_none=True)

    async def close(self):
        await self.stack.aclose()
