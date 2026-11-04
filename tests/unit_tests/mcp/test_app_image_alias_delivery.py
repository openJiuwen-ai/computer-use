# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""App aliases consume the same actually delivered image; fixed Windows keep theirs."""

import base64
import gc
import unittest
import weakref
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from computer_use.client.computer import computer_for_controller
from computer_use.client.execution import execution_scope
from computer_use.client.host import (
    set_action_handler,
    set_diagnostic_handler,
    set_execution_guard,
    set_observation_handler,
    set_preview_handler,
    set_text_page_handler,
)
from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.images.capture import WindowImage
from computer_use.interaction.read.acquisition.images.composite.pipeline import InterfaceImagePipeline
from computer_use.interaction.read.views.pipeline import WindowPart
from computer_use.interaction.read.views.publication import publish
from computer_use.interaction.write.ui.pipeline import InterfacePointerPipeline
from computer_use.native.contracts import Binding
from computer_use.protocol.captures import decode_capture
from computer_use.session import images as images_state
from computer_use_mcp.runtime import Runtime
from PIL import Image
from unit_tests.test_application_window_lifetimes import ref, setup


class AppImageAliasDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_delivery = images_state._delivery_required
        self.events = []
        self.runtime = Runtime(self.events.append)
        with execution_scope(self.runtime.executor):
            self.controller, self.app = setup()
            self.window = computer_for_controller(self.controller).window(ref(self.app, "a"))
        self.alias = self.window._app
        self.assertIsNot(self.alias, self.app)
        self.assertIs(self.alias._instance, self.app._instance)
        self.captures = []
        self.write = SimpleNamespace(arun=AsyncMock(return_value="dispatched"))
        self.pointer = InterfacePointerPipeline(self.controller, self.write)
        self.runtime.task_namespace.update(
            app=self.app,
            computer=computer_for_controller(self.controller),
            compose=lambda *args: self.runtime.executor.call(self.compose, *args),
            alias=self.alias,
            window=self.window,
            observe=lambda *args: self.runtime.executor.call(self.observe, *args),
            capture=lambda *args: self.runtime.executor.call(self.capture, *args),
            click=lambda ui, point=(20, 30): self.runtime.executor.run(lambda: self.click(ui, point)),
            publish_text=lambda ui: self.runtime.executor.call(publish, self.controller, ui, "Text only", None),
        )

    async def asyncTearDown(self):
        await self.runtime.close()
        for setter in (
            set_action_handler,
            set_diagnostic_handler,
            set_execution_guard,
            set_observation_handler,
            set_preview_handler,
            set_text_page_handler,
        ):
            setter(None)
        images_state._delivery_required = self.previous_delivery

    def capture(self, ui, key, size=(100, 80)):
        item = ui._instance.members[key]
        identity = "capture-" + str(len(self.captures))
        buffer = BytesIO()
        with Image.new("RGB", size, "white" if key == "a" else "black") as pixels:
            pixels.save(buffer, format="PNG")
        target = Binding("session", item["instance"], key, item["surface"], capture=identity)
        png = decode_capture(
            dict(
                id=identity,
                kind="window",
                scope=target.surface,
                startedAt=1,
                completedAt=2,
                width=size[0],
                height=size[1],
                png=base64.b64encode(buffer.getvalue()).decode(),
            ),
            "window",
        )
        part = WindowPart(
            target,
            image=WindowImage(target, png, {}, dict(nativeSpace="screen", toNative=(1, 0, 0, 1, 0, 0))),
        )
        image, _ = InterfaceImagePipeline(self.controller).run(ui, [part])
        ui._image, ui._context = image, key
        self.captures.append(image)
        return image

    def observe(self, ui, key):
        image = self.capture(ui, key)
        return publish(self.controller, ui, "Observed " + key, image.png)

    def compose(self, ui, keys):
        parts = []
        for index, key in enumerate(keys):
            image = self.capture(ui, key)
            original = image.pieces[0].image
            image = replace(original, coordinates={"nativeSpace": "screen", "toNative": (1, 0, 0, 1, index * 120, 0)})
            parts.append(WindowPart(original.target, image=image))
        canvas, _ = InterfaceImagePipeline(self.controller).run(ui, parts)
        ui._image = canvas
        self.captures.append(canvas)
        return publish(self.controller, ui, "Composite", canvas.png)

    async def click(self, ui, point=(20, 30)):
        # Exercise actual image selection/mapping and dispatch target, without UI input.
        with patch("computer_use.interaction.write.ui.pipeline.refresh", new_callable=AsyncMock):
            return await self.pointer.run(ui, "click", (point,), {}, None)

    async def execute(self, code, *, reset_images=False):
        if reset_images:
            self.runtime.executor.call(images_state.reset_image_delivery)
        result = await self.runtime.execute(
            dict(
                code=code,
                executionId=len(self.events) + 1,
            )
        )
        self.assertFalse(result.get("isError"), result)
        return result

    def last_target(self):
        return self.write.arun.call_args.args[0].target

    async def test_postwrite_owner_alias_image_updates_user_app_coordinates(self):
        await self.execute("old = observe(app, 'a')")
        first = self.app._image
        await self.execute("new = observe(alias, 'b')")
        second = self.alias._image
        self.assertIs(self.app._image, first)
        self.assertIs(self.app._instance.delivered_image, second)
        await self.click(self.app)
        self.assertEqual(self.last_target(), second.pieces[0].image.target)
        self.assertEqual(self.last_target().root_window, "b")
        # Selection from the delivered single-window observation can reuse exactly its PNG.
        b = self.app.window(ref(self.app, "b"))
        self.assertIs(b._image.png, second.png)
        self.assertEqual(b._image.pieces, second.pieces)

    async def test_old_alias_png_cannot_renew_or_replace_the_newer_delivery(self):
        await self.execute("old = observe(app, 'a')")
        await self.execute("new = observe(alias, 'a')")
        second = self.alias._image
        result = await self.execute("print_image(old.screenshot)", reset_images=True)
        text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        self.assertIn("stale", text)
        self.assertNotIn("Use this displayed image's pixels", text)
        self.assertIs(self.app._instance.delivered_image, second)
        with self.assertRaisesRegex(ComputerUseError, "not been delivered"):
            await self.click(self.app)
        await self.execute("print_image(new.screenshot)")
        await self.click(self.app)
        self.assertEqual(self.last_target(), second.pieces[0].image.target)

    async def test_pending_sample_does_not_revoke_delivered_image_or_grant_new_coordinates(self):
        await self.execute("old = observe(app, 'a')")
        first = self.app._instance.delivered_image
        await self.execute("pending = capture(alias, 'b')\nclick(app)")
        self.assertIs(self.app._instance.delivered_image, first)
        self.assertEqual(self.last_target(), first.pieces[0].image.target)
        # Even explicit image output only commits when the tool call finishes.
        await self.execute("print_image(pending.png)\nclick(app)")
        self.assertEqual(self.last_target(), first.pieces[0].image.target)
        second = self.alias._image
        self.assertIs(self.app._instance.delivered_image, second)
        await self.click(self.app)
        self.assertEqual(self.last_target(), second.pieces[0].image.target)

    async def test_text_only_delivery_does_not_commit_an_unseen_image(self):
        await self.execute("pending = capture(alias, 'b')\ntext = publish_text(alias)\nprint(text)")
        self.assertIsNone(self.app._instance.delivered_image)
        with self.assertRaisesRegex(ComputerUseError, "no current image mapping"):
            await self.click(self.app)

    async def test_image_only_alias_delivery_keeps_its_exact_mcp_scale_and_native_target(self):
        result = await self.execute("large = capture(alias, 'b', (4096, 1278))\nprint_image(large.png)")
        text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        self.assertIn(f"Window reference: {ref(self.app, 'b')}", text)
        self.assertNotIn("Window reference: unknown", text)
        image = self.alias._image
        self.assertIs(self.app._instance.delivered_image, image)
        self.assertEqual(image.delivered_coordinates.delivered_size, (2048, 639))
        await self.click(self.app)
        request = self.write.arun.call_args.args[0]
        self.assertEqual(request.target, image.pieces[0].image.target)
        self.assertEqual(request.intent.parameters["x"], 40)
        self.assertEqual(request.intent.parameters["y"], 60)

    async def test_fixed_window_image_survives_other_app_alias_deliveries(self):
        await self.execute("fixed = observe(window, 'a')")
        fixed = self.window._image
        await self.execute("new = observe(alias, 'b')")
        self.assertIs(self.window._image, fixed)
        await self.click(self.window)
        self.assertEqual(self.last_target(), fixed.pieces[0].image.target)
        second = self.alias._image
        await self.execute("print_image(fixed.screenshot)")
        self.assertIs(self.app._instance.delivered_image, second)
        await self.click(self.app)
        self.assertEqual(self.last_target(), second.pieces[0].image.target)

    async def test_older_independent_popup_output_keeps_exact_mapping_without_replacing_app_canvas(self):
        await self.execute("popup = capture(alias, 'b')\nparent = observe(app, 'a')")
        parent = self.app._instance.delivered_image
        popup = self.alias._image
        result = await self.execute("print_image(popup.png)", reset_images=True)
        text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        self.assertNotIn("stale", text)
        self.assertIn(f"computer.window({ref(self.app, 'b')})", text)
        self.assertNotIn("Coordinate target=computer.app(", text)
        self.assertIs(self.app._instance.delivered_image, parent)
        b = self.app.window(ref(self.app, "b"))
        await self.click(b)
        self.assertEqual(self.last_target(), popup.pieces[0].image.target)
        # Re-delivering B must not authorize unseen A in the reset model epoch.
        with self.assertRaisesRegex(ComputerUseError, "not been delivered"):
            await self.click(self.app)

    async def test_composite_delivery_bootstraps_inline_window_with_whole_canvas_pixels(self):
        await self.execute("old = observe(window, 'a')")
        old = self.window._image
        await self.execute("combined = compose(app, ('a', 'b'))")
        canvas = self.app._image
        self.assertIsNot(self.window._image, old)
        self.assertIs(self.window._image.png, canvas.png)
        b = self.app.window(ref(self.app, "b"))
        self.assertIs(b._image.png, canvas.png)
        await self.click(b, (140, 30))
        self.assertEqual(self.last_target(), canvas.pieces[1].image.target)
        self.assertEqual(self.write.arun.call_args.args[0].intent.parameters["x"], 20)
        with self.assertRaisesRegex(ComputerUseError, "gap outside"):
            await self.click(b, (20, 30))
        with self.assertRaisesRegex(ComputerUseError, "gap outside"):
            await self.click(self.window, (140, 30))
        await self.execute(f"click(computer.window({ref(self.app, 'b')}), (140, 30))")
        self.assertEqual(self.last_target(), canvas.pieces[1].image.target)

    async def test_delayed_composite_retains_only_members_without_a_newer_delivered_image(self):
        # Automatic observation sampled a parent and popup before a later
        # explicit parent read. Emitting that composite later cannot roll the
        # parent back, but the independent popup must remain actionable.
        await self.execute("old = compose(app, ('a', 'b'))")
        composite = self.app._image
        await self.execute("new = observe(window, 'a')")
        current_parent = self.window._image
        result = await self.execute("print_image(old.screenshot)")
        text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        expected = f"Coordinate inputs for this displayed canvas: computer.window({ref(self.app, 'b')})."
        self.assertIn(expected, text)
        self.assertNotIn("Coordinate target=computer.app(", text)
        self.assertIs(self.window._image, current_parent)
        await self.click(self.window)
        self.assertEqual(self.last_target(), current_parent.pieces[0].image.target)
        await self.click(self.app.window(ref(self.app, "b")), (140, 30))
        self.assertEqual(self.last_target(), composite.pieces[1].image.target)

    async def test_window_recreation_keeps_current_canvas_after_temporary_observer_dies(self):
        await self.execute("combined = compose(app, ('a', 'b'))")
        canvas = self.app._image
        source = weakref.ref(canvas)
        b = self.app.window(ref(self.app, "b"))
        handle = weakref.ref(b)
        del b
        gc.collect()
        self.assertIsNone(handle())
        self.app._image = None
        self.app._instance.delivered_image = None
        self.captures.clear()
        del canvas
        gc.collect()
        self.assertIsNotNone(source())
        canvas = source()
        b = self.app.window(ref(self.app, "b"))
        self.assertIs(b._image.png, canvas.png)
        await self.click(b, (140, 30))
        self.assertEqual(self.last_target(), canvas.pieces[1].image.target)

    async def test_pending_fixed_window_sample_does_not_change_delivered_pointer_mapping(self):
        await self.execute("old = observe(window, 'a')")
        old = self.window._image
        await self.execute("pending = capture(window, 'a', (200, 160))\nclick(window)")
        self.assertEqual(self.last_target(), old.pieces[0].image.target)
        await self.execute("print_image(pending.png)\nclick(window)")
        self.assertEqual(self.last_target(), old.pieces[0].image.target)
        await self.click(self.window)
        self.assertEqual(self.last_target(), self.window._image.pieces[0].image.target)
        self.assertNotEqual(self.last_target(), old.pieces[0].image.target)

    async def test_native_retention_tracks_delivered_and_latest_pending_exact_captures(self):
        await self.execute("old = observe(window, 'a')")
        delivered = self.window._image.pieces[0].image.target.capture
        await self.execute("pending = capture(window, 'a', (200, 160))")
        pending = self.window._image.pieces[0].image.target.capture
        self.assertEqual(self.controller.transport.capture_references, frozenset((delivered, pending)))
        await self.execute("print_image(pending.png)")
        self.assertEqual(self.controller.transport.capture_references, frozenset((pending,)))

    async def test_direct_sdk_return_commits_png_without_mcp_authorization(self):
        set_observation_handler(None)
        images_state._delivery_required = False
        try:
            state = self.observe(self.alias, "b")
            image = self.alias._image
            self.assertIs(state.screenshot, image.png)
            self.assertIs(self.app._instance.delivered_image, image)
            await self.click(self.app)
            self.assertEqual(self.last_target(), image.pieces[0].image.target)
        finally:
            set_observation_handler(self.runtime.observation)
            images_state._delivery_required = True
