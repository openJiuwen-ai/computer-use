# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A driver exit between related-read stages never starts a successor implicitly."""

import asyncio
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from computer_use.client.factory import session_controller
from computer_use.errors import ComputerUseError
from computer_use.interaction.agent.requests import DiscoverApps
from computer_use.interaction.requests import ExecuteAction
from computer_use.native.connection import Connection
from computer_use.native.contracts import Binding, HostPolicy, Intent
from computer_use.native.transport import ConnectionPort
from computer_use.session.events import set_action_handler
from computer_use.session.results import result_receipt


class UI(SimpleNamespace):
    pass


class PostwriteSessionContinuityTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.spawn_log = Path(self.directory.name) / "spawns.txt"
        self.disconnected = asyncio.Event()
        driver = Path(__file__).resolve().parents[1] / "fixtures/observation_session_driver.py"
        self.connection = Connection(
            self.disconnected.set,
            driver=dict(command=sys.executable, args=[str(driver), str(self.spawn_log)]),
        )
        self.port = ConnectionPort(self.connection, HostPolicy())
        await self.port.connect(3000)
        self.original_identity = self.connection.session_identity
        self.controller = session_controller(self.port)
        self.target = Binding(self.connection.session, "app", "root", "surface", "node")
        self.ui = UI(_instance=SimpleNamespace(anchor=SimpleNamespace(name="Fixture")), _image=None)
        self.controller.register(self.ui, dict(selector="fixture"))
        self.receipt = dict(status="dispatched", target="original", detail="Sent once.")
        self.execute = self.controller.engine.execute = AsyncMock(return_value=self.receipt)
        self.read = self.controller.interfaces.read = AsyncMock(side_effect=self.read_across_exit)
        self.events = []
        set_action_handler(self.events.append)

    async def asyncTearDown(self):
        set_action_handler(None)
        self.connection.close()
        await self.connection.wait_closed()
        await asyncio.gather(*tuple(self.connection._readers), return_exceptions=True)

    def spawn_count(self):
        return len(self.spawn_log.read_text().splitlines())

    async def read_across_exit(self, *_args):
        # Exercise the real ConnectionPort/CallLifecycle directory stage and its
        # successful finish. The process then exits before the next read stage.
        async with self.port.calls.open(None, 3000) as read:
            await self.port.directory(
                read.token, read.target, dict(kind="surfaces", application="app"), read.remaining()
            )
        await asyncio.wait_for(self.disconnected.wait(), 3)
        self.assertFalse(self.port.calls.retired)  # cleanup was acknowledged
        self.assertIsNone(self.connection.session_identity)
        # This is the same unbound admission used by WorkingScopeDiscovery.
        # Without the suffix constraint it would start the fixture a second time.
        async with self.port.calls.open(None, 3000):
            self.fail("a successor driver was admitted by the related observation")

    async def run_action(self):
        return await self.controller.run(ExecuteAction(self.ui, Intent("click", {}), self.target))

    async def assert_suffix_stopped_and_ordinary_read_can_reconnect(self, outcome):
        self.assertEqual(outcome.observation_status, "unavailable")
        self.assertEqual(outcome.observation_error.code, "stale_target")
        self.assertFalse(outcome.observation_error.session_usable)
        self.assertIn("no replacement driver", outcome.observation_error.detail)
        self.assertEqual(self.spawn_count(), 1)
        self.assertIsNone(self.connection.session_identity)
        self.assertFalse(self.port.calls.retired)
        self.execute.assert_awaited_once()
        self.read.assert_awaited_once()

        # The task-local prohibition has ended. A normal public read keeps its
        # prior ability to establish a new driver, even with the same wire ID.
        self.assertEqual(await self.controller.run(DiscoverApps()), [])
        self.assertEqual(self.spawn_count(), 2)
        self.assertEqual(self.connection.session, self.original_identity[1])
        self.assertNotEqual(self.connection.session_identity, self.original_identity)

    async def test_completed_receipt_survives_driver_exit_between_read_stages(self):
        result = await self.run_action()
        self.assertIs(result_receipt(result), self.receipt)
        self.assertEqual((result.status, result.detail), ("dispatched", "Sent once."))
        self.assertEqual(self.events, [self.receipt])
        await self.assert_suffix_stopped_and_ordinary_read_can_reconnect(result)

    async def test_original_uncertain_error_survives_driver_exit_between_read_stages(self):
        cause = ValueError("original native failure")
        failure = ComputerUseError(
            dict(code="execution_unknown", target="original", effect="possible", detail="uncertain", next="observe"),
            cause=cause,
        )
        context, impact = object(), object()
        failure._failure_context, failure.impact = context, impact
        self.execute.side_effect = failure
        with self.assertRaises(ComputerUseError) as caught:
            await self.run_action()
        self.assertIs(caught.exception, failure)
        self.assertEqual((failure.effect, failure.detail, failure.next), ("possible", "uncertain", "observe"))
        self.assertIs(failure.__cause__, cause)
        self.assertIs(failure._failure_context, context)
        self.assertIs(failure.impact, impact)
        self.assertEqual(self.events, [])
        await self.assert_suffix_stopped_and_ordinary_read_can_reconnect(failure)
