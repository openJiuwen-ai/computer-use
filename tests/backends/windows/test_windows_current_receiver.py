"""Production C# receiver query/ancestor mechanism; this does not run UIA."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from backends.windows.test_windows_v5_execution import compile_probe

from computer_use.protocol.execution import decodeExecutionResult


@unittest.skipUnless(os.environ.get("COMPUTER_USE_WINDOWS_TEST_DOTNET"), "standalone .NET SDK not configured")
class WindowsCurrentReceiverTests(unittest.TestCase):
    def test_focused_path_evidence_and_query_failure_boundary(self):
        with tempfile.TemporaryDirectory(prefix="windows-current-receiver-") as temporary:
            out = Path(temporary)
            sdk, env = compile_probe(self, out, "NativeCurrentReceiverTests")
            result = subprocess.run(
                [str(sdk / "dotnet"), str(out / "probe.dll")], capture_output=True, text=True, env=env, timeout=30
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            self.assertGreaterEqual(report["assertions"], 30)
            confirmed = decodeExecutionResult("inspect", report["facts"])
            self.assertEqual(confirmed["primitives"], [])
            self.assertEqual(confirmed["input"]["currentReceiver"]["state"], "confirmed")
            self.assertEqual(confirmed["input"]["currentReceiver"]["ancestors"], ["token-wrapper", "parent"])
            unavailable = decodeExecutionResult("inspect", report["unavailable"])
            self.assertEqual(unavailable["input"]["currentReceiver"]["state"], "unavailable")
            self.assertEqual(decodeExecutionResult("execute", report["guarded"])["dispatch"], "notDispatched")


if __name__ == "__main__":
    unittest.main()
