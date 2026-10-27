"""Portable pipeline boundary probes using the existing native OS substitutes.

Install the Linux driver editable in the test environment, then run
python -m unittest discover -s tests/native/linux -p test_native_pipelines.py.
No Linux GUI is claimed.
"""

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[5]
sys.path.append(str(ROOT / "tests/fixtures"))
from linux_native_provider import Backend

from computer_use_linux.contracts.protocol import Failure, Request
from computer_use_linux.runtime.native import NativeExecution


def context(seconds=10):
    return Request(time.monotonic() + seconds, threading.Event())


def scope(**changes):
    return {**dict(session="session", instance=None, rootWindow=None, surface=None, node=None, capture=None), **changes}


class NativePipelineBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.driver = NativeExecution(self.backend, "session")
        self.pid = patch("computer_use_linux.windowing.native_directory.process_start", return_value="generation")
        self.pid.start()

    def tearDown(self):
        self.driver.close()
        self.pid.stop()

    def begin(self, target):
        return self.driver.perform(
            "begin", dict(mode="read", target=target, intent=dict(kind="read", parameters={})), context()
        )["operation"]

    def test_wrong_operation_or_scope_does_not_enter_provider(self):
        operation = self.begin(scope())
        self.backend.ax.calls.clear()
        for params in (
            dict(operation="other", target=scope(), query=dict(kind="applications")),
            dict(
                operation=operation,
                target=scope(instance="other", rootWindow="other", surface="other"),
                query=dict(kind="nodes", fields=["name"], maxNodes=1, maxDepth=0),
            ),
        ):
            with self.assertRaises(Failure) as caught:
                self.driver.perform("inspect", params, context())
            self.assertEqual(caught.exception.code, "stale_target")
            self.assertEqual(self.backend.ax.calls, [])
        self.driver.finish(operation)

    def test_retained_capture_query_does_not_reenter_accessibility_provider(self):
        text_ref = self.driver.store.text("retained 😀")
        operation = self.begin(scope())
        with patch.object(self.backend.ax, "begin", side_effect=AssertionError("captured text is immutable")):
            result = self.driver.perform(
                "inspect",
                dict(
                    operation=operation,
                    target=scope(),
                    query=dict(kind="capturedText", textRef=text_ref, offset=0, length=32),
                ),
                context(),
            )
        self.assertEqual(result["page"]["text"], "retained 😀")
        self.driver.finish(operation)
        self.assertEqual(self.driver.finish(operation), dict(cleanup="complete"))

    def test_query_cancel_closes_scope_but_preserves_session_references(self):
        retained = self.driver.store.text("still retained")
        operation = self.begin(scope())
        cancelled = context()
        cancelled.cancelled.set()
        with self.assertRaises(Failure) as caught:
            self.driver.perform(
                "inspect", dict(operation=operation, target=scope(), query=dict(kind="applications")), cancelled
            )
        self.assertEqual(caught.exception.code, "cancelled")
        self.assertIsNone(self.driver.current)
        self.assertEqual(self.driver.store.text_page(retained, 0, 32)["text"], "still retained")
        self.assertEqual(self.driver.finish(operation), dict(cleanup="complete"))


if __name__ == "__main__":
    unittest.main()
