"""Protocol 5 dependency/permission entry points; no real desktop is accessed."""

import builtins
import importlib.util
import io
import json
import tempfile
import time
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from computer_use_linux.__main__ import native_backend
from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.runtime import resources
from computer_use_native_tools import native as host


@contextmanager
def no_legacy_imports():
    original = builtins.__import__
    forbidden = (
        "orca",
        "computer_use_linux.backend",
        "computer_use_linux.accessibility",
        "computer_use_linux.control",
        "computer_use_linux.read",
        "computer_use_linux.orca_adapter",
    )

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        absolute = importlib.util.resolve_name("." * level + name, globals["__package__"]) if level else name
        if any(absolute == prefix or absolute.startswith(prefix + ".") for prefix in forbidden):
            raise AssertionError("Legacy import in native path: " + absolute)
        return original(name, globals, locals, fromlist, level)

    with patch("builtins.__import__", guarded):
        yield


class LinuxResourcesTests(unittest.TestCase):
    def setUp(self):
        self.atspi = NS(init=Mock(), exit=Mock(), set_timeout=Mock(), get_desktop=Mock(return_value=object()))
        loop = NS(pending=lambda: False)
        repository = NS(
            Atspi=self.atspi, Gio=NS(), GObject=NS(Object=object), GLib=NS(MainContext=NS(default=lambda: loop))
        )
        self.enterContext(patch.dict("sys.modules", {"gi": NS(require_version=Mock()), "gi.repository": repository}))
        self.desktop = NS(session_type="x11", can_capture=True, can_input=True, notes=[], capture=Mock(), close=Mock())
        self.enterContext(patch("computer_use_linux.runtime.native_backend.connect_desktop", return_value=self.desktop))
        self.unlocked = self.enterContext(patch("computer_use_linux.runtime.native_backend.ensure_unlocked"))
        self.apps = self.enterContext(
            patch("computer_use_linux.objects.native_accessibility.NativeAccessibility.applications", return_value=[])
        )
        self.started = self.enterContext(
            patch("computer_use_linux.objects.native_accessibility.NativeAccessibility._ensure_started")
        )

    def test_native_installation_loads_real_lazy_adapter_without_bus_or_legacy(self):
        with no_legacy_imports(), patch.object(resources.ctypes, "CDLL") as library:
            resources.check_installation(6)
        self.atspi.init.assert_not_called()
        self.apps.assert_not_called()
        self.started.assert_not_called()
        self.assertEqual(library.call_count, 2)

    def test_default_installation_is_native_and_does_not_connect_to_bus(self):
        with no_legacy_imports(), patch.object(resources.ctypes, "CDLL"):
            resources.check_installation()
        self.atspi.init.assert_not_called()
        self.started.assert_not_called()

    def test_native_permissions_use_real_native_resources_not_legacy_backend(self):
        with no_legacy_imports():
            status = resources.check_permissions(6)
        self.assertEqual(status["protocolVersion"], 6)
        self.assertTrue(all(status[k] for k in ("ready", "accessibility", "screenCapture", "input")))
        self.assertNotIn("orca", status)
        self.desktop.capture.assert_called_once()
        self.desktop.close.assert_called_once()

    def test_broken_ax_does_not_hide_native_images_and_does_not_claim_full_support(self):
        self.atspi.get_desktop.side_effect = Failure("broken bus", "observation_unavailable")
        self.desktop.can_input = False
        with no_legacy_imports():
            status = resources.check_permissions(6)
        self.assertTrue(status["ready"])
        self.assertTrue(status["screenCapture"])
        self.assertFalse(status["accessibility"])
        self.assertFalse(status["input"])
        self.assertIn("observation resources only", " ".join(status["details"]))
        self.desktop.close.assert_called_once()

    def test_no_observation_route_or_lock_or_cleanup_failure_is_not_ready(self):
        for failure in ("unavailable", "locked", "cleanup"):
            with self.subTest(failure=failure):
                self.desktop.can_capture = failure != "unavailable"
                self.atspi.get_desktop.side_effect = (
                    Failure("no AX", "observation_unavailable") if failure == "unavailable" else None
                )
                self.unlocked.side_effect = Failure("locked", "input_unavailable") if failure == "locked" else None
                self.desktop.close.side_effect = RuntimeError("close failed") if failure == "cleanup" else None
                with no_legacy_imports():
                    status = resources.check_permissions(6)
                self.assertFalse(status["ready"])

    def test_legacy_and_invalid_versions_are_rejected_before_resources_start(self):
        for version in (3, True, 6.0, 5):
            for method in (resources.check_permissions, resources.check_installation):
                with self.subTest(version=version, method=method.__name__), no_legacy_imports():
                    with self.assertRaisesRegex(ValueError, "protocol 6"):
                        method(version)
        self.unlocked.assert_not_called()
        self.atspi.init.assert_not_called()
        self.desktop.capture.assert_not_called()
        self.started.assert_not_called()

    def test_absent_desktop_root_is_not_a_healthy_empty_desktop(self):
        self.desktop.can_capture = self.desktop.can_input = False
        self.atspi.get_desktop.return_value = None
        with no_legacy_imports():
            status = resources.check_permissions(6)
        self.assertFalse(status["accessibility"])
        self.assertFalse(status["ready"])
        self.assertIn("desktop root is unavailable", " ".join(status["details"]))
        self.apps.assert_not_called()
        # A present desktop can legitimately contain no applications. No
        # children are enumerated just to establish the resource connection.
        self.atspi.get_desktop.return_value = object()
        with no_legacy_imports():
            status = resources.check_permissions(6)
        self.assertTrue(status["accessibility"])
        self.assertTrue(status["ready"])
        self.apps.assert_not_called()

    def test_ax_deadline_does_not_expire_the_independent_screenshot_probe(self):
        from computer_use_linux.objects.native_accessibility import NativeAccessibility

        original_begin = NativeAccessibility.begin
        contexts = []

        def begin(ax, ctx):
            contexts.append(ctx)
            original_begin(ax, ctx)

        def slow_root(_):
            contexts[0].deadline = time.monotonic() - 1
            return object()

        def capture(_, scope, ctx):
            contexts.append(ctx)
            ctx.check()

        self.atspi.get_desktop.side_effect = slow_root
        self.desktop.capture.side_effect = capture
        with no_legacy_imports(), patch.object(NativeAccessibility, "begin", begin):
            status = resources.check_permissions(6)
        self.assertFalse(status["accessibility"])
        self.assertTrue(status["screenCapture"])
        self.assertTrue(status["ready"])
        self.assertEqual(len(contexts), 2)
        self.assertIsNot(contexts[0], contexts[1])
        self.assertIs(contexts[0].stopped, contexts[1].stopped)

    def test_native_server_factory_does_not_import_legacy_modules(self):
        import threading

        with no_legacy_imports():
            backend = native_backend("shared", threading.Event())
            backend.close()
        self.atspi.init.assert_not_called()


class LinuxHostResourceTests(unittest.TestCase):
    def test_macos_host_cannot_select_the_retired_protocol(self):
        with (
            patch.object(host.sys, "platform", "darwin"),
            patch.object(host, "buildMacOS") as build,
            patch.object(host, "nativeDriver") as driver,
        ):
            for method in (host.buildNative, host.readPermissions):
                with self.assertRaisesRegex(ValueError, "requires protocol 6"):
                    method(protocol_version=3)
            build.assert_not_called()
            driver.assert_not_called()
            host.buildNative()
            build.assert_called_once_with(None)

    def test_build_uses_native_resources_and_rejects_legacy_before_running_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            native = Path(directory) / "os_adapter/linux"
            native.mkdir(parents=True)
            (native / "requirements.lock").write_text("fixture==1\n")
            (native / ".venv/bin").mkdir(parents=True)
            (native / ".venv/bin/python").touch()
            with patch.object(host.sys, "platform", "linux"), patch.object(host, "run") as run:
                host.buildNative(directory)
                code = [call.args[1][1] for call in run.call_args_list if call.args[1][0] == "-c"]
                self.assertEqual(len(code), 2)
                self.assertTrue(all("check_installation(6" in item for item in code))
                self.assertNotIn("orca_adapter", "\n".join(code))
                self.assertIn("installed=True", code[-1])
                run.reset_mock()
                with self.assertRaisesRegex(ValueError, "protocol 6"):
                    host.buildNative(directory, protocol_version=3)
                run.assert_not_called()

    def test_host_forwards_native_check_and_requires_versioned_response(self):
        driver = dict(command="python", args=["-m", "computer_use_linux"])
        with (
            patch.object(host.sys, "platform", "linux"),
            patch.object(host, "nativeDriver", return_value=driver),
            patch.object(host.subprocess, "run") as run,
        ):
            run.return_value = NS(returncode=0, stdout=json.dumps(dict(protocolVersion=6, ready=True)))
            self.assertTrue(host.readPermissions()["ready"])
            self.assertEqual(run.call_args.args[0][-3:], ["--check-permissions", "--protocol-version", "6"])
            run.reset_mock()
            with self.assertRaisesRegex(ValueError, "protocol 6"):
                host.readPermissions(protocol_version=3)
            run.assert_not_called()
            run.return_value.stdout = '{"ready":true}'
            with self.assertRaisesRegex(ValueError, "protocol 6"):
                host.readPermissions()

    def test_default_cli_selects_native_without_returning_a_path_exit_code(self):
        with patch.object(host, "buildNative", return_value=Path("/driver")) as build:
            self.assertIsNone(host.buildCli([]))
            build.assert_called_once_with(protocol_version=6)
        with (
            patch.object(
                host,
                "readPermissions",
                return_value=dict(ready=True, accessibility=False, screenCapture=True, input=False),
            ) as read,
            patch.object(host.sys, "platform", "linux"),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(host.permissions([]), 0)
            read.assert_called_once_with(False, protocol_version=6)

    def test_invalid_versions_do_not_probe_or_build(self):
        with (
            patch.object(host.sys, "platform", "win32"),
            patch.object(host, "run") as run,
            patch.object(host.subprocess, "run") as subprocess,
        ):
            for method in (host.buildNative, host.readPermissions):
                with self.assertRaises(ValueError):
                    method(protocol_version=True)
            run.assert_not_called()
            subprocess.assert_not_called()


if __name__ == "__main__":
    unittest.main()
