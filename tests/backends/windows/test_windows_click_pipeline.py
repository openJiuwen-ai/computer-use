"""Compiled C# click contract/dispatch regressions with injected OS boundaries."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from backends.windows.test_windows_v5_execution import compile_probe
from repository import ROOT

from computer_use.protocol.execution import decodeExecutionResult


@unittest.skipUnless(os.environ.get("COMPUTER_USE_WINDOWS_TEST_DOTNET"), "standalone .NET SDK not configured")
class WindowsClickPipelineTests(unittest.TestCase):
    def test_negotiation_native_boundary_progress_and_preparation(self):
        with tempfile.TemporaryDirectory(prefix="windows-click-pipeline-") as tmp:
            out = Path(tmp)
            sdk, env = compile_probe(self, out, "NativeClickPipelineTests")
            run = subprocess.run(
                [str(sdk / "dotnet"), str(out / "probe.dll"), str(ROOT / "tests/fixtures/native-begin-requests.json")],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            report = json.loads(run.stdout)
            self.assertGreaterEqual(report["assertions"], 80)
            self.assertEqual(
                decodeExecutionResult("inspect", report["facts"])["primitives"][0]["click"]["delivery"], "object_action"
            )
            isolated = decodeExecutionResult("inspect", report["isolatedFacts"])
            self.assertFalse(isolated["actionsComplete"])
            self.assertEqual(isolated["primitives"][0]["click"]["delivery"], "desktop_pointer")
            self.assertEqual(decodeExecutionResult("execute", report["receipt"])["dispatch"], "partial")


if __name__ == "__main__":
    unittest.main()
