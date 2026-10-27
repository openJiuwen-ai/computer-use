"""Production transport supports only the native execution protocol."""

import unittest

from computer_use.errors import ComputerUseError
from computer_use.native.connection import Connection
from computer_use.protocol.session import decodeHello


class CurrentProtocolTests(unittest.IsolatedAsyncioTestCase):
    def test_default_is_six_and_old_versions_are_not_constructible(self):
        self.assertEqual(Connection(lambda: None).protocol_version, 6)
        for version in (3, 4, 5, 7):
            with self.assertRaises(ValueError):
                Connection(lambda: None, protocol_version=version)

    async def test_removed_commands_fail_before_starting_any_driver(self):
        connection = Connection(lambda: None)
        for method in ("readApp", "listApps", "findElement", "typeTextElement"):
            with self.assertRaises(ComputerUseError) as caught:
                await connection.request(method, {})
            self.assertEqual(caught.exception.effect, "none")
            self.assertIsNone(connection._child)

    def test_default_hello_requires_the_current_protocol(self):
        reply = dict(
            version=6,
            session="native",
            id=0,
            result=dict(
                platform="macos",
                desktopMode="shared",
                maxTimeoutMs=30000,
                capabilities=["cancel", "requestDedup", "nativeOperations"],
            ),
        )
        self.assertEqual(decodeHello(reply, "shared")["session"], "native")
        with self.assertRaises(ValueError):
            decodeHello({**reply, "version": 3}, "shared")
