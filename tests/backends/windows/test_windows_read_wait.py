"""Event hints wake bounded observation refreshes, never confirm UI effects."""

import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from computer_use.interaction.read.acquisition.loading import refreshable
from computer_use.interaction.read.acquisition.reader import NativeReader
from computer_use.native.contracts import Binding, HostPolicy
from computer_use.native.transport import ConnectionPort
from computer_use.protocol.execution import decodeExecutionResult, decodeInspectQuery
from computer_use.protocol.observation import NativeNode, NativeObservation, ReadIssue
from computer_use.protocol.observation.adapters import DriverReadMetadata

TARGET = Binding("session", "instance", "window", "surface", "root")


class WaitTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def port(reason="event", features=("nativeReadEvents",)):
        result = dict(
            kind="readChange",
            session="session",
            surface="surface",
            after="epoch:0",
            reason=reason,
            elapsedMs=10,
            events=["text"] if reason == "event" else [],
        )
        connection = SimpleNamespace(
            protocol_version=6,
            platform="windows",
            capabilities=features,
            sessionIdentity="session",
            request=AsyncMock(return_value=result),
        )
        port = ConnectionPort(connection, HostPolicy())
        port._operations["operation"] = ("session", "")

        @asynccontextmanager
        async def operation(target, remaining):
            yield SimpleNamespace(token="operation", target=target, remaining=remaining)

        port.calls.operation = operation
        return port

    @staticmethod
    def snapshot(session="session"):
        return SimpleNamespace(
            native=NativeObservation(
                backend="windows", session=session, id="o", interval_ns=(1, 2), roots=(), nodes={}
            ),
            collection=DriverReadMetadata(
                surface="surface",
                change_token="epoch:0",
                relation_fields={},
                requested_fields={},
                requested_relations=(),
                field_sources={},
            ),
        )

    async def test_event_wakes_without_sleep_and_echoes_observed_scope(self):
        port = self.port()
        with patch("computer_use.interaction.read.acquisition.reader.asyncio.sleep", new_callable=AsyncMock) as sleep:
            self.assertEqual(await NativeReader(port).wait_for_change(TARGET, self.snapshot(), lambda: 1000), "event")
        sleep.assert_not_awaited()
        port.connection.request.assert_awaited_once_with(
            "inspect",
            dict(
                operation="operation",
                target=dict(
                    session="session",
                    instance="instance",
                    rootWindow="window",
                    surface="surface",
                    node="root",
                    capture=None,
                ),
                query=dict(kind="readChange", after="epoch:0", timeoutMs=200),
            ),
            dict(timeoutMs=1000),
        )

    async def test_missing_capability_or_foreign_snapshot_uses_bounded_poll(self):
        for features, snapshot in [((), self.snapshot()), (("nativeReadEvents",), self.snapshot("other"))]:
            port = self.port(features=features)
            with patch(
                "computer_use.interaction.read.acquisition.reader.asyncio.sleep", new_callable=AsyncMock
            ) as sleep:
                self.assertEqual(await NativeReader(port).wait_for_change(TARGET, snapshot, lambda: 1000), "poll")
                self.assertGreater(sleep.await_args.args[0], 0.15)
                self.assertLessEqual(sleep.await_args.args[0], 0.2)
            port.connection.request.assert_not_awaited()

    async def test_unavailable_event_source_retains_poll_fallback(self):
        port = self.port("unavailable")
        with patch("computer_use.interaction.read.acquisition.reader.asyncio.sleep", new_callable=AsyncMock) as sleep:
            self.assertEqual(await NativeReader(port).wait_for_change(TARGET, self.snapshot(), lambda: 1000), "poll")
        sleep.assert_awaited_once()

    async def test_timeout_does_not_add_another_poll(self):
        port = self.port("timeout")
        with patch("computer_use.interaction.read.acquisition.reader.asyncio.sleep", new_callable=AsyncMock) as sleep:
            self.assertEqual(await NativeReader(port).wait_for_change(TARGET, self.snapshot(), lambda: 1000), "timeout")
        sleep.assert_not_awaited()

    async def test_foreign_reply_cannot_wake_the_bound_window(self):
        for key in ("session", "surface", "after"):
            port = self.port()
            port.connection.request.return_value[key] = "foreign"
            with self.assertRaises(ValueError):
                await port.wait_for_change("operation", TARGET, "epoch:0", 200, 1000)

    async def test_invalid_wait_never_reaches_transport(self):
        for delay in (0, 401, True):
            port = self.port()
            with self.assertRaises(ValueError):
                await port.wait_for_change("operation", TARGET, "epoch:0", delay, 1000)
            port.connection.request.assert_not_awaited()
        port = self.port(features=())
        with self.assertRaises(ValueError):
            await port.wait_for_change("operation", TARGET, "epoch:0", 200, 1000)


class WaitSchemaTests(unittest.TestCase):
    def test_wire_query_and_result_keep_hints_separate_from_facts(self):
        query = dict(kind="readChange", after="epoch:0", timeoutMs=200)
        self.assertEqual(decodeInspectQuery(query), query)
        for delay in (0, 401, True):
            with self.assertRaises(ValueError):
                decodeInspectQuery(dict(query, timeoutMs=delay))
        result = dict(
            kind="readChange",
            session="session",
            surface="surface",
            after="epoch:0",
            reason="event",
            events=["text"],
            elapsedMs=5,
        )
        self.assertEqual(decodeExecutionResult("inspect", result), result)
        with self.assertRaises(ValueError):
            decodeExecutionResult("inspect", dict(result, ready=True))

    def test_loading_does_not_repeat_timed_out_or_out_of_scope_collection(self):
        observation = NativeObservation(
            backend="windows",
            session="s",
            id="o",
            interval_ns=(1, 2),
            roots=("n",),
            nodes={"n": NativeNode(role="document")},
        )
        snapshot = SimpleNamespace(native=observation, facts={"unread": []})
        self.assertTrue(refreshable(snapshot))
        snapshot.native = replace(observation, issues=(ReadIssue(("nodes", "n", "text", "content"), "timeout"),))
        self.assertFalse(refreshable(snapshot))
        snapshot.native = observation
        snapshot.facts["unread"] = ["unread"]
        self.assertFalse(refreshable(snapshot))
        snapshot.facts["unread"] = []
        snapshot.native = replace(observation, issues=(ReadIssue(("nodes", "n", "relations", "children"), "timeout"),))
        self.assertFalse(refreshable(snapshot))
        self.assertFalse(refreshable(None))
