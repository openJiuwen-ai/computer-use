"""A real stdio MCP round trip across the desktop relay, without UI input."""

import asyncio
import os
import socket
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@unittest.skipIf(os.name == "nt", "The serve process needs an interactive Windows session")
class DesktopBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_authentication_persistent_tools_metadata_unicode_and_clean_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            probe = socket.socket()
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
            probe.close()
            arguments = ["-m", "computer_use_mcp.desktop_bridge"]
            options = ["--port", str(port), "--state-dir", folder + "/state"]
            env = dict(
                os.environ, COMPUTER_USE_LOG_DIR=folder + "/logs"
            )
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                *arguments,
                "serve",
                *options,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                ready = await asyncio.wait_for(process.stderr.readline(), 5)
                self.assertIn(b"Desktop bridge ready", ready)
                token_file = Path(folder + "/state/token")
                self.assertEqual(token_file.stat().st_mode & 0o777, 0o600)
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(b'{"token":"wrong","mode":"connect"}\n')
                await writer.drain()
                self.assertEqual(await asyncio.wait_for(reader.read(), 5), b"")
                writer.close()
                await writer.wait_closed()
                params = StdioServerParameters(command=sys.executable, args=[*arguments, "connect", *options], env=env)
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=15)) as client:
                        initialized = await client.initialize()
                        self.assertEqual(initialized.serverInfo.name, "computer-use")
                        tools = await client.list_tools()
                        self.assertEqual([tool.name for tool in tools.tools], ["computer_use", "computer_use_reset"])
                        result = await client.call_tool(
                            "computer_use", dict(code="answer=123+456\nprint('你好', answer)", title="验证远程变量")
                        )
                        self.assertFalse(result.isError, result)
                        self.assertEqual(result.content[0].text, "你好 579")
                        self.assertEqual(result.meta["computer-use"]["title"], "验证远程变量")
                        self.assertIn("cursor", result.meta["computer-use-history"])
                        next_result = await client.call_tool("computer_use", dict(code="print(answer+1)"))
                        self.assertEqual(next_result.content[0].text, "580")
                        reset = await client.call_tool("computer_use_reset", {})
                        self.assertFalse(reset.isError, reset)
                stop = await asyncio.create_subprocess_exec(
                    sys.executable,
                    *arguments,
                    "stop",
                    *options,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                _, error = await asyncio.wait_for(stop.communicate(), 15)
                self.assertEqual(stop.returncode, 0, error.decode())
                self.assertEqual(await asyncio.wait_for(process.wait(), 15), 0)
                self.assertFalse(token_file.exists())
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()


if __name__ == "__main__":
    unittest.main()
