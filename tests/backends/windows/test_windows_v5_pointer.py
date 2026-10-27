"""Public Python -> production C# pointer runtime, using explicit fake OS I/O."""

import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backends.windows.test_windows_v5_execution import compile_probe

from computer_use import ClickOption
from computer_use.client.factory import nativeComputer
from computer_use.errors import ComputerUseError
from computer_use.native.connection import Connection
from computer_use.native.contracts import HostPolicy
from computer_use.native.transport import ConnectionPort
from computer_use.protocol.execution import decodeExecutionResult


@unittest.skipUnless(os.environ.get("COMPUTER_USE_WINDOWS_TEST_DOTNET"), "standalone .NET SDK not configured")
class WindowsPointerRuntimeTests(unittest.TestCase):
    def test_pointer_contracts_cleanup_and_public_window_api(self):
        with tempfile.TemporaryDirectory(prefix="windows-v5-pointer-probe-") as temporary:
            out = Path(temporary)
            sdk, env = compile_probe(self, out, "NativePointerTests")
            run = subprocess.run(
                [str(sdk / "dotnet"), str(out / "probe.dll")], capture_output=True, text=True, env=env, timeout=30
            )
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            report = json.loads(run.stdout)
            self.assertGreaterEqual(report["assertions"], 65)
            self.assertEqual(decodeExecutionResult("inspect", report["facts"])["input"]["pointerRoute"], "no")
            self.assertEqual(decodeExecutionResult("execute", report["receipt"])["cleanup"], "failed")

            async def public_chain(setting):
                journal = out / f"{setting}.jsonl"
                connection = Connection(
                    lambda: None,
                    dict(
                        command=str(sdk / "dotnet"),
                        args=[str(out / "probe.dll"), "--serve", str(journal), setting],
                        timeoutMs=5000,
                    ),
                    protocol_version=6,
                )
                try:
                    computer = nativeComputer(
                        ConnectionPort(connection, HostPolicy(foreground_assist=setting in ("assist", "occluded")))
                    )
                    app = computer.app((await computer.listApps())[0].id)
                    (self.assertIsNotNone((await app.read(view="both")).screenshot))
                    actions = [
                        lambda: app.click(ClickOption(button="right", count=2), at=(0, 0)),
                        lambda: app.scroll("left", at=(0, 0), pages=0.5),
                        lambda: app.drag((0, 0), (1, 1)),
                    ]
                    for action_index, action in enumerate(actions):
                        if setting in ("strict", "occluded"):
                            with self.assertRaises(ComputerUseError) as error:
                                await action()
                            self.assertEqual(error.exception.effect, "none")
                            if setting == "occluded":
                                self.assertIn(
                                    "captured point cannot currently reach"
                                    if action_index == 0
                                    else "captured point is not reachable",
                                    str(error.exception),
                                )
                        else:
                            await action()
                    # Independent addressed actions retain their original route.
                    await (await app.element(0)).click()
                    await computer.read(view="both")
                    with self.assertRaises(ComputerUseError) as error:
                        await computer.click(at=(0, 0))
                    self.assertEqual(error.exception.effect, "none")
                finally:
                    connection.close()
                    await connection.waitClosed()
                effects = json.loads(Path(str(journal) + ".effects.json").read_text())
                self.assertEqual(effects["acquired"], effects["released"])
                self.assertEqual(effects["activated"], int(setting == "assist"))
                self.assertEqual(effects["batches"], 0 if setting in ("strict", "occluded") else 29)
                self.assertEqual(effects["objectSent"], 1)
                requests = [json.loads(line) for line in journal.read_text().splitlines()]
                self.assertLessEqual(
                    {r["method"] for r in requests if "method" in r},
                    {"hello", "begin", "inspect", "capture", "execute", "finish"},
                )
                commands = [r["params"]["command"] for r in requests if r.get("method") == "execute"]
                begun = [r["params"] for r in requests if r.get("method") == "begin"]
                self.assertTrue(all(p.get("clickContract") == "v1" for p in begun if p["intent"]["kind"] == "click"))
                self.assertTrue(all("clickContract" not in p for p in begun if p["intent"]["kind"] != "click"))
                # Negotiated click merges preparation into pointer_sequence.
                # The adjacent scroll/drag paths retain their legacy protocol.
                self.assertEqual(
                    [c["kind"] for c in commands],
                    (
                        ["prepare_receiver"] * 2
                        if setting == "occluded"
                        else (["pointer_sequence"] * 3 if setting != "strict" else [])
                    )
                    + ["native_action"],
                )
                self.assertEqual(
                    [c["kind"] for c in commands if c["foreground"]],
                    ["prepare_receiver"] * 2
                    if setting == "occluded"
                    else ["pointer_sequence"]
                    if setting == "assist"
                    else [],
                )
                self.assertTrue(
                    all(
                        c["target"]["capture"] is not None and c["target"]["node"] is None
                        for c in commands
                        if c["kind"] != "native_action"
                    )
                )

            with patch.dict(os.environ, {"COMPUTER_USE_DESKTOP": "shared"}):
                for setting in ("strict", "assist", "ready", "occluded"):
                    asyncio.run(public_chain(setting))


if __name__ == "__main__":
    unittest.main()
