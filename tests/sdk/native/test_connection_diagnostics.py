"""Driver cleanup diagnostics survive retirement without crossing sessions."""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from legacy_connection import Connection
from repository import ROOT as REPO_ROOT

from computer_use.diagnostics import setDiagnosticHandler

ROOT = REPO_ROOT


class ConnectionDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        self.connections = []
        setDiagnosticHandler(self.events.append)

    async def asyncTearDown(self):
        for connection in self.connections:
            connection.close()
            await connection.waitClosed()
        await asyncio.sleep(0)
        setDiagnosticHandler(None)

    async def test_real_driver_reports_cleanup_after_host_close(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = Connection(
                lambda: None,
                dict(
                    command=sys.executable,
                    args=[
                        str(ROOT / "tests/fixtures/deadline-driver.py"),
                        str(Path(directory) / "journal.jsonl"),
                        "diagnostic-cleanup",
                    ],
                ),
                protocol_version=3,
            )
            self.connections.append(connection)
            await connection.request("listApps", {})
            child = connection._child
            connection.close()
            await connection.waitClosed()
            async with asyncio.timeout(2):
                while not any(
                    event.get("value", {}).get("kind") == "fixture_cleanup"
                    for event in self.events
                    if isinstance(event.get("value"), dict)
                ):
                    await asyncio.sleep(0.005)
            cleanup = [
                event
                for event in self.events
                if isinstance(event.get("value"), dict) and event["value"].get("kind") == "fixture_cleanup"
            ]
            self.assertEqual(cleanup[-1]["pid"], child.pid)
            self.assertIsNone(connection.sessionIdentity)

    async def test_retired_split_unicode_keeps_its_trace_and_not_successor_context(self):
        connection = Connection(lambda: None)
        old = SimpleNamespace(pid=100, stderr=asyncio.StreamReader())
        successor = SimpleNamespace(pid=200, stderr=asyncio.StreamReader())
        connection._child = old
        old_traces = {7: "old-trace"}
        reader = asyncio.create_task(connection._read_stderr(old, old_traces))
        line = json.dumps(dict(requestId=7, message=dict(kind="cleanup", detail="已停止")), ensure_ascii=False).encode()
        split = line.index("已".encode()) + 1
        old.stderr.feed_data(line[:split])
        await asyncio.sleep(0)
        connection._child = successor
        connection._traces = {7: "successor-trace"}
        connection._diagnostic = "successor context"
        old.stderr.feed_data(line[split:] + b"\nlast fragment")
        old.stderr.feed_eof()
        await reader
        self.assertEqual(connection._diagnostic, "successor context")
        self.assertEqual(self.events[0]["value"], dict(kind="cleanup", detail="已停止"))
        self.assertEqual(self.events[0]["traceId"], "old-trace")
        self.assertEqual(self.events[0]["pid"], 100)
        self.assertEqual(self.events[1]["value"], "last fragment")
        self.assertTrue(self.events[1]["partial"])
        self.assertEqual(self.events[1]["pid"], 100)


if __name__ == "__main__":
    unittest.main()
