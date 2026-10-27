"""Generated assets and production Windows animation/alpha rendering checks."""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from repository import ROOT as REPO_ROOT
from windows_sources import native_source

from computer_use_native_tools.pointer import generatePointerAssets

ROOT = REPO_ROOT


class PointerAssetTests(unittest.TestCase):
    def test_both_native_assets_share_the_source_digest_and_are_current(self):
        generatePointerAssets(check=True, root=ROOT)
        mac = (ROOT / "os_adapter/macos/Sources/Preview/AgentPointerAssets.swift").read_text(encoding="utf-8")
        windows = (ROOT / "os_adapter/windows/Sources/Preview/AgentPointerAssets.cs").read_text(encoding="utf-8")
        assets = ROOT / "os_adapter/assets/agent-pointer"
        expected = hashlib.sha256(
            ((assets / "cursor.svg").read_text() + "\x00" + (assets / "motion.json").read_text()).encode()
        ).hexdigest()
        for source in (mac, windows):
            digest = re.search(r"\bsha256 ([0-9a-f]{64})\b", source)
            self.assertIsNotNone(digest)
            self.assertEqual(digest[1], expected)

    def test_windows_asset_edit_is_detected_without_changing_macos(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in (
                "os_adapter/assets/agent-pointer",
                "os_adapter/macos/Sources/Preview",
                "os_adapter/windows/Sources/Preview",
            ):
                (root / name).mkdir(parents=True)
            for name in ("cursor.svg", "motion.json"):
                (root / "os_adapter/assets/agent-pointer" / name).write_bytes((ROOT / "os_adapter/assets/agent-pointer" / name).read_bytes())
            generatePointerAssets(root=root)
            windows = root / "os_adapter/windows/Sources/Preview/AgentPointerAssets.cs"
            windows.write_text(windows.read_text(encoding="utf-8") + "// stale\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "generated .cs is stale"):
                generatePointerAssets(check=True, root=root)


@unittest.skipUnless(sys.platform == "win32", "requires Windows .NET Framework and GDI+")
class WindowsPointerDrawingTests(unittest.TestCase):
    def test_real_generated_artwork_and_idle_clock(self):
        compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        with tempfile.TemporaryDirectory(prefix="windows-pointer-tests-") as directory:
            executable = Path(directory) / "test.exe"
            source = ROOT / "os_adapter/windows"
            result = subprocess.run(
                [
                    str(compiler),
                    "/nologo",
                    "/target:exe",
                    "/langversion:5",
                    "/warnaserror+",
                    f"/out:{executable}",
                    "/reference:System.Drawing.dll",
                    *(
                        str(native_source(name))
                        for name in (
                            "Sources/Preview/AgentPointerArtwork.cs",
                            "Sources/Preview/AgentPointerAssets.cs",
                            "Tests/Preview/AgentPointerTests.cs",
                        )
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            self.assertTrue(report["passed"])
            self.assertGreaterEqual(report["assertions"], 23)


if __name__ == "__main__":
    unittest.main()
