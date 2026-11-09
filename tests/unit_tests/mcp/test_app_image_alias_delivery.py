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

from computer_use.client.computer import App, computer_for_controller
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
from computer_use.interaction.agent.scope import window_scope
from computer_use.interaction.query.instances.scope import adopt
from computer_use.interaction.read.acquisition.components.support import ObservationGap
from computer_use.interaction.read.acquisition.images.capture import WindowImage
from computer_use.interaction.read.acquisition.images.composite.pipeline import InterfaceImagePipeline
from computer_use.interaction.read.views.collection import CollectionPlan
from computer_use.interaction.read.views.pipeline import WindowPart
from computer_use.interaction.read.views.publication import publish
from computer_use.interaction.write.request.model import ActionPlan
from computer_use.interaction.write.request.pipeline import ExecuteNativeWrite
from computer_use.interaction.write.ui.pipeline import InterfacePointerPipeline
from computer_use.native.contracts import Binding, HostPolicy
from computer_use.native.scopes import RunningApplication
from computer_use.native.transport import ConnectionPort
from computer_use.pipeline.core import Control
from computer_use.protocol.captures import capture_metadata, decode_capture
from computer_use.protocol.execution import decode_capture_request
from computer_use.session import images as images_state
from computer_use.session.events import action_completed
from computer_use.session.images import register_image_delivery
from computer_use.session.interfaces import WindowIdentity
from computer_use.session.observation_data import observation_data
from computer_use.session.operations import OperationBudget
from computer_use.session.state import AppState
from computer_use.session.windows import WindowReferences
from computer_use_mcp.image_delivery import prepare_image
from computer_use_mcp.runtime import Runtime
from PIL import Image
from unit_tests.test_application_window_lifetimes import directory, ref, setup, surface
from unit_tests.test_global_input_foundation import PrimitiveConnection


class AppImageAliasDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_delivery = images_state._delivery_required
        self.events = []
        self.runtime = Runtime(self.events.append)
        with execution_scope(self.runtime.executor):
            self.controller, self.app = setup()
            self.window = window_scope(self.controller, self.app, ref(self.app, "a"))
        self.alias = App(self.controller, {"selector": "editor"}, _internal=True)
        adopt(self.controller, self.alias, self.app._instance.directory)
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
            scope=lambda ref: self.runtime.executor.call(window_scope, self.controller, self.app, ref),
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

    def capture(self, ui, key, size=(100, 80), color=None, origin=(0, 0)):
        item = ui._instance.members[key]
        identity = "capture-" + str(len(self.captures))
        buffer = BytesIO()
        with Image.new("RGB", size, color or ("white" if key == "a" else "black")) as pixels:
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
            image=WindowImage(target, png, {}, dict(nativeSpace="screen", toNative=(1, 0, 0, 1, *origin))),
        )
        image, _ = InterfaceImagePipeline(self.controller).run(ui, [part])
        ui._image, ui._context = image, key
        self.captures.append(image)
        return image

    def observe(self, ui, key, presentations=(), gaps=(), color=None):
        image = self.capture(ui, key, color=color)
        text = "Observed " + key + "\n" + "\n".join(gap.detail for gap in gaps)
        return publish(self.controller, ui, text, image.png, presentations=presentations, partial=bool(gaps), gaps=gaps)

    def source_part(self, key, token, color):
        item = self.app._instance.members[key]
        target = Binding("session", item["instance"], key, item["surface"], capture=token)
        buffer = BytesIO()
        with Image.new("RGB", (100, 80), color) as pixels:
            pixels.save(buffer, format="PNG")
        png = decode_capture(dict(
            id=token, kind="window", scope=target.surface, startedAt=1, completedAt=2,
            width=100, height=80, png=base64.b64encode(buffer.getvalue()).decode(),
        ), "window")
        return WindowPart(target, image=WindowImage(
            target, png, {}, dict(nativeSpace="screen", toNative=(1, 0, 0, 1, 0, 0)),
        ))

    def compose(self, ui, keys, presentations=()):
        parts = []
        for index, key in enumerate(keys):
            image = self.capture(ui, key)
            original = image.pieces[0].image
            image = replace(original, coordinates={"nativeSpace": "screen", "toNative": (1, 0, 0, 1, index * 120, 0)})
            parts.append(WindowPart(original.target, image=image))
        self.composition_gaps = []
        canvas, _ = InterfaceImagePipeline(self.controller).run(ui, parts, on_gap=self.composition_gaps.append)
        ui._image = canvas
        self.captures.append(canvas)
        plan = CollectionPlan(keys[0], "image")
        plan.order = tuple(WindowIdentity.of(ui._instance.members[key]) for key in keys)
        for key, part in zip(keys, parts):
            identity = WindowIdentity.of(ui._instance.members[key])
            plan.requested[identity] = frozenset(("image",))
            plan.captured(ui._instance.members[key], part)
        coverage = plan.coverage(ui._instance.directory, parts, tuple(piece.window for piece in canvas.pieces))
        return publish(
            self.controller, ui, "Composite", canvas.png,
            partial=bool(self.composition_gaps), window_coverage=coverage, presentations=presentations,
        )

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

    async def test_attached_menu_model_coordinates_keep_host_and_exact_popup_receiver(self):
        item = self.app._instance.members["b"]
        item["content"] = {"kind": "menu", "host": "a", "evidence": "AXChildren"}
        result = await self.execute("observed = compose(app, ('a', 'b'))")
        text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        self.assertNotIn(f"window={ref(self.app, 'b')}", text)
        await self.click(self.window, (140, 30))
        self.assertEqual(self.last_target().root_window, "b")
        self.assertEqual(self.write.arun.call_args.args[0].intent.parameters["x"], 20)

    async def test_attached_menu_click_receipt_keeps_delivered_host_alias(self):
        self.app._instance.members["b"]["content"] = {"kind": "menu", "host": "a", "evidence": "AXChildren"}
        self.controller.engine.execute = AsyncMock(return_value={"status": "dispatched", "detail": "Click sent"})

        async def write(request, _control):
            receipt = await ExecuteNativeWrite(self.controller).arun(ActionPlan(request, target=request.target), Control())
            action_completed(receipt)
            return receipt

        self.write.arun.side_effect = write
        delivered = await self.execute("compose(app, ('a', 'b'))")
        caption = next(block for block in delivered["content"] if block["type"] == "image")["_meta"]["computer-use"]["caption"]
        self.assertIn(f"window={ref(self.app, 'a')}", caption)
        self.assertNotIn(f"window={ref(self.app, 'b')}", caption)
        result = await self.execute("click(window, (140, 30))")
        text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        self.assertIn(f"Window {ref(self.app, 'a')} / click", text)
        self.assertNotIn(f"Window {ref(self.app, 'b')} / click", text)
        native = self.controller.engine.execute.await_args.args[1]
        self.assertEqual(native.root_window, "b")
        self.assertIs(native, self.last_target())

    async def test_delayed_menu_image_does_not_bind_to_replacement_host(self):
        menu = {**self.app._instance.members["b"],
                "content": {"kind": "menu", "host": "a", "evidence": "AXChildren"}}
        adopt(self.controller, self.app, directory(surface("a"), menu))
        image = self.capture(self.app, "b")
        adopt(self.controller, self.app, directory(surface("a", scope="replacement"), menu))
        replacement = WindowIdentity.of(self.app._instance.members["a"])
        self.runtime.task_namespace["late_menu"] = image
        result = await self.execute("print_image(late_menu.png)")
        caption = next(block for block in result["content"] if block["type"] == "image")["_meta"]["computer-use"]["caption"]
        self.assertNotIn(replacement, self.app._instance.window_images)
        self.assertNotIn(f"window={ref(self.app, 'a')}", caption)
        await self.click(self.app)
        self.assertEqual(self.last_target().root_window, "b")

    async def test_delayed_shared_pixels_do_not_bind_to_replacement_ax_source(self):
        source = {**self.app._instance.members["b"],
                  "content": {"kind": "unknown", "host": "a", "evidence": "nativeWindowID"}}
        adopt(self.controller, self.app, directory(surface("a"), source))
        image = self.capture(self.app, "a")
        replacement = {**surface("b", scope="replacement"), "content": source["content"]}
        adopt(self.controller, self.app, directory(surface("a"), replacement))
        self.runtime.task_namespace["late_shared"] = image
        await self.execute("print_image(late_shared.png)")
        self.assertNotIn(WindowIdentity.of(replacement), self.app._instance.window_images)
        await self.click(self.window)
        self.assertEqual(self.last_target().root_window, "a")

    async def test_repeated_partial_images_keep_gap_once_and_deliver_current_pixels(self):
        self.runtime.task_namespace.update(
            gap=ObservationGap("tree", "observation_incomplete", "Auxiliary AX children unavailable"),
            other_gap=ObservationGap("tree", "observation_incomplete", "Other source missing"),
        )
        result = await self.execute(
            "observe(app, 'a', (), (gap,), 'red')\nobserve(app, 'a', (), (gap,), 'blue')"
        )
        self.assertEqual(sum(block["type"] == "image" for block in result["content"]), 1)
        text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        self.assertEqual(text.count("Auxiliary AX children unavailable"), 1)
        image = next(block for block in result["content"] if block["type"] == "image")
        with Image.open(BytesIO(base64.b64decode(image["data"]))) as png:
            self.assertEqual(png.getpixel((20, 30))[:3], (0, 0, 255))
        separate = await self.execute(
            "observe(app, 'a', (), (gap,), 'red')\nobserve(app, 'a', (), (other_gap,), 'blue')",
            reset_images=True,
        )
        self.assertEqual(sum(block["type"] == "image" for block in separate["content"]), 2)

    async def test_finished_attached_menu_image_is_superseded_but_unconfirmed_menu_is_kept(self):
        item = self.app._instance.members["b"]
        shown = {"id": ("session", item["surface"], "menu"), "host": "a", "state": "observed",
                 "sources": ({key: item[key] for key in ("window", "surface", "instance")},)}
        self.runtime.task_namespace.update(shown=shown, ended={**shown, "state": "ended"},
                                          unknown={**shown, "state": "unconfirmed"})
        kept = await self.execute("compose(app, ('a', 'b'), (shown,))\nobserve(app, 'a', (unknown,))")
        self.assertEqual(sum(block["type"] == "image" for block in kept["content"]), 2)
        final = await self.execute("compose(app, ('a', 'b'), (shown,))\nobserve(app, 'a', (ended,))", reset_images=True)
        self.assertEqual(sum(block["type"] == "image" for block in final["content"]), 1)

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
        b = window_scope(self.controller, self.app, ref(self.app, "b"))
        self.assertIs(WindowReferences.delivered_image(self.app._instance, b).png, second.png)
        self.assertEqual(WindowReferences.delivered_image(self.app._instance, b).pieces, second.pieces)

    async def test_old_alias_png_cannot_renew_or_replace_the_newer_delivery(self):
        await self.execute("old = observe(app, 'a')")
        await self.execute("new = observe(alias, 'a')")
        second = self.alias._image
        result = await self.execute("print_image(old.screenshot)", reset_images=True)
        text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        self.assertIn("stale", text)
        self.assertNotIn("Image point", text)
        self.assertIs(self.app._instance.delivered_image, second)
        with self.assertRaisesRegex(ComputerUseError, "not been delivered"):
            await self.click(self.app)
        await self.execute("print_image(new.screenshot)")
        await self.click(self.app)
        self.assertEqual(self.last_target(), second.pieces[0].image.target)

    async def test_scaled_window_caption_point_is_reusable_for_explicit_global_click(self):
        connection = PrimitiveConnection()
        self.controller.port = ConnectionPort(connection, HostPolicy())
        self.runtime.task_namespace["computer"]._apps[("selector", "editor")] = self.app
        result = await self.execute("picture = capture(window, 'a', (2301, 933))\nprint_image(picture.png)")
        image = next(block for block in result["content"] if block["type"] == "image")
        caption = image["_meta"]["computer-use"]["caption"]
        self.assertIn("Coordinate space: displayed image pixels.", caption)
        self.assertIn("App click: computer.app('editor').click(at=p)", caption)
        self.assertIn("Global click (explicit): computer.click(target=p)", caption)
        point_example = next(
            line.split(": ", 1)[1]
            for line in caption.splitlines()
            if line.startswith(f"Image point for window={ref(self.app, 'a')}:")
        )
        result = await self.execute(point_example.replace("(x, y)", "(100, 50)"))
        self.assertFalse(result.get("isError"), result)
        # Subsequent unrelated observations cannot relabel the retained point.
        await self.execute("observe(alias, 'b')")
        result = await self.execute("computer.click(target=p)")
        self.assertFalse(result.get("isError"), result)
        point = connection.requests[0][1]["point"]
        self.assertAlmostEqual(point["x"], 100 * 2301 / 2048)
        self.assertAlmostEqual(point["y"], 50 * 933 / 830)
        self.assertEqual(connection.requests[0][1]["target"]["capture"], "capture-0")
        parameters = connection.requests[1][1]["intent"]["parameters"]
        self.assertAlmostEqual(parameters["x"], point["x"] - 300)
        self.assertAlmostEqual(parameters["y"], point["y"] + 40)
        self.assertEqual([method for method, _ in connection.requests], ["locatePoint", "begin", "execute", "finish"])

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

    async def test_same_call_point_error_identifies_the_exact_newly_delivered_image(self):
        a = ref(self.app, "a")
        point = f"app.point((20, 30), window={a})"
        result = await self.runtime.execute({"executionId": 1, "code": (
            "from computer_use import ComputerUseError\n"
            "observed = observe(window, 'a')\n"
            "try:\n"
            f"    {point}\n"
            "except ComputerUseError as error:\n"
            "    assert error.code == 'snapshot_expired' and error.effect == 'none'\n"
            "    raise"
        )})
        self.assertTrue(result.get("isError"), result)
        image = next(block for block in result["content"] if block["type"] == "image")
        self.assertIn(f"Image point for window={a}:", image["_meta"]["computer-use"]["caption"])
        self.assertEqual(
            result["content"][-1]["text"],
            "ComputerUseError: this UI has no current image mapping\nEffect: none\n"
            f"Use the attached image (Image 1.1, Window {a}) in the next computer_use call.",
        )
        self.write.arun.assert_not_awaited()
        await self.execute(f"p = {point}")

    async def test_same_call_point_error_does_not_label_other_or_unpublished_pixels(self):
        a = ref(self.app, "a")
        point = f"app.point((20, 30), window={a})"
        stale_after_point = (
            "observe(window, 'a')\ntry:\n"
            f"    {point}\n"
            "except Exception:\n"
            "    window._image.expired = True\n"
            "    raise"
        )
        for code, has_image in (
            (f"observe(alias, 'b')\n{point}", True),
            (f"capture(window, 'a')\n{point}", False),
            (stale_after_point, True),
            (f"observe(window, 'a')\ncapture(window, 'a')\n{point}", True),
        ):
            with self.subTest(code=code):
                result = await self.runtime.execute({"executionId": len(self.events) + 1, "code": code})
                self.assertTrue(result.get("isError"), result)
                self.assertEqual(any(block["type"] == "image" for block in result["content"]), has_image)
                self.assertEqual(
                    result["content"][-1]["text"],
                    "ComputerUseError: this UI has no current image mapping\nEffect: none",
                )
        self.write.arun.assert_not_awaited()

    async def test_same_call_point_miss_names_new_fixed_window_image_without_using_it(self):
        await self.execute("combined = compose(app, ('a', 'b'))")
        b = ref(self.app, "b")
        point = f"app.point((20, 30), window={b})"
        # A fresh image of another window cannot help this fixed-window point.
        other = await self.runtime.execute({"executionId": 2, "code": f"observe(window, 'a')\n{point}"})
        self.assertTrue(other.get("isError"), other)
        self.assertNotIn("Use the attached image", other["content"][-1]["text"])

        hidden = await self.runtime.execute({"executionId": 3, "code": f"capture(scope({b}), 'b')\n{point}"})
        self.assertTrue(hidden.get("isError"), hidden)
        self.assertNotIn("Use the attached image", hidden["content"][-1]["text"])

        result = await self.runtime.execute({"executionId": 4, "code": (
            f"observed = observe(scope({b}), 'b')\n{point}"
        )})
        self.assertTrue(result.get("isError"), result)
        image, = [block for block in result["content"] if block["type"] == "image"]
        image_id = image["_meta"]["computer-use"]["imageId"]
        error = result["content"][-1]["text"]
        self.assertIn("Effect: none", error)
        self.assertIn(f"Use the attached image (Image {image_id}, Window {b}) in the next computer_use call.", error)
        self.write.arun.assert_not_awaited()

        await self.execute(f"p = {point}")
        await self.click(window_scope(self.controller, self.app, b), (20, 30))
        self.assertEqual(self.last_target(), self.captures[-1].pieces[0].image.target)

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
        fixed = WindowReferences.delivered_image(self.app._instance, self.window)
        await self.execute("new = observe(alias, 'b')")
        self.assertIs(WindowReferences.delivered_image(self.app._instance, self.window), fixed)
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
        self.assertIn(f"computer.app('editor').point((x, y), window={ref(self.app, 'b')})", text)
        self.assertNotIn("Coordinate target=computer.app(", text)
        self.assertIs(self.app._instance.delivered_image, parent)
        b = window_scope(self.controller, self.app, ref(self.app, "b"))
        await self.click(b)
        self.assertEqual(self.last_target(), popup.pieces[0].image.target)
        # Re-delivering B must not authorize unseen A in the reset model epoch.
        with self.assertRaisesRegex(ComputerUseError, "not been delivered"):
            await self.click(self.app)

    async def test_composite_delivery_bootstraps_inline_window_with_whole_canvas_pixels(self):
        await self.execute("old = observe(window, 'a')")
        old = WindowReferences.delivered_image(self.app._instance, self.window)
        await self.execute("combined = compose(app, ('a', 'b'))")
        canvas = self.app._image
        self.assertIsNot(WindowReferences.delivered_image(self.app._instance, self.window), old)
        self.assertIs(WindowReferences.delivered_image(self.app._instance, self.window).png, canvas.png)
        b = window_scope(self.controller, self.app, ref(self.app, "b"))
        self.assertIs(WindowReferences.delivered_image(self.app._instance, b).png, canvas.png)
        await self.click(b, (140, 30))
        self.assertEqual(self.last_target(), canvas.pieces[1].image.target)
        self.assertEqual(self.write.arun.call_args.args[0].intent.parameters["x"], 20)
        with self.assertRaisesRegex(ComputerUseError, "outside the selected window's captured scope"):
            await self.click(b, (20, 30))
        with self.assertRaisesRegex(ComputerUseError, "outside the selected window's captured scope"):
            await self.click(self.window, (140, 30))
        with self.assertRaisesRegex(ComputerUseError, "gap outside"):
            await self.click(b, (110, 30))
        await self.execute(f"click(scope({ref(self.app, 'b')}), (140, 30))")
        self.assertEqual(self.last_target(), canvas.pieces[1].image.target)

    async def test_composite_bounds_use_the_delivered_image_size(self):
        with patch(
            "computer_use_mcp.runtime.prepare_image", side_effect=lambda png: prepare_image(png, max_dimension=110)
        ):
            result = await self.execute("combined = compose(app, ('a', 'b'))")
        caption, = [block["_meta"]["computer-use"]["caption"] for block in result["content"] if block["type"] == "image"]
        a, b = ref(self.app, "a"), ref(self.app, "b")
        self.assertIn("Image: window 110x40 px", caption)
        self.assertIn(f"Window {a} bounds (0, 0) to (49, 39).", caption)
        self.assertIn(f"Window {b} bounds (60, 0) to (109, 39).", caption)
        await self.click(window_scope(self.controller, self.app, b), (60, 39))
        self.assertEqual(self.last_target().root_window, "b")
        self.assertEqual(self.write.arun.call_args.args[0].intent.parameters["x"], 0)

    async def test_standalone_chooser_and_parent_canvas_only_advertise_the_actual_window_point(self):
        adopt(self.controller, self.app, directory(surface("a"), surface("b", parent="a"), focus="b"))

        def chooser():
            image = self.capture(self.app, "b", (750, 610), "red", (235, 99))
            return publish(self.controller, self.app, "Save As chooser", image.png)

        def parent_canvas():
            document = self.capture(self.window, "a", (1220, 748), "white", (0, 30)).pieces[0].image
            panel = self.capture(self.window, "b", (750, 610), "red", (235, 99)).pieces[0].image
            parts = [WindowPart(image.target, image=image) for image in (document, panel)]
            canvas, _ = InterfaceImagePipeline(self.controller).run(self.window, parts)
            return publish(self.controller, self.window, "Document and chooser", canvas.png)

        self.runtime.task_namespace.update(
            chooser=lambda: self.runtime.executor.call(chooser),
            parent_canvas=lambda: self.runtime.executor.call(parent_canvas),
        )
        result = await self.execute("standalone = chooser()\ncombined = parent_canvas()")
        images = [block["_meta"]["computer-use"] for block in result["content"] if block["type"] == "image"]
        standalone, combined = [item["caption"] for item in images]
        target = ref(self.app, "b")
        self.assertIn("750x610", standalone)
        self.assertIn(f"Window reference: {target}", standalone)
        self.assertIn('Capture: "capture-0"', standalone)
        self.assertNotIn(f"Image point for window={target}:", standalone)
        self.assertNotIn("Coordinate target=", standalone)
        superseded = f"Window {target} coordinates superseded. Use Image {images[1]['imageId']} pixels."
        self.assertIn(superseded, standalone)
        self.assertIn("1220x748", combined)
        self.assertIn(f"Image point for window={target}:", combined)
        self.assertNotIn("superseded", combined)
        text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        self.assertIn(superseded, text)
        self.assertEqual(text.count(f"Image point for window={target}:"), 1)

        panel = window_scope(self.controller, self.app, target)
        point_error = await self.runtime.execute({
            "executionId": len(self.events) + 1,
            "code": (
                "from computer_use import ComputerUseError\n"
                "try:\n"
                f"    app.point((195, 164), window={target})\n"
                "except ComputerUseError as error:\n"
                "    assert error.code == 'invalid_request' and error.effect == 'none'\n"
                "    raise"
            ),
        })
        self.assertTrue(point_error.get("isError"))
        self.assertEqual(
            point_error["content"][-1]["text"],
            "ComputerUseError: the point is outside the selected window's captured scope\nEffect: none",
        )
        self.write.arun.assert_not_awaited()
        with self.assertRaisesRegex(ComputerUseError, "outside the selected window's captured scope"):
            await self.click(panel, (195, 164))
        await self.click(panel, (430, 233))
        request = self.write.arun.call_args.args[0]
        self.assertEqual(request.target.capture, "capture-2")
        self.assertEqual((request.intent.parameters["x"], request.intent.parameters["y"]), (195, 164))

        streamed = [event["output"] for event in self.events if "output" in event]
        image_captions = [block["_meta"]["computer-use"]["caption"] for block in streamed if block["type"] == "image"]
        self.assertEqual(len(image_captions), 2)
        self.assertTrue(all("Image point" not in caption for caption in image_captions))
        self.assertTrue(all("superseded" not in caption for caption in image_captions))

    async def test_same_response_keeps_distinct_window_hint_on_earlier_canvas(self):
        result = await self.execute("combined = compose(app, ('a', 'b'))\nlatest = observe(window, 'a')")
        captions = [block["_meta"]["computer-use"]["caption"] for block in result["content"] if block["type"] == "image"]
        old, latest = captions
        a, b = ref(self.app, "a"), ref(self.app, "b")
        self.assertNotIn(f"Image point for window={a}:", old)
        self.assertIn(f"Image point for window={b}:", old)
        self.assertIn(f"Window {a} coordinates superseded.", old)
        self.assertNotIn(f"Window {b} coordinates superseded.", old)
        self.assertNotIn(f"Window {a} bounds", old)
        self.assertIn(f"Window {b} bounds (120, 0) to (219, 79).", old)
        self.assertNotIn("Coordinate target=", old)
        self.assertIn(f"Image point for window={a}:", latest)
        self.assertNotIn(" bounds ", latest)
        await self.click(window_scope(self.controller, self.app, b), (140, 30))
        self.assertEqual(self.last_target().root_window, "b")
        await self.click(self.window)
        self.assertEqual(self.last_target().capture, self.captures[-1].pieces[0].image.target.capture)

    async def test_coordinate_hints_follow_capture_generation_even_when_printed_in_reverse(self):
        result = await self.execute(
            "earlier = capture(app, 'a', (100, 80), 'red')\n"
            "later = capture(alias, 'a', (120, 90), 'white')\n"
            "print_image(later.png)\nprint_image(earlier.png)"
        )
        captions = [block["_meta"]["computer-use"]["caption"] for block in result["content"] if block["type"] == "image"]
        later, earlier = captions
        self.assertIn("120x90", later)
        self.assertIn("Image point for window=", later)
        self.assertIn("100x80", earlier)
        self.assertNotIn("Image point", earlier)
        await self.click(self.window)
        self.assertEqual(self.last_target().capture, "capture-1")

    async def test_delayed_composite_retains_only_members_without_a_newer_delivered_image(self):
        # Automatic observation sampled a parent and popup before a later
        # explicit parent read. Emitting that composite later cannot roll the
        # parent back, but the independent popup must remain actionable.
        await self.execute("old = compose(app, ('a', 'b'))")
        composite = self.app._image
        await self.execute("new = observe(window, 'a')")
        current_parent = WindowReferences.delivered_image(self.app._instance, self.window)
        result = await self.execute("print_image(old.screenshot)")
        text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        expected = f"Image point for window={ref(self.app, 'b')}: p = computer.app('editor').point((x, y), window={ref(self.app, 'b')})"
        self.assertIn(expected, text)
        self.assertNotIn("Coordinate target=computer.app(", text)
        self.assertIs(WindowReferences.delivered_image(self.app._instance, self.window), current_parent)
        await self.click(self.window)
        self.assertEqual(self.last_target(), current_parent.pieces[0].image.target)
        await self.click(window_scope(self.controller, self.app, ref(self.app, "b")), (140, 30))
        self.assertEqual(self.last_target(), composite.pieces[1].image.target)

    async def test_window_recreation_keeps_current_canvas_after_temporary_observer_dies(self):
        await self.execute("combined = compose(app, ('a', 'b'))")
        canvas = self.app._image
        source = weakref.ref(canvas)
        b = window_scope(self.controller, self.app, ref(self.app, "b"))
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
        b = window_scope(self.controller, self.app, ref(self.app, "b"))
        self.assertIs(WindowReferences.delivered_image(self.app._instance, b).png, canvas.png)
        await self.click(b, (140, 30))
        self.assertEqual(self.last_target(), canvas.pieces[1].image.target)

    async def test_pending_fixed_window_sample_does_not_change_delivered_pointer_mapping(self):
        await self.execute("old = observe(window, 'a')")
        old = WindowReferences.delivered_image(self.app._instance, self.window)
        await self.execute("pending = capture(window, 'a', (200, 160))\nclick(window)")
        self.assertEqual(self.last_target(), old.pieces[0].image.target)
        await self.execute("print_image(pending.png)\nclick(window)")
        self.assertEqual(self.last_target(), old.pieces[0].image.target)
        await self.click(self.window)
        self.assertEqual(self.last_target(), WindowReferences.delivered_image(self.app._instance, self.window).pieces[0].image.target)
        self.assertNotEqual(self.last_target(), old.pieces[0].image.target)

    async def test_native_retention_tracks_delivered_and_latest_pending_exact_captures(self):
        await self.execute("old = observe(window, 'a')")
        delivered = WindowReferences.delivered_image(self.app._instance, self.window).pieces[0].image.target.capture
        await self.execute("pending = capture(window, 'a', (200, 160))")
        pending = self.window._image.pieces[0].image.target.capture
        self.assertEqual(self.controller.port.capture_references, frozenset((delivered, pending)))
        await self.execute("print_image(pending.png)")
        self.assertEqual(self.controller.port.capture_references, frozenset((pending,)))

    async def test_cross_app_retention_reaches_the_real_capture_port_for_one_hundred_other_images(self):
        connection, wire = self.controller.transport, []
        connection.protocol_version = 6
        connection.capabilities = ("nativeCaptureRetention",)

        async def capture_request(method, parameters, options):
            self.assertEqual(method, "capture")
            decoded = decode_capture_request(parameters)
            wire.append(decoded)
            target, token = decoded["target"], f"wire-{len(wire)}"
            buffer = BytesIO()
            with Image.new("RGB", (100, 80), "red" if target["rootWindow"] == "a" else "blue") as image:
                image.save(buffer, format="PNG")
            png = decode_capture(dict(
                id=token, kind="window", scope=target["surface"], startedAt=1, completedAt=2,
                width=100, height=80, png=base64.b64encode(buffer.getvalue()).decode(),
            ), "window")
            return dict(kind="capture", binding=target, image=png)

        connection.request = AsyncMock(side_effect=capture_request)
        port = self.controller.port = ConnectionPort(connection, HostPolicy())
        port._operations["read"] = (connection.session_identity, "read", None)
        other = App(self.controller, dict(
            selector="other",
            _nativeState=AppState("other", running=RunningApplication("other", "Other", "session")),
        ), _internal=True)
        adopt(self.controller, other, directory(surface("c", scope="other"), application="other", focus="c"))

        async def wire_read(ui, key):
            member = ui._instance.members[key]
            target = Binding("session", member["instance"], key, member["surface"])
            captured = await port.capture("read", target, 1024, 500)
            original = WindowImage(
                replace(target, capture=capture_metadata(captured["image"])["id"]), captured["image"], {},
                dict(nativeSpace="screen", toNative=(1, 0, 0, 1, 0, 0)),
            )
            canvas, _ = InterfaceImagePipeline(self.controller).run(ui, [WindowPart(target, image=original)])
            ui._image, ui._context = canvas, key
            return publish(self.controller, ui, "Captured " + key, canvas.png)

        self.runtime.task_namespace.update(
            other=other,
            wire_read=lambda ui, key: self.runtime.executor.run(lambda: wire_read(ui, key)),
        )
        await self.execute("first = wire_read(app, 'a')")
        original = self.app._instance.delivered_image.pieces[0].image.target
        self.assertEqual(wire[0]["retainedCaptures"], [])
        for index in range(100):
            await self.execute("wire_read(other, 'c')")
            expected = {original.capture} | ({f"wire-{index + 1}"} if index else set())
            self.assertEqual(set(wire[-1]["retainedCaptures"]), expected)
        self.assertEqual(connection.request.await_count, 101)
        self.assertFalse(hasattr(connection, "capture_references"), "retention has one I/O owner, no shadow writes")
        await self.click(self.window)
        self.assertEqual(self.last_target(), original)

    async def test_repeated_explicit_and_automatic_image_outputs_share_one_exact_picture(self):
        result = await self.execute(
            "state = observe(window, 'a')\nprint_image(state.screenshot)\nprint_image(state.screenshot)\nprint(state)"
        )
        self.assertEqual(sum(block["type"] == "image" for block in result["content"]), 1)
        self.assertEqual(sum("Image point" in block.get("text", "") for block in result["content"]), 1)
        await self.click(self.window)
        self.assertEqual(
            self.last_target(), WindowReferences.delivered_image(self.app._instance, self.window).pieces[0].image.target
        )

    async def test_identical_same_window_pixels_acknowledge_new_native_capture_once_each(self):
        result = await self.execute(
            "first = capture(app, 'a')\nprint_image(first.png)\n"
            "second = capture(alias, 'a')\nprint_image(second.png)"
        )
        self.assertEqual(sum(block["type"] == "image" for block in result["content"]), 1)
        self.assertIs(self.app._instance.delivered_image, self.alias._image)
        await self.click(self.window)
        self.assertEqual(self.last_target(), self.alias._image.pieces[0].image.target)
        self.assertNotEqual(self.last_target(), self.captures[-2].pieces[0].image.target)
        outputs = [
            event["diagnostic"] for event in self.events
            if event.get("diagnostic", {}).get("kind") == "image_output"
        ]
        self.assertEqual([event["coalesced"] for event in outputs], [False, True])
        self.assertEqual(len({event["capture"]["id"] for event in outputs}), 2)

    async def test_identical_pixels_in_independent_windows_keep_separate_coordinate_domains(self):
        result = await self.execute(
            "first = capture(app, 'a')\nprint_image(first.png)\n"
            "second = capture(alias, 'b', (100, 80), 'white')\nprint_image(second.png)"
        )
        self.assertEqual(sum(block["type"] == "image" for block in result["content"]), 2)
        self.assertEqual(self.captures[-2].png, self.captures[-1].png)
        await self.click(self.window)
        self.assertEqual(self.last_target().root_window, "a")
        await self.click(window_scope(self.controller, self.app, ref(self.app, "b")))
        self.assertEqual(self.last_target().root_window, "b")

    async def test_image_labels_count_pictures_separately_from_exact_source_callbacks(self):
        result = await self.execute(
            "first = capture(app, 'a')\nprint_image(first.png)\n"
            "second = capture(alias, 'a')\nprint_image(second.png)\n"
            "third = capture(alias, 'b', (100, 80), 'white')\nprint_image(third.png)"
        )
        images = [block for block in result["content"] if block["type"] == "image"]
        metadata = [block["_meta"]["computer-use"] for block in images]
        self.assertEqual(len(images), 2)
        self.assertEqual([item["imageId"].rsplit(".", 1)[1] for item in metadata], ["1", "2"])
        for item, key in zip(metadata, ("a", "b")):
            self.assertIn(f"computer.app('editor').point((x, y), window={ref(self.app, key)})", item["caption"])
        outputs = [
            event["diagnostic"] for event in self.events if event.get("diagnostic", {}).get("kind") == "image_output"
        ]
        self.assertEqual([event["coalesced"] for event in outputs], [False, True, False])
        self.assertEqual(len({event["capture"]["id"] for event in outputs}), 3)

    async def historical_capture_is_displayed_without_new_input_authority(self, *, ended):
        state = self.app._instance
        original, original_directory = dict(state.members["a"]), state.directory
        number, identity = ref(self.app, "a"), WindowIdentity.of(original)
        part = self.source_part("a", "historical", "red")
        part.source = SimpleNamespace(scope_confirmed=False, source_directory=original_directory)
        adopt(self.controller, self.app, directory(
            surface("b"), focus="b", partial=not ended, ended=(original,) if ended else (),
        ))
        canvas, notes = InterfaceImagePipeline(self.controller).run(self.app, [part])
        self.assertIs(canvas.png, part.image.image)
        self.assertFalse(canvas.pieces[0].scope_confirmed)
        self.assertIsNone(canvas.for_window("a"))
        self.assertEqual(notes[-1].values[0].captures[0]["ref"], number)
        self.assertFalse(notes[-1].values[0].mapping_available)
        displayed = []
        register_image_delivery(canvas.png, lambda: displayed.append("pixels"))
        self.runtime.task_namespace["historical"] = canvas
        result = await self.execute("print_image(historical.png)")
        images = [block for block in result["content"] if block["type"] == "image"]
        self.assertEqual(
            [base64.b64decode(block["data"]) for block in images], [memoryview(part.image.image).tobytes()],
        )
        caption = images[0]["_meta"]["computer-use"]["caption"]
        self.assertIn(f"Window {number} image: historical capture (1–2 native ns)", caption)
        self.assertIn("current scope unconfirmed", caption)
        self.assertNotIn("Coordinate target=", caption)
        self.assertNotIn("superseded", caption)
        self.assertEqual(displayed, ["pixels"])
        self.assertIsNone(canvas.delivered)
        self.assertIsNone(state.delivered_image)
        self.assertNotIn(identity, state.window_images)
        self.assertNotIn(identity, state.sampled_window_images)
        self.assertNotIn("a", state.members)
        self.assertNotIn("a", state.windows.values())
        self.assertNotIn("historical", self.controller.port.capture_references)
        with self.assertRaisesRegex(ComputerUseError, "current scope is unconfirmed"):
            canvas.hit((20, 30))

    async def test_unconfirmed_capture_keeps_exact_historical_pixels_without_current_mapping(self):
        await self.historical_capture_is_displayed_without_new_input_authority(ended=False)

    async def test_authoritatively_ended_capture_keeps_exact_historical_pixels_without_current_mapping(self):
        await self.historical_capture_is_displayed_without_new_input_authority(ended=True)

    async def test_reused_native_slot_displays_both_lifetimes_without_rebinding_the_historical_region(self):
        original_directory = self.app._instance.directory
        old_number = ref(self.app, "a")
        historical = self.source_part("a", "historical", "red")
        historical.source = SimpleNamespace(scope_confirmed=False, source_directory=original_directory)
        adopt(self.controller, self.app, directory(surface("a", scope="replacement"), surface("b")))
        current_number = ref(self.app, "a")
        self.assertNotEqual(current_number, old_number)
        current = self.source_part("a", "current", "blue")
        gaps = []
        canvas, notes = InterfaceImagePipeline(self.controller).run(
            self.app,
            [historical, current],
            on_gap=gaps.append,
        )
        self.assertEqual(len(canvas.pieces), 2)
        self.assertEqual([piece.scope_confirmed for piece in canvas.pieces], [False, True])
        self.assertEqual([capture["ref"] for capture in notes[-1].values[0].captures], [old_number, current_number])
        with Image.open(BytesIO(canvas.png)) as pixels:
            self.assertEqual(pixels.getpixel((20, 30))[:3], (255, 0, 0))
            self.assertEqual(pixels.getpixel((136, 30))[:3], (0, 0, 255))
        self.assertEqual(len(gaps), 1)
        self.runtime.task_namespace["states"] = canvas
        result = await self.execute("print_image(states.png)")
        text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        self.assertIn(
            f"Image point for window={current_number}: p = computer.app('editor').point((x, y), window={current_number})",
            text,
        )
        self.assertNotIn(f"Image point for window={old_number}:", text)
        image = next(block for block in result["content"] if block["type"] == "image")
        self.assertIn("arrangement is not a live window layout", image["_meta"]["computer-use"]["caption"])
        self.assertIn("separate", gaps[0].detail)
        await self.click(self.app, (136, 30))
        self.assertEqual(self.last_target(), current.image.target)
        with self.assertRaisesRegex(ComputerUseError, "current scope is unconfirmed"):
            await self.click(self.app, (20, 30))
        self.assertEqual(set(self.controller.port.capture_references), {"current"})

    async def test_budget_fallback_delivers_only_the_shown_window_mapping_and_coverage(self):
        clock = SimpleNamespace(now=0.0)
        budget = OperationBudget(1000, clock=lambda: clock.now)
        clock.now = 1.001
        with patch(
            "computer_use.interaction.read.acquisition.images.composite.pipeline.current_operation_budget",
            return_value=budget,
        ):
            result = await self.execute("limited = compose(app, ('a', 'b'))")
        self.assertEqual(sum(block["type"] == "image" for block in result["content"]), 1)
        state = self.runtime.task_namespace["limited"]
        data = observation_data(state)
        self.assertTrue(data["partial"])
        coverage = {record["window"]: record for record in data["window_coverage"]}
        self.assertEqual(coverage["a"]["captured"], ("image",))
        self.assertEqual(coverage["a"]["included"], ("image",))
        self.assertEqual(coverage["a"]["delivered"], ("image",))
        self.assertEqual(coverage["b"]["captured"], ("image",))
        self.assertEqual(coverage["b"]["included"], ())
        self.assertEqual(coverage["b"]["delivered"], ())
        self.assertEqual(coverage["b"]["missing"], ("image",))
        self.assertEqual(len(self.composition_gaps), 1)
        canvas = self.app._instance.delivered_image
        self.assertIs(canvas.png, self.captures[0].png)
        self.assertEqual([piece.window for piece in canvas.pieces], ["a"])
        self.assertNotIn(WindowIdentity.of(self.app._instance.members["b"]), self.app._instance.window_images)
        await self.click(self.window)
        self.assertEqual(self.last_target(), canvas.pieces[0].image.target)
        self.assertEqual(self.write.arun.call_args.args[0].intent.parameters, {"x": 20.0, "y": 30.0})
        b = window_scope(self.controller, self.app, ref(self.app, "b"))
        with self.assertRaisesRegex(ComputerUseError, "no current image mapping"):
            await self.click(b)

    async def test_fallback_raw_pixels_replace_composite_coordinates_only_after_delivery(self):
        await self.execute("original = compose(app, ('a', 'b'))")
        original = self.app._instance.delivered_image
        old_target = original.pieces[1].image.target
        clock = SimpleNamespace(now=0.0)
        budget = OperationBudget(1000, clock=lambda: clock.now)
        clock.now = 1.001
        with patch(
            "computer_use.interaction.read.acquisition.images.composite.pipeline.current_operation_budget",
            return_value=budget,
        ):
            await self.execute("limited = compose(app, ('b', 'a'))\nclick(app, (140, 30))")
        self.assertEqual(self.last_target(), old_target)
        self.assertEqual(original.pieces[1].box, (120, 0, 100, 80))
        current = self.app._instance.delivered_image
        self.assertEqual(current.pieces[0].window, "b")
        self.assertEqual(current.pieces[0].box, (0, 0, 100, 80))
        self.assertIsNot(current.png, original.png)
        b = window_scope(self.controller, self.app, ref(self.app, "b"))
        await self.click(b, (20, 30))
        self.assertEqual(self.last_target(), current.pieces[0].image.target)
        self.assertNotEqual(self.last_target(), old_target)
        with self.assertRaisesRegex(ComputerUseError, "gap outside"):
            await self.click(b, (140, 30))

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
