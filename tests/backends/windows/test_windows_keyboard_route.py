"""Focused UIA leaves may route through an ancestor HWND, with strict boundaries."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from repository import ROOT as REPO_ROOT


@unittest.skipUnless(sys.platform == "win32", "requires Windows .NET Framework compiler")
class WindowsKeyboardRouteTests(unittest.TestCase):
    def test_native_ancestor_requires_exact_focus_and_rejects_siblings_and_foreign_windows(self):
        native = REPO_ROOT / "os_adapter/windows"
        compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        with tempfile.TemporaryDirectory(prefix="windows-keyboard-route-") as directory:
            exe = Path(directory) / "probe.exe"
            result = subprocess.run(
                [
                    str(compiler),
                    "/nologo",
                    "/utf8output",
                    "/target:exe",
                    "/warnaserror+",
                    f"/out:{exe}",
                    str(native / "Sources/Control/NativeKeyboardRoute.cs"),
                    str(native / "Tests/Control/NativeKeyboardRouteTests.cs"),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            self.assertTrue(report["passed"])
            self.assertGreaterEqual(report["assertions"], 13)
