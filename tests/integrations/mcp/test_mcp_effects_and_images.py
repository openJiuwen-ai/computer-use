import json
import sys
import tempfile
import unittest
from pathlib import Path

from repository import ROOT as REPO_ROOT

from computer_use_mcp.session import ExecutionSession

"Real MCP worker effects and image delivery, without a desktop application."


ROOT = REPO_ROOT


class MCPEffectsAndImagesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = ExecutionSession(10000)

    async def asyncTearDown(self):
        await self.session.close()

    async def execute(self, code):
        return await self.session.execute(code)

    async def test_compile_failure_does_not_claim_an_executed_prefix(self):
        await self.execute("marker = 0")
        failed = await self.execute('marker = 1\nprint("bad"）')
        self.assertTrue(failed.get("isError"), failed)
        error = failed["content"][-1]["text"]
        self.assertIn("SyntaxError", error)
        self.assertIn("no statements from this call ran", error)
        self.assertNotIn("Earlier statements may have executed", error)
        later = await self.execute("print(marker)")
        self.assertEqual(later["content"][0]["text"], "0")

    async def test_runtime_syntax_error_still_preserves_possible_prefix_effects(self):
        failed = await self.execute('marker = 2\ncompile("bad ）", "inner", "exec")')
        self.assertTrue(failed.get("isError"), failed)
        self.assertIn("Earlier statements may have executed", failed["content"][-1]["text"])
        later = await self.execute("print(marker)")
        self.assertEqual(later["content"][0]["text"], "2")

    async def test_window_read_delivers_image_and_enables_next_call_only(self):
        with tempfile.TemporaryDirectory(prefix="mcp-window-image-") as directory:
            journal = str(Path(directory) / "wire.jsonl")
            setup = (
                f"""from computer_use.native.connection import Connection
from """
                f"""computer_use.client.factory """
                f"""import nativeComputer
from computer_use.native.contracts import """
                f"""HostPolicy
from computer_use.native.transport import """
                f"""ConnectionPort
connection """
                f"""= Connection(lambda: None, {{"command": """
                f"""{sys.executable!r}"""
                f""", "args": ["""
                f"""{str(ROOT / "tests/fixtures/v5-driver.py")!r}"""
                f""", "observation-sdk-changing-image", """
                f"""{journal!r}"""
                f"""]}}, protocol_version=6)
computer = """
                f"""nativeComputer(ConnectionPort(connection, """
                f"""HostPolicy(mode="exclusive")))
app = computer.app("Native """
                f"""fixture")
state """
                f"""= await app.read(view="both")"""
            )
            try:
                first = await self.execute(setup)
                self.assertFalse(first.get("isError"), first)
                self.assertEqual(len([b for b in first["content"] if b["type"] == "image"]), 1)
                click = await self.execute("await app.click(at=(0, 0))")
                self.assertFalse(click.get("isError"), click)
                changed = await self.execute('state = await app.read(view="both")\nawait app.click(at=(0, 0))')
                self.assertTrue(changed.get("isError"), changed)
                self.assertIn("image has not been delivered", changed["content"][-1]["text"])
                explicit = await self.execute('state = await app.read(view="both")\nprintImage(state.screenshot)')
                self.assertFalse(explicit.get("isError"), explicit)
                self.assertEqual(len([b for b in explicit["content"] if b["type"] == "image"]), 1)
                next_click = await self.execute("await app.click(at=(0, 0))")
                self.assertFalse(next_click.get("isError"), next_click)
                requests = [json.loads(line) for line in Path(journal).read_text().splitlines()]
                self.assertEqual(len([r for r in requests if r.get("method") == "execute"]), 2)
            finally:
                await self.execute(
                    ('if "connection" in globals():\n    connection.close()\n    await connection.waitClosed()')
                )


if __name__ == "__main__":
    unittest.main()
