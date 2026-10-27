import asyncio
import os
import sys
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from sdk.parity.test_protocol_parity import ROOT

from computer_use_mcp.session import ExecutionSession


class MCPServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_stdio_tools_persistence_and_reset(self):
        with tempfile.TemporaryDirectory(prefix="python-mcp-") as directory:
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "computer_use_mcp", "--desktop", "exclusive"],
                cwd=ROOT,
                env={
                    **os.environ,
                    "PYTHONPATH": str(ROOT / "tests"),
                    "COMPUTER_USE_LOG_DIR": directory + "/logs",
                },
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=15)) as client:
                    initialized = await client.initialize()
                    self.assertEqual(initialized.serverInfo.name, "computer-use")
                    self.assertEqual(set(initialized.capabilities.experimental), {"computer-use-host", "computer-use-preview", "computer-use-demo-foreground", "computer-use-history"})
                    tools = await client.list_tools()
                    self.assertEqual([t.name for t in tools.tools], ["computer_use", "computer_use_reset"])
                    self.assertIn("Python", tools.tools[0].description)
                    first = await client.call_tool(
                        "computer_use",
                        dict(
                            title=" Keep variable ",
                            code=(
                                "import os\nassert os.environ['COMPUTER_USE_DESKTOP'] == "
                                "'exclusive'\nnumber=128*37\nprint(number)"
                            ),
                        ),
                    )
                    self.assertFalse(first.isError)
                    self.assertEqual(first.content[0].text, "4736")
                    self.assertIsNone(first.structuredContent)
                    self.assertEqual(first.meta["computer-use"]["title"], "Keep variable")
                    self.assertIn("cursor", first.meta["computer-use-history"])
                    second = await client.call_tool("computer_use", dict(code="print(number+1)"))
                    self.assertEqual(second.content[0].text, "4737")
                    invalid = await client.call_tool("computer_use", dict(code="   "))
                    self.assertTrue(invalid.isError)
                    reset = await client.call_tool("computer_use_reset", {})
                    self.assertFalse(reset.isError)
                    cleared = await client.call_tool("computer_use", dict(code="print(number)"))
                    self.assertTrue(cleared.isError)
                    self.assertIn("NameError", cleared.content[-1].text)
                    running = asyncio.create_task(
                        client.call_tool("computer_use", dict(code="print('before block')\nwhile True: pass"))
                    )
                    from pathlib import Path

                    for _ in range(100):
                        if any(
                            "before block" in file.read_text(encoding="utf-8")
                            and '"text":"before block"' in file.read_text(encoding="utf-8")
                            for file in Path(directory + "/logs").rglob("*.jsonl")
                        ):
                            break
                        await asyncio.sleep(0.02)
                    reset = await client.call_tool("computer_use_reset", {})
                    self.assertFalse(reset.isError)
                    interrupted = await running
                    self.assertTrue(interrupted.isError)
                    self.assertTrue(
                        any(block.type == "text" and "before block" in block.text for block in interrupted.content)
                    )

    async def test_reset_while_worker_starts_never_sends_code(self):
        with (
            tempfile.TemporaryDirectory(prefix="python-startup-") as directory,
            patch.dict(
                os.environ,
            ),
        ):
            session = ExecutionSession(5000)
            original = session._start
            entered, release = asyncio.Event(), asyncio.Event()

            async def delayed():
                entered.set()
                await release.wait()
                await original()

            session._start = delayed
            try:
                execution = session.execute("print('must not run')")
                await entered.wait()
                reset = session.reset()
                release.set()
                outcome_value, reset_outcome = await asyncio.gather(execution, reset)
                self.assertTrue(outcome_value["isError"])
                self.assertIn("No code was executed", outcome_value["content"][0]["text"])
                self.assertFalse(reset_outcome.get("isError", False))
                self.assertIsNone(session._child)
                session._start = original
                fresh = await session.execute("print('fresh')")
                self.assertEqual(fresh["content"][0]["text"], "fresh")
            finally:
                await session.close()


if __name__ == "__main__":
    unittest.main()
