"""Same native fixture as the original transport; no desktop interaction."""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

from legacy_connection import Connection
from sdk.parity.test_protocol_parity import ROOT

from computer_use.errors import ComputerUseError
from computer_use.native.connection import driverTerminationGraceMs, requestCleanupGraceMs
from computer_use.native.preview import setPreviewHandler


class ConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="computer-use-python-deadline-")
        self.journal = Path(self.temp.name) / "events.jsonl"
        self.connections = []
        self.invalidations = 0

    async def asyncTearDown(self):
        setPreviewHandler(None)
        for connection in self.connections:
            connection.close()
            await connection.waitClosed()
        self.temp.cleanup()

    def make(self, mode, deadline=True):
        def invalidate():
            self.invalidations += 1

        args = (
            [str(ROOT / "tests/fixtures/deadline-driver.py"), str(self.journal), mode]
            if deadline
            else [str(ROOT / "tests/fixtures/driver.py"), mode]
        )
        connection = Connection(invalidate, dict(command=sys.executable, args=args, timeoutMs=3000))
        self.connections.append(connection)
        return connection

    def events(self):
        return (
            [json.loads(line) for line in self.journal.read_text().split("\n") if line] if self.journal.exists() else []
        )

    async def waitFor(self, kind):
        for _ in range(300):
            if any(e["event"] == kind for e in self.events()):
                return
            await asyncio.sleep(0.005)
        self.fail(f"fixture did not report {kind}")

    async def test_cooperative_cancellation_and_partial_effects(self):
        for mode in ("cooperative", "partial", "late-success"):
            with self.subTest(mode=mode):
                if self.journal.exists():
                    self.journal.unlink()
                c = self.make(mode)
                before = await c.request("listApps", {})
                method = "readElementText" if mode == "cooperative" else "typeText"
                params = (
                    dict(ref="slow", offset=0, length=2)
                    if mode == "cooperative"
                    else dict(appRef="fixture-input", text="one insertion")
                )
                pending = asyncio.create_task(c.request(method, params, dict(timeoutMs=70)))
                await self.waitFor("cancel")
                self.assertFalse(pending.done())
                with self.assertRaisesRegex(ComputerUseError, "in progress"):
                    await c.request("listApps", {})
                if mode == "late-success":
                    self.assertEqual((await pending)["status"], "dispatched")
                else:
                    with self.assertRaises(ComputerUseError) as caught:
                        await pending
                    self.assertEqual(caught.exception.effect, "partial" if mode == "partial" else "none")
                    self.assertIn("cleanup completed", caught.exception.detail)
                self.assertEqual(await c.request("listApps", {}), before)
                events = self.events()
                self.assertEqual(sum(e["event"] == "start" for e in events), 1)
                self.assertEqual(sum(e["event"] == "cancel" for e in events), 1)
                self.assertEqual(sum(e["event"] == "input" for e in events), 0 if mode == "cooperative" else 1)
                c.close()
                await c.waitClosed()

    async def test_uncertain_input_is_not_replayed_and_old_process_retires(self):
        c = self.make("uncooperative")
        first = await c.request("listApps", {})
        with self.assertRaises(ComputerUseError) as caught:
            await c.request("typeText", dict(appRef="fixture-input", text="one insertion"), dict(timeoutMs=70))
        self.assertEqual(caught.exception.effect, "possible")
        self.assertIn("cleanup grace", caught.exception.detail)
        second = await c.request("listApps", {})
        self.assertNotEqual(first, second)
        events = self.events()
        starts = [e for e in events if e["event"] == "start"]
        self.assertEqual(len(starts), 2)
        self.assertFalse(starts[1]["previousAlive"])
        self.assertEqual(sum(e["event"] == "input" for e in events), 1)
        self.assertEqual(sum(e["event"] == "cancel" for e in events), 1)

    async def test_retired_process_cannot_kill_successor(self):
        c = self.make("cooperative")
        first = await c.request("listApps", {})
        c.close()
        second = await c.request("listApps", {})
        self.assertNotEqual(first, second)
        await asyncio.sleep((driverTerminationGraceMs + 80) / 1000)
        self.assertEqual(await c.request("listApps", {}), second)
        starts = [e for e in self.events() if e["event"] == "start"]
        self.assertEqual(len(starts), 2)
        self.assertFalse(starts[1]["previousAlive"])

    async def test_corrupt_cleanup_invalidates_without_waiting_for_grace(self):
        c = self.make("corrupt-on-cancel")
        await c.request("listApps", {})
        start = asyncio.get_running_loop().time()
        with self.assertRaisesRegex(ComputerUseError, "different version or session"):
            await c.request("readElementText", dict(ref="slow", offset=0, length=2), dict(timeoutMs=70))
        self.assertLess(asyncio.get_running_loop().time() - start, requestCleanupGraceMs / 1000)
        self.assertGreater(self.invalidations, 0)

    async def test_handshake_timeout_sends_no_native_command(self):
        c = self.make("hello-hang")
        with self.assertRaisesRegex(ComputerUseError, "hello timed out"):
            await c.request("listApps", {}, dict(timeoutMs=150))
        self.assertTrue(all(e["event"] != "request" or e["method"] == "hello" for e in self.events()))

    async def test_required_capabilities_fail_before_dispatch(self):
        c = self.make("no-cancel")
        with self.assertRaisesRegex(ComputerUseError, "missing cancel"):
            await c.request("listApps", {})
        self.assertTrue(all(e["event"] != "request" or e["method"] == "hello" for e in self.events()))

    @unittest.skipIf(
        sys.platform == "win32",
        "Windows driver transport has no POSIX preview descriptor; native pointer feedback is tested separately",
    )
    async def test_preview_pipe_preserves_split_unicode_and_protocol(self):
        frames = []
        setPreviewHandler(frames.append)
        c = self.make("preview", False)
        apps = await c.request("listApps", {})
        for _ in range(40):
            if frames:
                break
            await asyncio.sleep(0.005)
        self.assertEqual(apps[0]["id"], "test.app")
        self.assertEqual(frames[0]["name"], "测试应用")
        self.assertEqual(frames[0]["kind"], "frame")


if __name__ == "__main__":
    unittest.main()
