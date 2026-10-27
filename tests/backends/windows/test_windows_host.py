"""Windows launch contract over real child processes, without desktop input.

On non-Windows hosts only CreateProcess's creation flag is removed by the
test adapter. This checks our argv, stdio, handshake and shutdown behavior;
it does not stand in for running UIA or the C# driver on Windows.
"""

import asyncio
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from legacy_connection import Connection as HistoricalConnection, driverTerminationGraceMs
from repository import ROOT as REPO_ROOT

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.native.connection import Connection
from computer_use.native.contracts import Binding, HostPolicy, Intent
from computer_use.native.preview import setPreviewHandler
from computer_use.native.transport import ConnectionPort
from computer_use.paths import nativeDriver
from computer_use_native_tools.native import buildNative, buildWindows, readPermissions

ROOT = REPO_ROOT
NO_WINDOW = 0x08000000


class WindowsBuildTests(unittest.TestCase):
    def test_retired_protocol_is_rejected_before_build_or_permission_probe(self):
        with (
            patch("computer_use_native_tools.native.sys", SimpleNamespace(platform="win32")),
            patch("computer_use_native_tools.native.buildWindows") as build,
            patch("computer_use_native_tools.native.nativeDriver") as driver,
        ):
            for method in (buildNative, readPermissions):
                with self.subTest(method=method.__name__), self.assertRaisesRegex(ValueError, "requires protocol 6"):
                    method(protocol_version=3)
            build.assert_not_called()
            driver.assert_not_called()
            buildNative()
            build.assert_called_once_with(None)

    def test_explicit_v5_uses_the_shared_windows_binary_and_resource_check(self):
        with patch("computer_use_native_tools.native.sys", SimpleNamespace(platform="win32")):
            with patch("computer_use_native_tools.native.buildWindows", return_value="driver.exe") as build:
                self.assertEqual(buildNative("source", protocol_version=6), "driver.exe")
                build.assert_called_once_with("source")
            # Permission flags describe the interactive desktop, never which
            # v5 object, keyboard or pointer capabilities a provider supports.
            status = dict(accessibility=True, screenCapture=True, input=True, platform="windows")
            with (
                patch(
                    "computer_use_native_tools.native.nativeDriver", return_value=dict(command="driver.exe", args=[])
                ),
                patch(
                    "computer_use_native_tools.native.subprocess.run",
                    return_value=SimpleNamespace(returncode=0, stdout=json.dumps(status)),
                ) as run,
            ):
                self.assertEqual(readPermissions(protocol_version=6), status)
                self.assertEqual(run.call_args.args[0], ["driver.exe", "--check-permissions"])

    def test_default_driver_and_build_output_agree_without_node(self):
        with tempfile.TemporaryDirectory(prefix="windows build 测试 ") as directory:
            root = Path(directory)
            assets = root / "os_adapter/assets/agent-pointer"
            assets.mkdir(parents=True)
            repository = REPO_ROOT
            for name in ("cursor.svg", "motion.json"):
                (assets / name).write_bytes((repository / "os_adapter/assets/agent-pointer" / name).read_bytes())
            (root / "os_adapter/macos/Sources/Preview").mkdir(parents=True)
            native = root / "os_adapter/windows"
            native.mkdir(parents=True)
            (native / "Sources/Preview").mkdir(parents=True)
            for name in ("Sources/Runtime/Program.cs", "Sources/Runtime/Driver.cs"):
                (native / name).parent.mkdir(parents=True, exist_ok=True)
                (native / name).write_text("// fixture")
            framework = root / "Windows/Microsoft.NET/Framework64/v4.0.30319"
            framework.mkdir(parents=True)
            (framework / "csc.exe").touch()
            for name in (
                "System.dll",
                "System.Core.dll",
                "System.Web.Extensions.dll",
                "System.Drawing.dll",
                "System.Runtime.WindowsRuntime.dll",
                "System.Runtime.dll",
            ):
                (framework / name).touch()
            metadata = root / "Windows/System32/WinMetadata"
            metadata.mkdir(parents=True)
            for name in (
                "Windows.Foundation.winmd",
                "Windows.Graphics.winmd",
                "Windows.Storage.winmd",
                "Windows.Media.winmd",
                "Windows.Globalization.winmd",
            ):
                (metadata / name).touch()
            (native / "Workers/Capture").mkdir(parents=True)
            (native / "Workers/Capture/Program.cs").write_text("// pixels")
            with (
                patch("computer_use_native_tools.native.sys", SimpleNamespace(platform="win32")),
                patch.dict(os.environ, WINDIR=str(root / "Windows")),
                patch("computer_use_native_tools.native.run") as run,
                redirect_stdout(io.StringIO()),
            ):
                executable = buildNative(root)
            self.assertEqual(nativeDriver("win32", root), dict(command=str(executable), args=[]))
            self.assertEqual(run.call_count, 4)
            importer_command, importer_args, importer_cwd = run.call_args_list[0].args
            self.assertEqual(importer_command, framework / "csc.exe")
            self.assertEqual(importer_cwd, native)
            self.assertIn(native / "Build/ImportUia.cs", importer_args)
            self.assertEqual(
                run.call_args_list[1].args, (executable.with_name("import-uia.exe"), [executable.parent], native)
            )
            command, args, cwd = run.call_args_list[2].args
            self.assertEqual(command, framework / "csc.exe")
            self.assertEqual(cwd, native)
            self.assertIn(f"/out:{executable}", args)
            self.assertIn("/platform:x64", args)
            self.assertEqual(args[-2:], [native / "Sources/Runtime/Driver.cs", native / "Sources/Runtime/Program.cs"])
            self.assertIn(f"/reference:{framework / 'WPF/UIAutomationClient.dll'}", args)
            self.assertIn(f"/reference:{executable.with_name('Interop.NativeUIA.dll')}", args)
            self.assertTrue(executable.parent.is_dir())
            capture_args = run.call_args_list[3].args[1]
            self.assertIn(f"/out:{executable.with_name('computer-use-capture.exe')}", capture_args)
            self.assertIn(native / "Workers/Capture/Program.cs", capture_args)
            self.assertIn(f"/reference:{metadata / 'Windows.Graphics.winmd'}", capture_args)

    def test_missing_compiler_and_wrong_host_fail_before_launch(self):
        with tempfile.TemporaryDirectory() as directory, patch("computer_use_native_tools.native.run") as run:
            with patch("computer_use_native_tools.native.sys", SimpleNamespace(platform="linux")):
                with self.assertRaisesRegex(RuntimeError, "on Windows"):
                    buildWindows(directory)
            with (
                patch("computer_use_native_tools.native.sys", SimpleNamespace(platform="win32")),
                patch.dict(os.environ, WINDIR=directory),
            ):
                with self.assertRaisesRegex(RuntimeError, "compiler is required"):
                    buildWindows(directory)
            run.assert_not_called()


class WindowsTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="windows-stdio-")
        self.addCleanup(self.temp.cleanup)
        self.journal = Path(self.temp.name) / "wire.jsonl"
        self.connections, self.launches = [], []
        real_spawn = asyncio.create_subprocess_exec

        async def spawn(*args, **options):
            self.launches.append((args, options.copy()))
            self.assertNotIn("pass_fds", options)
            self.assertNotIn("computer_use.native.driver_exec", args)
            self.assertEqual(options["creationflags"], NO_WINDOW)
            self.assertNotIn("COMPUTER_USE_LIFETIME_FD", options["env"])
            self.assertNotIn("COMPUTER_USE_PREVIEW_FD", options["env"])
            if os.name != "nt":
                options.pop("creationflags")
            return await real_spawn(*args, **options)

        self.enterContext(
            patch("computer_use.native.connection.sys", SimpleNamespace(platform="win32", executable=sys.executable))
        )
        self.enterContext(patch("legacy_connection.sys", SimpleNamespace(platform="win32", executable=sys.executable)))
        self.enterContext(patch("computer_use.native.connection.subprocess.CREATE_NO_WINDOW", NO_WINDOW, create=True))
        self.enterContext(patch("computer_use.native.connection.asyncio.create_subprocess_exec", spawn))

    async def asyncTearDown(self):
        setPreviewHandler(None)
        for connection in self.connections:
            connection.close()
            await connection.waitClosed()

    def connection(self, mode="cooperative", *, version=3, native=False, default=False):
        args = (
            [str(ROOT / "tests/fixtures/v5-driver.py"), mode, str(self.journal)]
            if native
            else [str(ROOT / "tests/fixtures/deadline-driver.py"), str(self.journal), mode]
        )
        driver = dict(command=sys.executable if native else sys.executable, args=args)
        if default:
            self.enterContext(patch("computer_use.native.connection.nativeDriver", return_value=driver))
            self.enterContext(patch("legacy_connection.nativeDriver", return_value=driver))
        connection = (Connection if version == 6 else HistoricalConnection)(
            lambda: None, None if default else driver, protocol_version=version
        )
        self.connections.append(connection)
        return connection

    def events(self):
        return [json.loads(line) for line in self.journal.read_text().splitlines()] if self.journal.exists() else []

    async def wait_event(self, kind):
        for _ in range(300):
            if any(item.get("event") == kind for item in self.events()):
                return
            await asyncio.sleep(0.005)
        self.fail(f"child did not report {kind}")

    async def test_default_driver_uses_stdio_without_inheriting_posix_descriptors(self):
        frames = []
        setPreviewHandler(frames.append)
        with patch.dict(os.environ, COMPUTER_USE_PREVIEW_FD="3", COMPUTER_USE_LIFETIME_FD="4"):
            connection = self.connection(default=True)
            apps = await connection.request("listApps", {})
            self.assertEqual(apps[0]["displayName"], "Fixture")
            self.assertIsNone(connection._previewTransport)
            self.assertEqual(os.environ["COMPUTER_USE_LIFETIME_FD"], "4")
        connection.close()
        await connection.waitClosed()
        self.assertEqual(frames, [])
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.launches[0][0][0], sys.executable)
        kinds = [item["event"] for item in self.events()]
        self.assertIn("eof", kinds)
        self.assertIn("released", kinds)
        self.assertNotIn("sigterm", kinds)

    async def test_same_v5_engine_uses_direct_stdio_without_a_legacy_route(self):
        connection = self.connection("normal", version=6, native=True)
        engine = Engine(ConnectionPort(connection, HostPolicy()))
        target = Binding("native-v4-fixture", "instance", None, "surface", "node")
        result = await engine.execute(Intent("action", {"action": "AXPress"}), target)
        self.assertEqual(result["status"], "dispatched")
        methods = [entry["method"] for entry in self.events()]
        self.assertEqual(methods, ["hello", "begin", "inspect", "execute", "finish"])
        self.assertTrue(all(entry["version"] == 6 for entry in self.events()))

    async def test_legacy_driver_cannot_respond_as_v5_or_trigger_a_downgrade(self):
        connection = self.connection(version=6)
        with self.assertRaisesRegex(ComputerUseError, "incompatible hello version") as caught:
            await connection.connect()
        await connection.waitClosed()
        self.assertEqual(caught.exception.effect, "none")
        self.assertEqual(len(self.launches), 1)
        self.assertEqual([v["method"] for v in self.events() if v["event"] == "request"], ["hello"])

    async def test_close_cancels_input_and_allows_eof_cleanup_before_exit(self):
        connection = self.connection("partial")
        await connection.connect()
        pending = asyncio.create_task(connection.request("typeText", dict(appRef="fixture", text="one insertion")))
        await self.wait_event("input")
        connection.close()
        with self.assertRaises(ComputerUseError) as caught:
            await pending
        await connection.waitClosed()
        self.assertEqual(caught.exception.effect, "possible")
        kinds = [item["event"] for item in self.events()]
        self.assertEqual(kinds.count("input"), 1)
        self.assertEqual(kinds.count("cancel"), 1)
        self.assertIn("released", kinds)
        self.assertNotIn("sigterm", kinds)

    async def test_uncooperative_child_exits_before_a_successor_can_start(self):
        connection = self.connection("uncooperative")
        first = await connection.request("listApps", {})
        child = connection._child
        before = asyncio.get_running_loop().time()
        connection.close()
        second = await connection.request("listApps", {})
        self.assertGreaterEqual(asyncio.get_running_loop().time() - before, driverTerminationGraceMs / 1000 * 0.9)
        self.assertIsNotNone(child.returncode)
        self.assertNotEqual(first, second)
        starts = [item for item in self.events() if item["event"] == "start"]
        self.assertEqual(len(starts), 2)
        self.assertFalse(starts[1]["previousAlive"])


if __name__ == "__main__":
    unittest.main()
