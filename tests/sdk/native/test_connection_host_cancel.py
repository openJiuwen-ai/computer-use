"""Caller cancellation retires its request, not just the outer awaiter."""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

from legacy_connection import Connection as HistoricalConnection
from repository import ROOT as REPO_ROOT

from computer_use.client.factory import nativeComputer
from computer_use.diagnostics import setDiagnosticHandler
from computer_use.native.connection import Connection
from computer_use.native.contracts import HostPolicy
from computer_use.native.transport import ConnectionPort

ROOT = REPO_ROOT


class HostCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.journal = Path(self.temp.name) / "wire.jsonl"
        self.connections, self.errors, self.diagnostics = [], [], []
        self.loop = asyncio.get_running_loop()
        self.handler = self.loop.get_exception_handler()
        self.loop.set_exception_handler(lambda loop, context: self.errors.append(context))
        setDiagnosticHandler(self.diagnostics.append)

    async def asyncTearDown(self):
        try:
            for connection in self.connections:
                connection.close()
                await connection.waitClosed()
            await asyncio.sleep(0)
            self.assertEqual([e.get("message") for e in self.errors], [])
        finally:
            self.loop.set_exception_handler(self.handler)
            setDiagnosticHandler(None)
            self.temp.cleanup()

    def connection(self, mode, *, version=6):
        args = (
            [str(ROOT / "tests/fixtures/v5-driver.py"), mode, str(self.journal)]
            if version == 6
            else [str(ROOT / "tests/fixtures/deadline-driver.py"), str(self.journal), mode]
        )
        result = (Connection if version == 6 else HistoricalConnection)(
            lambda: None, dict(command=sys.executable, args=args, timeoutMs=3000), protocol_version=version
        )
        self.connections.append(result)
        return result

    def records(self):
        return [json.loads(line) for line in self.journal.read_text().splitlines()] if self.journal.exists() else []

    @staticmethod
    async def wait_for(predicate):
        async with asyncio.timeout(3):
            while not predicate():
                await asyncio.sleep(0.005)

    async def cancel(self, task, connection):
        child = connection._child
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await connection.waitClosed()
        self.assertIsNotNone(child.returncode)
        self.assertIsNone(connection.sessionIdentity)

    async def test_cancelled_handshake_sends_no_command_and_retires_driver(self):
        connection = self.connection("hello-hang", version=3)
        task = asyncio.create_task(connection.connect())
        await self.wait_for(lambda: any(r.get("method") == "hello" for r in self.records()))
        await self.cancel(task, connection)
        self.assertEqual([r["method"] for r in self.records() if "method" in r], ["hello"])

    async def test_cancelled_v6_capture_publishes_nothing_and_needs_fresh_scope(self):
        connection = self.connection("desktop-delay")
        computer = nativeComputer(ConnectionPort(connection, HostPolicy("shared")))
        task = asyncio.create_task(computer.read(view="both"))
        await self.wait_for(lambda: any(r.get("method") == "capture" for r in self.records()))
        await self.cancel(task, connection)
        self.assertIsNone(computer._controller.desktop._view)
        self.assertEqual(sum(r.get("method") == "capture" for r in self.records()), 1)
        failures = [d for d in self.diagnostics if d.get("kind") == "command_end" and d.get("method") == "capture"]
        self.assertEqual(failures[-1]["failure"]["effect"], "none")
        with self.assertRaisesRegex(RuntimeError, "session is retired"):
            await computer.read(view="both")
        fresh = nativeComputer(ConnectionPort(self.connection("desktop-delay"), HostPolicy("shared")))
        (self.assertIsNotNone((await fresh.read(view="both")).screenshot))
        self.assertFalse(any(r.get("method") == "execute" for r in self.records()))

    async def test_cancelled_write_retains_uncertainty_and_never_replays(self):
        connection = self.connection("partial", version=3)
        before = await connection.request("listApps", {})
        task = asyncio.create_task(connection.request("typeText", dict(appRef="fixture-input", text="one insertion")))
        await self.wait_for(lambda: any(r.get("event") == "input" for r in self.records()))
        await self.cancel(task, connection)
        failures = [d for d in self.diagnostics if d.get("kind") == "command_end" and d.get("method") == "typeText"]
        self.assertEqual(failures[-1]["failure"]["effect"], "possible")
        self.assertNotEqual(await connection.request("listApps", {}), before)
        self.assertEqual(sum(r.get("event") == "input" for r in self.records()), 1)
        self.assertEqual(sum(r.get("event") == "cancel" for r in self.records()), 1)
        self.assertFalse([r for r in self.records() if r.get("event") == "start"][-1]["previousAlive"])

    async def test_queued_reply_between_cancel_and_cleanup_keeps_its_failure(self):
        connection = self.connection("desktop-delay")
        computer = nativeComputer(ConnectionPort(connection, HostPolicy("shared")))
        task = asyncio.create_task(computer.read(view="both"))
        await self.wait_for(lambda: any(r.get("method") == "capture" for r in self.records()))
        child, pending = connection._child, connection._pending
        reply = dict(
            version=6,
            session=connection.session,
            id=pending["id"],
            error=dict(
                code="execution_failed",
                effect="none",
                target="desktop",
                detail="fixture reply already queued",
                next="read again",
            ),
        )
        task.cancel()
        # Model the pipe callback already queued ahead of the task wakeup.
        connection._receive((json.dumps(reply) + "\n").encode())
        self.assertIsNotNone(connection.sessionIdentity)
        failure = [d for d in self.diagnostics if d.get("kind") == "command_end" and d.get("method") == "capture"][-1]
        self.assertEqual(failure["failure"]["detail"], "fixture reply already queued")
        self.assertEqual(failure["stage"], "backend")
        with self.assertRaises(asyncio.CancelledError):
            await task
        await connection.waitClosed()
        self.assertIsNotNone(child.returncode)
        self.assertIsNone(computer._controller.desktop._view)
