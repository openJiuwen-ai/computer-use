"""Compile and exercise production callback state with the Windows Framework."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from repository import ROOT as REPO_ROOT


@unittest.skipUnless(sys.platform == "win32", "requires the Windows Framework compiler")
class NativeReadEventTests(unittest.TestCase):
    def test_exclusive_foreground_recovery_does_not_replay_or_close_foreign_ui(self):
        def check(result):
            self.assertGreaterEqual(result["cases"], 20)
            self.assertGreaterEqual(result["assertions"], 60)

        self.compile_and_run("NativeForegroundRecoveryTests", check)

    def test_line_probing_is_limited_by_primary_read_scope(self):
        self.compile_and_run("NativeLineQueryTests", lambda result: self.assertEqual(result["assertions"], 14))

    def test_directory_helper_cannot_supply_actions_or_window_lifetimes(self):
        self.compile_and_run("NativeDirectoryProtocolTests", lambda result: self.assertEqual(result["assertions"], 36))

    def test_subminimum_transaction_slices_are_supported_and_restored(self):
        self.compile_and_run("NativeTransactionBudgetTests", lambda result: self.assertEqual(result["assertions"], 13))

    def test_closed_lid_requires_an_active_display_for_physical_input(self):
        self.compile_and_run(
            "NativeDesktopStateTests", lambda result: self.assertGreaterEqual(result["assertions"], 10)
        )

    def test_clipboard_text_family_uses_crlf(self):
        self.compile_and_run("NativeClipboardFormatsTests", lambda result: self.assertEqual(result["assertions"], 12))

    def test_preparation_and_collection_share_a_soft_budget(self):
        self.compile_and_run("NativeReadBudgetTests", lambda result: self.assertGreaterEqual(result["assertions"], 10))

    def test_storm_late_callback_and_worker_epoch(self):
        def check(result):
            self.assertEqual(result["assertions"], 7)
            self.assertEqual(result["stormCallbacks"], 4000)
            self.assertTrue(result["lateCallbacksIgnored"])
            self.assertTrue(result["foreignEpochRejected"])

        self.compile_and_run("NativeReadEventsTests", check)

    def compile_and_run(self, main, check):
        root = REPO_ROOT
        native = root / "os_adapter/windows"
        framework = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319"
        refs = (
            "System.dll",
            "System.Core.dll",
            "System.Web.Extensions.dll",
            "System.Drawing.dll",
            "System.Windows.Forms.dll",
            "WPF/WindowsBase.dll",
            "WPF/UIAutomationClient.dll",
            "WPF/UIAutomationTypes.dll",
        )
        with tempfile.TemporaryDirectory(prefix="uia-callback-") as folder:
            output = Path(folder)

            def run(command):
                result = subprocess.run(
                    [str(a) for a in command],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stdout.decode("utf-8", errors="replace"))
                return result.stdout

            run(
                [
                    framework / "csc.exe",
                    "/nologo",
                    "/target:exe",
                    "/platform:x64",
                    "/out:" + str(output / "import.exe"),
                    native / "Build/ImportUia.cs",
                ]
            )
            run([output / "import.exe", output])
            run(
                [
                    framework / "csc.exe",
                    "/nologo",
                    "/target:exe",
                    "/platform:x64",
                    "/warnaserror+",
                    "/main:ComputerUse.Windows." + main,
                    "/out:" + str(output / "probe.exe"),
                    *("/reference:" + str(framework / name) for name in refs),
                    "/reference:" + str(output / "Interop.NativeUIA.dll"),
                    *sorted((native / "Sources").rglob("*.cs")),
                    next((native / "Tests").rglob(main + ".cs")),
                ]
            )
            result = json.loads(run([output / "probe.exe"]))
            check(result)
