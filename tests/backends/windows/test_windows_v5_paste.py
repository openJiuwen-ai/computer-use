"""Production C# paste and Python protocol; no Windows GUI acceptance claim."""

import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backends.windows.test_windows_v5_execution import compile_probe

from computer_use.client.factory import nativeComputer
from computer_use.errors import ComputerUseError
from computer_use.native.connection import Connection
from computer_use.native.contracts import HostPolicy
from computer_use.native.transport import ConnectionPort
from computer_use.protocol.execution import decodeExecutionResult


@unittest.skipUnless(os.environ.get("COMPUTER_USE_WINDOWS_TEST_DOTNET"), "standalone .NET SDK not configured")
class WindowsPasteRuntimeTests(unittest.TestCase):
    def test_production_transaction_recipe_and_public_python_chain(self):
        with tempfile.TemporaryDirectory(prefix="windows-v5-paste-probe-") as temporary:
            out = Path(temporary)
            sdk, env = compile_probe(self, out, "NativePasteTests")
            run = subprocess.run(
                [str(sdk / "dotnet"), str(out / "probe.dll")], capture_output=True, text=True, env=env, timeout=30
            )
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            report = json.loads(run.stdout)
            self.assertGreaterEqual(report["assertions"], 50)
            self.assertEqual(decodeExecutionResult("inspect", report["facts"])["kind"], "inputContext")
            receipt = decodeExecutionResult("execute", report["receipt"])
            self.assertEqual(receipt["dispatch"], "dispatched")
            self.assertEqual(receipt["confirmation"], "unavailable")

            async def public_chain(assist):
                journal = out / ("assist.jsonl" if assist else "strict.jsonl")
                connection = Connection(
                    lambda: None,
                    dict(
                        command=str(sdk / "dotnet"),
                        args=[str(out / "probe.dll"), "--serve", str(journal), "assist" if assist else "strict"],
                        timeoutMs=5000,
                    ),
                    protocol_version=6,
                )
                try:
                    computer = nativeComputer(ConnectionPort(connection, HostPolicy(foreground_assist=assist)))
                    app = computer.app((await computer.listApps())[0].id)
                    await app.read(view="both")
                    # Empty paste must not clear a selection or acquire focus.
                    await (await app.element(0)).paste("")
                    if assist:
                        await (await app.element(0)).paste("新值\t🙂\r\n")
                        await app.paste("尾")
                        self.assertIn("initial新值", (await app.read(full=True)).text)
                    else:
                        target = await app.element(0)
                        for action in (lambda: target.paste("wrong"), lambda: app.paste("wrong")):
                            with self.assertRaises(ComputerUseError) as error:
                                await action()
                            self.assertEqual(error.exception.effect, "none")
                finally:
                    connection.close()
                    await connection.waitClosed()
                effects = json.loads(Path(str(journal) + ".effects.json").read_text())
                self.assertEqual(effects["text"], "initial新值\t🙂\r\n尾" if assist else "initial")
                self.assertTrue(effects["restored"])
                self.assertEqual(effects["clipboardSaved"], 2 if assist else 0)
                self.assertEqual(effects["acquired"], effects["released"])
                self.assertEqual((effects["activated"], effects["focused"]), (1, 1) if assist else (0, 0))
                requests = [json.loads(line) for line in journal.read_text().splitlines()]
                steps = [r["params"]["command"] for r in requests if r.get("method") == "execute"]
                self.assertEqual(
                    [c["kind"] for c in steps],
                    ["prepare_receiver", "clipboard_paste", "clipboard_paste"] if assist else [],
                )
                if assist:
                    self.assertIsNone(steps[-1]["target"]["node"])
                    self.assertEqual([c["kind"] for c in steps if c["foreground"]], ["prepare_receiver"])
                self.assertLessEqual(
                    {r["method"] for r in requests if "method" in r},
                    {"hello", "begin", "inspect", "capture", "execute", "finish"},
                )

            with patch.dict(os.environ, {"COMPUTER_USE_DESKTOP": "shared"}):
                asyncio.run(public_chain(False))
                asyncio.run(public_chain(True))


if __name__ == "__main__":
    unittest.main()
