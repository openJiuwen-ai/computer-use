"""v5 capture uses production scope/lifecycle/SDK, with only the OS substituted."""

import io
import sys
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch

from linux_native_provider import CaptureBackend
from PIL import Image

import backends.linux.test_linux_native as base
from computer_use.client.factory import nativeComputer
from computer_use.errors import ComputerUseError
from computer_use.native.connection import Connection
from computer_use.native.contracts import HostPolicy
from computer_use.native.transport import ConnectionPort
from computer_use.protocol.captures import captureMetadata
from computer_use.protocol.execution import decodeExecutionResult
from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.objects.registry import Window
from computer_use_linux.runtime.native import NativeExecution
from computer_use_linux.system.x11 import X11


class LinuxCaptureTests(unittest.TestCase):
    def setUp(self):
        self.b = CaptureBackend()
        self.driver = NativeExecution(self.b, "session")
        self.pid = patch("computer_use_linux.windowing.native_directory.process_start", return_value="generation")
        self.pid.start()
        ctx = base.context()
        apps = self.driver.directory.applications(ctx)["applications"]
        self.application = apps[0]["id"]
        row = self.driver.directory.read(self.application, ctx)["surfaces"][0]
        self.target = base.scope(instance=row["instance"], rootWindow=row["window"], surface=row["surface"])

    def tearDown(self):
        self.driver.close()
        self.pid.stop()

    def begin(self, target=None, *, mode="read", ctx=None):
        target = self.target if target is None else target
        intent = dict(kind="read", parameters={}) if mode == "read" else dict(kind="click", parameters={})
        return self.driver.perform("begin", dict(mode=mode, target=target, intent=intent), ctx or base.context())[
            "operation"
        ]

    def read(self, *, target=None, kind="window", dimension=256):
        target = self.target if target is None else target
        operation = self.begin(target)
        try:
            return decodeExecutionResult(
                "capture",
                self.driver.perform(
                    "capture",
                    dict(operation=operation, target=target, kind=kind, maxDimension=dimension),
                    base.context(),
                ),
            )
        finally:
            self.driver.finish(operation)

    def test_window_without_accessibility_uses_native_pixels_and_image_bound_coordinates(self):
        self.b.desktop.items[0].accessible = None
        self.b.window.dead = True
        with patch.object(self.b.ax, "call", side_effect=AssertionError("Capture must not query AT-SPI nodes")):
            result = self.read(dimension=160)
        png = result["image"]
        self.assertEqual(Image.open(io.BytesIO(png)).size, (160, 120))
        self.assertEqual(result["coordinates"]["toNative"], [4, 0, 0, 4, 0, 0])
        self.assertEqual(result["binding"], self.target)
        retained = self.driver.captures.entries[captureMetadata(png)["id"]]
        self.assertEqual(retained.target, {**self.target, "capture": captureMetadata(png)["id"]})
        self.assertNotIn("png", retained.capture.image)
        self.assertEqual(self.b.leases, 0)

    def test_desktop_catalogue_preserves_untitled_windows_clips_rectangles_and_resolves_selector(self):
        self.b.desktop.items.append(
            Window(
                22, "", self.b.app.name, 42, (-20, -10, 100, 60), transient=11, modal=True, process_start="generation"
            )
        )
        self.b.ax.calls.clear()
        result = self.read(target=base.scope(), kind="desktop", dimension=256)
        self.assertEqual(self.b.ax.calls, [])
        self.assertEqual(result["desktopRef"], captureMetadata(result["screenshot"])["id"])
        observation = result["observation"]
        self.assertEqual(observation["frontmost"]["selector"], self.application)
        self.assertEqual(observation["windows"][1]["title"], "")
        self.assertEqual(observation["windows"][1]["rect"], dict(x=0, y=0, width=20, height=12.5))
        self.assertEqual({w["selector"] for w in observation["windows"]}, {self.application})
        self.assertEqual(observation["displays"][0]["name"], "X11 screen")

    def test_new_geometry_never_mutates_an_earlier_capture(self):
        first = self.read()
        first_id = captureMetadata(first["image"])["id"]
        original = self.driver.captures.entries[first_id]
        self.b.desktop.items[0].rect = (50, 30, 800, 200)
        self.b.desktop.items[0].title = "New title is not a new window"
        second = self.read()
        self.assertNotEqual(first_id, captureMetadata(second["image"])["id"])
        self.assertEqual(original.capture.window.rect, (0, 0, 640, 480))
        self.assertEqual(original.capture.transform, (0, 0, 2.5, 2.5))
        self.assertEqual(second["coordinates"]["toNative"], [3.125, 0, 0, 3.125, 50, 30])
        self.assertEqual(Image.open(io.BytesIO(first["image"])).size, (256, 192))

    def test_geometry_or_identity_changes_during_capture_cannot_publish_a_mapping(self):
        for kind in ("move", "close", "generation", "lock"):
            with self.subTest(kind=kind):
                original = replace(self.b.desktop.items[0])

                def change(*, kind=kind, original=original):
                    if kind == "move":
                        self.b.desktop.items[0].rect = (1, 2, 640, 480)
                    elif kind == "close":
                        self.b.registry.retire(11)
                    elif kind == "generation":
                        self.b.desktop.items[0] = replace(original, process_start="new")
                    else:
                        self.b.locked = True

                self.b.desktop.after_capture = change
                with self.assertRaises(Failure):
                    self.read()
                self.assertEqual(len(self.driver.captures.entries), 0)
                self.b.desktop.items[0] = original
                self.b.registry.instances[self.target["instance"]].alive = True
                self.b.locked = False

    def test_catalogue_failure_keeps_pixels_and_does_not_hide_cancellation(self):
        with patch.object(self.b.desktop, "windows", side_effect=Failure("catalogue unavailable")):
            result = self.read(target=base.scope(), kind="desktop")
        self.assertIsNotNone(result["screenshot"])
        self.assertIn("catalogue unavailable", result["observation"]["catalogError"])
        operation = self.begin(base.scope())
        ctx = base.context()
        self.b.desktop.after_capture = ctx.cancelled.set
        with self.assertRaises(Failure) as error:
            self.driver.perform(
                "capture", dict(operation=operation, target=base.scope(), kind="desktop", maxDimension=16), ctx
            )
        self.assertEqual(error.exception.code, "cancelled")
        self.assertIsNone(self.driver.current)
        self.assertEqual(len(self.driver.captures.entries), 1, "Cancelled pixels cannot acquire a coordinate binding")
        self.b.desktop.after_capture = None
        with patch.object(self.b.desktop, "windows", side_effect=Failure("native timeout", "deadline_exceeded")):
            with self.assertRaises(Failure) as timeout:
                self.read(target=base.scope(), kind="desktop")
        self.assertEqual(timeout.exception.code, "deadline_exceeded")
        self.assertEqual(len(self.driver.captures.entries), 1)

    def test_whole_operation_deadline_and_locked_desktop_stop_before_capture(self):
        ctx = base.context(0.01)
        operation = self.begin(ctx=ctx)
        time.sleep(0.02)
        with self.assertRaises(Failure) as error:
            self.driver.perform(
                "capture", dict(operation=operation, target=self.target, kind="window", maxDimension=16), base.context()
            )
        self.assertEqual(error.exception.code, "deadline_exceeded")
        self.assertIsNone(self.driver.current)
        self.assertEqual(self.b.desktop.captures, [])
        self.b.locked = True
        with self.assertRaises(Failure):
            self.read()
        self.assertEqual(self.b.desktop.captures, [])

    def test_capture_request_cannot_change_scope_or_use_a_writing_operation(self):
        operation = self.begin()
        request = dict(operation=operation, target=self.target, kind="window", maxDimension=16)
        for changed in (
            {"maxDimension": True},
            {"maxDimension": 0},
            {"maxDimension": 4097},
            {"kind": "overview"},
            {"kind": "desktop"},
            {"extra": 1},
            {"target": base.scope()},
            {"target": {**self.target, "node": "any"}},
            {"target": {**self.target, "capture": "old"}},
        ):
            with self.subTest(changed=changed), self.assertRaises(Failure):
                self.driver.perform("capture", {**request, **changed}, base.context())
        self.driver.finish(operation)
        operation = self.begin(mode="write")
        with self.assertRaises(Failure):
            self.driver.perform("capture", {**request, "operation": operation}, base.context())
        self.assertEqual(self.b.desktop.captures, [])

    def test_capture_metadata_is_bounded_and_destroyed_with_session(self):
        ids = [captureMetadata(self.read(dimension=1)["image"])["id"] for _ in range(65)]
        self.assertEqual(list(self.driver.captures.entries), ids[1:])
        self.driver.close()
        self.assertEqual(len(self.driver.captures.entries), 0)


class X11CaptureMechanisms(unittest.TestCase):
    def fake(self, **options):
        from backends.linux.test_linux_capture_lifecycle import NativeCaptureSeams
        from computer_use_linux.system.x11_capture import X11WindowCaptures

        native = NativeCaptureSeams(**options)
        self.enterContext(patch.dict(sys.modules, native.modules))
        self.enterContext(patch("time.monotonic", side_effect=lambda: native.now))
        native.manager = X11WindowCaptures(native)
        self.addCleanup(native.manager.close)
        x = X11.__new__(X11)
        x.Image, x.window_captures = Image, native.manager
        x.read_pixels = native.read_pixels
        return x, native, NS(scope="scope-1", window=1)

    def test_window_wrapper_uses_persistent_storage_and_preserves_image_mapping(self):
        x, native, instance = self.fake()
        first, transform = x.capture(instance, "scope", native.context(), max_dimension=16)
        second, other_transform = x.capture(instance, "scope", native.context(), max_dimension=16)
        self.assertEqual((first["width"], first["height"], transform), (16, 12, (20, 30, 2.5, 2.5)))
        self.assertEqual(other_transform, transform)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(sum(name == "redirect" for name, _ in native.calls), 1)
        self.assertEqual(sum(name == "unredirect" for name, _ in native.calls), 0)
        native.manager.close()
        self.assertEqual(sum(name == "unredirect" for name, _ in native.calls), 1)
        self.assertFalse(native.resources)

    def test_wrapper_keeps_readiness_gap_with_sampled_pixels(self):
        x, native, instance = self.fake(paint_delay=None)
        image, _ = x.capture(instance, "scope", native.context())
        self.assertEqual(image["stopReasons"], [native.manager.WAIT_NOTE])
        self.assertTrue(image["png"])

    def test_desktop_resolution_change_during_acquisition_is_not_published(self):
        x, native, _ = self.fake()
        geometries = iter([NS(width=640, height=480), NS(width=800, height=600)])
        x.root = NS(get_geometry=lambda: next(geometries))
        x.read_pixels = lambda drawable, width, height: Image.new("RGB", (width, height), "#cc4422")
        with self.assertRaises(Failure) as error:
            x.capture(None, "desktop", native.context())
        self.assertEqual(error.exception.code, "stale_target")


class LinuxCaptureSDKTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_app_and_desktop_capture_work_without_an_accessibility_tree(self):
        connection = Connection(
            lambda: None,
            dict(
                command=sys.executable,
                args=[str(base.ROOT / "tests/fixtures/linux_native_provider.py"), "--capture", "--no-ax"],
                timeoutMs=3000,
            ),
            protocol_version=6,
        )
        computer = nativeComputer(ConnectionPort(connection, HostPolicy()))
        try:
            app = computer.app((await computer.listApps())[0].id)
            state = await app.read(view="both")
            self.assertIsNotNone(state.screenshot)
            self.assertNotIn("Full text", state.text)
            desktop = await computer.read(view="both")
            self.assertIsNotNone(desktop.screenshot)
            self.assertEqual(captureMetadata(state.screenshot)["kind"], "window")
            self.assertEqual(captureMetadata(desktop.screenshot)["kind"], "desktop")
            self.assertNotEqual(captureMetadata(state.screenshot)["id"], captureMetadata(desktop.screenshot)["id"])
            self.assertIn("nativeCaptureKinds", connection.capabilities)
            with self.assertRaises(ComputerUseError) as error:
                await app.click(at=(10, 10))
            self.assertEqual(error.exception.effect, "none", "Image does not advertise an unimplemented input route")
        finally:
            connection.close()
            await connection.waitClosed()


if __name__ == "__main__":
    unittest.main()
