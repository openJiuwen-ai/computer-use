"""Current v5 public calls are lazy and share one native call lifecycle."""

import asyncio
import inspect
import unittest
from unittest.mock import patch

import integration.sdk.test_native_client as fixture


class AsyncAPITests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.NativeClientTests.asyncSetUp
    asyncTearDown = fixture.NativeClientTests.asyncTearDown
    computer = fixture.NativeClientTests.computer
    requests = fixture.NativeClientTests.requests

    async def test_unawaited_public_call_does_not_touch_desktop(self):
        computer = self.computer("directory")
        pending = computer.listApps()
        self.assertTrue(inspect.iscoroutine(pending))
        await asyncio.sleep(0)
        self.assertEqual(self.requests(), [])
        self.assertIsInstance(await pending, list)
        requests = self.requests()
        self.assertEqual([r["method"] for r in requests[:4]], ["hello", "begin", "inspect", "finish"])
        self.assertEqual(requests[2]["params"]["query"], {"kind": "applications"})

    async def test_abandoned_coroutine_has_no_effect(self):
        computer = self.computer()
        pending = computer.app("Fixture").typeText("must not send")
        pending.close()
        await asyncio.sleep(0)
        self.assertEqual(self.requests(), [])
        self.assertFalse(computer._controller.port.calls.busy)

    async def test_transport_creation_is_also_lazy(self):
        computer = self.computer()
        connection = computer._controller.transport
        pending = connection.request("inspect", dict(operation="not-started", query={"kind": "applications"}))
        self.assertTrue(inspect.iscoroutine(pending))
        await asyncio.sleep(0)
        self.assertIsNone(connection._child)
        self.assertFalse(connection._requesting)
        pending.close()

    async def test_explicitly_scheduled_calls_still_reject_overlap(self):
        entered, release = asyncio.Event(), asyncio.Event()
        computer = self.computer("directory")

        async def directory(*args):
            entered.set()
            await release.wait()
            return {"session": computer._controller.transport.session, "applications": [], "stopReasons": []}

        with patch.object(computer._controller.port, "directory", side_effect=directory):
            first = asyncio.create_task(computer.listApps())
            try:
                await entered.wait()
                with self.assertRaisesRegex(Exception, "busy|in progress"):
                    await computer.listApps()
            finally:
                release.set()
                await first
        self.assertEqual([r["method"] for r in self.requests()], ["hello", "begin", "finish"])
        self.assertFalse(computer._controller.port.calls.busy)


if __name__ == "__main__":
    unittest.main()
