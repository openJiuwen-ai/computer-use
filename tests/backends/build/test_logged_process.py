"""Fixture logging survives parent descriptor release and failed startup."""

import asyncio
import contextlib
import io
import json
import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from repository import ROOT

from computer_use_native_tools.process import launch_logged_process


class LoggedProcessTests(unittest.TestCase):
    def test_child_can_keep_writing_after_startup_closes_the_parent_file(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "fixture.log"
            command = [
                sys.executable,
                "-c",
                "import sys,time; print('early',flush=True); time.sleep(.05); print('late',flush=True); "
                "print('stderr',file=sys.stderr,flush=True)",
            ]
            with launch_logged_process(command, output) as process:
                self.assertEqual(process.wait(timeout=5), 0)
            self.assertEqual(output.read_text().splitlines(), ["early", "late", "stderr"])

    def test_startup_failure_closes_the_opened_log(self):
        opened = []

        def fail(command, **options):
            opened.append(options["stdout"])
            self.assertIs(options["stdout"], options["stderr"])
            self.assertFalse(options["stdout"].closed)
            raise FileNotFoundError("fixture startup failed")

        with tempfile.TemporaryDirectory() as folder:
            with patch("computer_use_native_tools.process.subprocess.Popen", side_effect=fail):
                with self.assertRaisesRegex(FileNotFoundError, "fixture startup failed"):
                    launch_logged_process(["unavailable-fixture"], Path(folder) / "fixture.log")
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].closed)

    def test_click_runner_reaps_fixture_when_x11_setup_fails(self):
        runner = runpy.run_path(str(ROOT / "tests/native/linux/control/click/fixtures/click_acceptance.py"))[
            "run"
        ]
        fixture = Mock(returncode=0)
        fixture.poll.return_value = None
        fixture.wait.return_value = 0
        diagnostic = Mock()
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "acceptance"
            with (
                patch.dict(runner.__globals__, launch_logged_process=Mock(return_value=fixture)),
                patch.dict(runner.__globals__, setDiagnosticHandler=diagnostic),
                patch.object(sys, "platform", "linux"),
                patch.dict(os.environ, DISPLAY=":fixture", CU_CLICK_ISOLATED_DISPLAY=":fixture", WAYLAND_DISPLAY=""),
                patch.dict(sys.modules, Xlib=None),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(asyncio.run(runner("gtk", output)), 1)
            report = json.loads((output / "report.json").read_text())
        self.assertIn("ModuleNotFoundError", report["error"])
        self.assertEqual(report["cleanup"], dict(fixtureReturncode=0, connectionsClosed=True))
        fixture.stdin.close.assert_called_once()
        fixture.wait.assert_called_once_with(4)
        diagnostic.assert_called_with(None)
