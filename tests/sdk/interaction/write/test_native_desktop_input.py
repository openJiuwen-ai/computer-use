"""Public desktop intentions use the same Engine and operation lifecycle."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from repository import ROOT as REPO_ROOT

from computer_use import ClickOption
from computer_use.client.factory import nativeComputer
from computer_use.errors import ComputerUseError
from computer_use.native.connection import Connection
from computer_use.native.contracts import Binding, HostPolicy, Intent
from computer_use.native.transport import ConnectionPort

ROOT = REPO_ROOT


class DesktopInputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="native-desktop-input-")
        self.journal = Path(self.temp.name) / "wire.jsonl"
        self.connections = []
        self.environment = patch.dict(os.environ, COMPUTER_USE_DESKTOP="exclusive")
        self.environment.start()

    async def asyncTearDown(self):
        for connection in self.connections:
            connection.close()
            await connection.waitClosed()
        self.environment.stop()
        self.temp.cleanup()

    def computer(self, mode="desktop-input"):
        connection = Connection(
            lambda: None,
            dict(
                command=sys.executable,
                args=[str(ROOT / "tests/fixtures/v5-driver.py"), mode, str(self.journal)],
                timeoutMs=3000,
            ),
            protocol_version=6,
        )
        self.connections.append(connection)
        return nativeComputer(ConnectionPort(connection, HostPolicy(os.environ["COMPUTER_USE_DESKTOP"])))

    def requests(self):
        return [json.loads(line) for line in self.journal.read_text().splitlines()] if self.journal.exists() else []

    async def test_all_public_intents_bind_exact_capture_and_finish_without_automatic_read(self):
        computer = self.computer()
        await computer.read(view="both")
        ref = computer._controller.desktop.target()
        operations = [
            ("click", lambda: computer.click(ClickOption(button="right", count=2), at=(1, 1))),
            ("scroll", lambda: computer.scroll("down", at=(1, 1), pages=0.5)),
            ("drag", lambda: computer.drag((1, 1), (2, 1))),
            ("insert", lambda: computer.typeText("中文 hello")),
            ("key", lambda: computer.pressKey("Return")),
        ]
        for intent, call in operations:
            with self.subTest(intent):
                start = len(self.requests())
                result = await call()
                self.assertEqual(result.status, "dispatched")
                frames = self.requests()[start:]
                self.assertEqual([r["method"] for r in frames], ["begin", "inspect", "execute", "finish"])
                begin, execute = frames[0]["params"], frames[2]["params"]
                self.assertEqual(begin["intent"]["kind"], intent)
                self.assertEqual(
                    begin["target"],
                    dict(
                        session=computer._controller.transport.session,
                        instance=None,
                        rootWindow=None,
                        surface=None,
                        node=None,
                        capture=ref,
                    ),
                )
                self.assertEqual(execute["command"]["target"], begin["target"])
                self.assertNotIn("parameters", execute["command"])
                self.assertFalse(execute["command"]["foreground"])

    async def test_empty_insertion_observes_scope_but_never_executes(self):
        computer = self.computer()
        await computer.read(view="both")
        result = await computer.typeText("")
        self.assertEqual(result.status, "verified")
        self.assertEqual([r["method"] for r in self.requests()][-3:], ["begin", "inspect", "finish"])

    async def test_shared_missing_observation_or_missing_image_cannot_send_desktop_input(self):
        computer = self.computer()
        with self.assertRaises(ComputerUseError):
            await computer.pressKey("Return")
        self.assertFalse(self.requests())
        with patch.dict(os.environ, COMPUTER_USE_DESKTOP="shared"):
            computer = self.computer()
            await computer.read(view="both")
            count = len(self.requests())
            for call in (lambda: computer.click(at=(1, 1)), lambda: computer.typeText("test")):
                with self.assertRaises(ComputerUseError) as error:
                    await call()
                self.assertEqual(error.exception.code, "mode_unsupported")
            self.assertEqual(len(self.requests()), count)
        computer = self.computer("desktop-no-image")
        await computer.read(view="both")
        count = len(self.requests())
        with self.assertRaises(ComputerUseError):
            await computer.click(at=(1, 1))
        self.assertEqual(len(self.requests()), count)

    async def test_missing_native_capability_refuses_before_write_begin(self):
        computer = self.computer("desktop-input-missing-capability")
        await computer.read(view="both")
        with self.assertRaises(ComputerUseError) as error:
            await computer.click(at=(1, 1))
        self.assertEqual(error.exception.code, "mode_unsupported")
        self.assertFalse(any(r["method"] == "begin" and r["params"]["mode"] == "write" for r in self.requests()))

    async def test_partial_or_unknown_effect_is_not_replayed_and_requires_fresh_observation(self):
        for mode in ("partial", "unknown"):
            with self.subTest(mode):
                computer = self.computer("desktop-input-" + mode)
                await computer.read(view="both")
                start = len(self.requests())
                with self.assertRaises(ComputerUseError):
                    await computer.typeText("one attempt")
                self.assertEqual(
                    [r["method"] for r in self.requests()[start:]], ["begin", "inspect", "execute", "finish"]
                )
                count = len(self.requests())
                with self.assertRaises(ComputerUseError):
                    await computer.pressKey("Return")
                self.assertEqual(len(self.requests()), count)

    async def test_discovery_scope_remains_read_only(self):
        computer = self.computer()
        await computer.read(view="both")
        with self.assertRaises(ComputerUseError) as error:
            await computer._controller.engine.execute(
                Intent("key", dict(key="Return")), Binding(computer._controller.transport.session, None, None, None)
            )
        self.assertEqual(error.exception.code, "invalid_request")
        self.assertFalse(any(r["method"] == "execute" for r in self.requests()))


if __name__ == "__main__":
    unittest.main()
