# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""MCP output geometry and subsequent input share the actual delivered PNG space."""

import base64
import unittest
from io import BytesIO, StringIO
from unittest.mock import patch

from computer_use.client.computer import computer_for_controller
from computer_use.client.execution import execution_scope
from computer_use.client.factory import session_controller
from computer_use.client.host import (
    set_action_handler,
    set_diagnostic_handler,
    set_execution_guard,
    set_observation_handler,
    set_preview_handler,
    set_text_page_handler,
)
from computer_use.client.images import image_delivery_epoch, image_delivery_required, register_image
from computer_use.errors import ComputerUseError
from computer_use.identity import Record
from computer_use.interaction.read.presentation.content import DisplayText
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.ir import freeze
from computer_use.native.contracts import HostPolicy
from computer_use.native.transport import ConnectionPort
from computer_use.protocol.png import PNG, png_size
from computer_use.session import images as images_state
from computer_use.session.desktop_store import DesktopImageAccess, DesktopStore
from computer_use.session.events import observation_completed, register_observation_delivery
from computer_use.session.observation import (
    CapturedTextDocument,
    lazy_observation,
    observation_document,
    observation_screenshot,
)
from computer_use_mcp.image_delivery import prepare_image, project_document
from computer_use_mcp.runtime import Runtime
from PIL import Image
from unit_tests.test_global_input_foundation import PrimitiveConnection


def encoded_image(size):
    with Image.new("RGB", size, "white") as image:
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return PNG(buffer.getvalue())


def desktop_document(size, *, captured=True):
    width, height = size
    value = dict(
        canvas=dict(width=width, height=height),
        frontmost=dict(name="Fixture", selector="fixture"),
        displays=[
            dict(
                name="Display",
                rect=dict(x=0, y=0, width=width, height=height),
                captured=captured,
                error=None if captured else "capture unavailable",
            )
        ],
        windows=[
            dict(
                app="Fixture",
                title="Target document",
                selector="fixture",
                rect=dict(x=814, y=150, width=800, height=600),
            )
        ],
        omittedWindows=0,
    )
    return value, DisplayDocument.join(
        [DisplayText("desktop", (freeze(value), freeze({"fixture": 2}), freeze({"0": 7})))]
    )


class ImageDeliveryCoordinateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_delivery = images_state._delivery_required
        self.events, self.deliveries = [], []
        self.runtime = Runtime(self.events.append)
        self.source = Record()
        self.store = DesktopStore(DesktopImageAccess(register_image, image_delivery_epoch, image_delivery_required))
        self.runtime.task_namespace.update(
            map_point=lambda x, y: self.runtime.executor.call(self.store.map_parameters, "click", dict(x=x, y=y)),
            assert_unseen=lambda: self.runtime.executor.call(self.assert_unseen),
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

    def capture(self, size=(4096, 1278), *, screenshot=True, full=False, details_document=None):
        facts, document = desktop_document(size, captured=screenshot)
        png = encoded_image(size) if screenshot else None
        state = lazy_observation(document, png, details_document=details_document)

        def publish():
            self.store.record(
                dict(
                    desktopRef="desktop:fixture",
                    binding={"session": "session"},
                    observation=facts,
                    screenshot=png,
                )
            )
            register_observation_delivery(state, lambda: self.deliveries.append(state))
            observation_completed(
                dict(
                    source=self.source,
                    scope="desktop",
                    view="desktop",
                    state=state,
                    document=document,
                    refs=[],
                    full=full,
                )
            )
            return state

        self.runtime.task_namespace["read"] = lambda: self.runtime.executor.call(publish)
        return facts, document, state

    def assert_unseen(self):
        self.assertEqual(self.deliveries, [])
        with self.assertRaises(ComputerUseError) as failure:
            self.store.target(True)
        self.assertIn("not been returned", str(failure.exception))

    async def execute(self, code, *, reset_images=False):
        if reset_images:
            self.runtime.executor.call(images_state.reset_image_delivery)
        return await self.runtime.execute(
            dict(
                code=code,
                executionId=len(self.events) + 1,
            )
        )

    def assert_delivery(self, result, size=(2048, 639)):
        self.assertFalse(result.get("isError"), result)
        images = [base64.b64decode(block["data"]) for block in result["content"] if block["type"] == "image"]
        self.assertEqual(len(images), 1)
        self.assertEqual(png_size(images[0]), dict(width=size[0], height=size[1]))
        text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        self.assertIn(f"Screenshot: desktop {size[0]}x{size[1]}", text)
        self.assertIn(f"Image: desktop {size[0]}x{size[1]}", text)
        self.assertIn("window=7; application: computer.app(2)", text)
        self.assertNotIn("4096x1278", text)
        self.assertEqual(text.count("Desktop: full view"), 1)
        return text

    async def test_automatic_delivery_binds_a_text_image_pair_and_preserves_source_capture(self):
        facts, document, state = self.capture()
        source = state.screenshot
        original_png = memoryview(source).tobytes()
        result = await self.execute("obs = read()\nassert_unseen()")
        text = self.assert_delivery(result)
        self.assertIn('"Display" (0, 0, 2048, 639)', text)
        self.assertIn('"Target document" (407, 75, 400, 300)', text)
        self.assertEqual(self.deliveries, [state])
        # A button visually at (515, 125) returns to the source target (1030, 250),
        # rather than the failing log's doubled (2060, 500).
        self.assertEqual(self.store.map_parameters("click", dict(x=515, y=125)), dict(x=1030, y=250))
        self.store.check_coordinates(515, 125)
        self.assertIn("Screenshot: desktop 2048x639", state.text)
        self.assertEqual(png_size(state.screenshot), {"width": 2048, "height": 639})
        self.assertIs(observation_document(state), document)
        self.assertIs(observation_screenshot(state), source)
        self.assertIn("Screenshot: desktop 4096x1278", document.text)
        self.assertEqual(facts["windows"][0]["rect"]["x"], 814)
        self.assertEqual(memoryview(source).tobytes(), original_png)

    async def test_desktop_caption_example_reaches_global_input_with_image_scaling(self):
        connection = PrimitiveConnection()
        with execution_scope(self.runtime.executor):
            controller = session_controller(ConnectionPort(connection, HostPolicy()))
            computer = computer_for_controller(controller)
        self.store = controller.desktop
        self.runtime.task_namespace["computer"] = computer
        self.capture()
        result = await self.execute("obs = read()")
        image = next(block for block in result["content"] if block["type"] == "image")
        caption = image["_meta"]["computer-use"]["caption"]
        self.assertIn("Coordinate space: displayed image pixels.", caption)
        self.assertIn("Image point: p = computer.point((x, y))", caption)
        self.assertIn("Global click: computer.click(target=p)", caption)
        self.assertNotIn("coordinate target=Computer", caption)
        # Follow the code actually delivered beside the PNG. The native stub
        # maps capture pixels to a display with origin (-300, 40).
        point_example = next(
            line.removeprefix("Image point: ") for line in caption.splitlines() if line.startswith("Image point: ")
        )
        click_example = next(
            line.removeprefix("Global click: ") for line in caption.splitlines() if line.startswith("Global click: ")
        )
        for point in ((1137, 323), (100, 50)):
            connection.requests.clear()
            result = await self.execute(point_example.replace("(x, y)", repr(point)) + "\n" + click_example)
            self.assertFalse(result.get("isError"), result)
            self.assertEqual(
                [method for method, _ in connection.requests], ["locatePoint", "begin", "execute", "finish"]
            )
            self.assertEqual(connection.requests[0][1]["point"], {"x": point[0] * 2, "y": point[1] * 2})
            parameters = connection.requests[1][1]["intent"]["parameters"]
            self.assertEqual((parameters["x"], parameters["y"]), (point[0] * 2 - 300, point[1] * 2 + 40))
        # Device-level input stays independent of this earlier image.
        connection.requests.clear()
        result = await self.execute("computer.click(at=(-200, -80))")
        self.assertFalse(result.get("isError"), result)
        self.assertEqual([method for method, _ in connection.requests], ["begin", "execute", "finish"])
        self.assertEqual(connection.requests[0][1]["intent"]["parameters"]["x"], -200)

    async def test_ordinary_string_operations_keep_the_actual_delivered_pixel_space(self):
        for expression in (
            "obs.text.splitlines()",
            "'\\n'.join(obs.text.splitlines())",
            "f'{obs.text}'",
            "str(obs.text)",
        ):
            with self.subTest(expression=expression):
                self.deliveries.clear()
                self.capture()
                result = await self.execute(f"obs = read()\nprint({expression})")
                self.assertFalse(result.get("isError"), result)
                images = [base64.b64decode(block["data"]) for block in result["content"] if block["type"] == "image"]
                self.assertEqual([png_size(png) for png in images], [{"width": 2048, "height": 639}])
                text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
                self.assertIn("(407, 75, 400, 300)", text)
                self.assertNotIn("(814, 150, 800, 600)", text)
                self.assertNotIn("4096x1278", text)
                self.assertEqual(self.store.map_parameters("click", {"x": 515, "y": 125}), {"x": 1030, "y": 250})

    async def test_default_desktop_details_share_the_public_image_pixel_space(self):
        _, document, state = self.capture()
        result = await self.execute("obs = read()\nprint(obs.read_details().text)")
        self.assertFalse(result.get("isError"), result)
        text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        self.assertIn("Captured details (same observation)", text)
        self.assertIn("2048x639", text)
        self.assertNotIn("4096x1278", text)
        self.assertIn("(407, 75, 400, 300)", state.read_details().text)
        self.assertIn("4096x1278", document.text)

    async def test_explicit_native_details_keep_their_screen_space(self):
        details = CapturedTextDocument("AX bounds in screen points: (814, 150, 800, 600)")
        _, _, state = self.capture(details_document=details)
        result = await self.execute("obs = read()\nprint(obs.read_details().text)")
        self.assertFalse(result.get("isError"), result)
        text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        self.assertIn(details.text, text)
        self.assertIn("2048x639", state.text)
        self.assertIn(details.text, state.read_details().text)

    async def test_explicit_observation_and_full_text_prints_use_delivery_geometry(self):
        for print_code in ("print(obs)", "print(obs.text)", "print(obs.text[:20])", "print(obs.text[-20:])"):
            with self.subTest(print_code=print_code):
                self.deliveries.clear()
                _, _, state = self.capture()
                result = await self.execute(f"obs = read()\n{print_code}\nassert_unseen()")
                self.assert_delivery(result)
                self.assertEqual(self.deliveries, [state])

    async def test_actual_rounded_png_dimensions_scale_each_axis_independently(self):
        _, document, state = self.capture((2301, 933))
        result = await self.execute("obs = read()")
        self.assert_delivery(result, (2048, 830))
        delivery = prepare_image(observation_screenshot(state))
        projected = project_document(document, delivery.coordinates)
        rect = projected.parts[0].values[0]["windows"][0]["rect"]
        self.assertAlmostEqual(rect["x"], 814 * 2048 / 2301)
        self.assertAlmostEqual(rect["y"], 150 * 830 / 933)
        self.assertNotEqual(2048 / 2301, 830 / 933)
        actual = self.store.map_parameters("click", dict(x=rect["x"], y=rect["y"]))
        self.assertAlmostEqual(actual["x"], 814)
        self.assertAlmostEqual(actual["y"], 150)
        self.assertEqual(document.parts[0].values[0]["canvas"]["width"], 2301)

    async def test_text_only_read_has_no_projection_or_image_authority(self):
        _, _, state = self.capture(screenshot=False)
        result = await self.execute("obs = read()\nprint(obs.text)")
        self.assertFalse(result.get("isError"), result)
        self.assertEqual([block["type"] for block in result["content"]], ["text"])
        self.assertIn("Screenshot: unavailable", result["content"][0]["text"])
        self.assertIn('"Target document" (814, 150, 800, 600)', result["content"][0]["text"])
        self.assertEqual(self.deliveries, [state])
        with self.assertRaises(ComputerUseError):
            self.store.target(True)

    async def test_crop_and_delivery_epoch_cannot_authorize_an_unseen_source(self):
        _, _, state = self.capture()
        await self.execute("obs = read()")
        with Image.open(BytesIO(state.screenshot)) as image, image.crop((800, 150, 1220, 800)) as crop:
            buffer = BytesIO()
            crop.save(buffer, format="PNG")
        self.runtime.task_namespace["crop"] = buffer.getvalue()
        result = await self.execute("print_image(crop)", reset_images=True)
        text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        self.assertIn("source unknown", text)
        self.assertNotIn("Image point", text)
        with self.assertRaises(ComputerUseError):
            self.store.map_parameters("click", dict(x=515, y=125))
        # Printing the known source also does not grant authority inside that call.
        result = await self.execute("print_image(obs.screenshot)\nmap_point(515, 125)")
        self.assertTrue(result.get("isError"), result)
        self.assertIn("not been returned", result["content"][-1]["text"])
        self.assertEqual(self.store.map_parameters("click", dict(x=515, y=125)), dict(x=1030, y=250))

    async def test_saved_public_text_and_png_describe_the_same_presentation(self):
        _, document, state = self.capture()
        file = StringIO()
        png_file = BytesIO()
        self.runtime.task_namespace.update(file=file, png_file=png_file)
        result = await self.execute("obs = read()\nprint(obs.text, file=file)\npng_file.write(obs.screenshot)")
        self.assert_delivery(result)
        self.assertIn("2048x639", file.getvalue())
        self.assertIn("2048x639", state.text)
        self.assertEqual(png_size(png_file.getvalue()), {"width": 2048, "height": 639})
        self.assertIn("4096x1278", document.text)
        self.assertEqual(png_size(observation_screenshot(state)), {"width": 4096, "height": 1278})

    async def test_retained_presentation_keeps_its_own_source_after_a_different_resize(self):
        _, _, first = self.capture()
        first_source = observation_screenshot(first)
        await self.execute("old = read()")
        first_png, first_text = first.screenshot, str(first.text)
        first_delivery = self.runtime.presentation_images.prepared(first_png)[1]
        _, _, second = self.capture()
        with patch("computer_use_mcp.runtime.prepare_image", side_effect=lambda png: prepare_image(png, max_dimension=1024)):
            await self.execute("new = read()")
        self.assertEqual(png_size(second.screenshot), {"width": 1024, "height": 319})
        self.assertIsNot(first_source, observation_screenshot(second))
        self.assertIs(first.screenshot, first_png)
        self.assertEqual(str(first.text), first_text)
        # Both exact public PNG and its explicit bytes conversion re-emit the
        # original pair; neither aliases the later observation or resizes again.
        with patch("computer_use_mcp.runtime.prepare_image", side_effect=AssertionError("unexpected re-encoding")):
            result = await self.execute("print(f'{old.text}')\nprint_image(bytes(old.screenshot))\nprint_image(old.screenshot)")
        text = self.assert_delivery(result)
        self.assertNotIn("1024x319", text)
        self.assertIs(self.runtime.presentation_images.prepared(bytes(first_png))[1], first_delivery)
        self.assertIs(first_delivery.source, first_source)

    async def test_equal_pixels_from_an_unrelated_buffer_do_not_inherit_presentation_authority(self):
        _, _, state = self.capture()
        await self.execute("obs = read()")
        for copy in (PNG(memoryview(state.screenshot).tobytes()), memoryview(state.screenshot).tobytes()):
            self.runtime.task_namespace["copy"] = copy
            result = await self.execute("print_image(copy)", reset_images=True)
            self.assertFalse(result.get("isError"), result)
            text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
            self.assertIn("source unknown", text)
            with self.assertRaisesRegex(ComputerUseError, "not been returned"):
                self.store.map_parameters("click", {"x": 515, "y": 125})
        await self.execute("print_image(bytes(obs.screenshot))")
        self.assertEqual(self.store.map_parameters("click", {"x": 515, "y": 125}), {"x": 1030, "y": 250})

    async def test_mutated_presentation_cannot_reuse_original_capture_authority(self):
        self.capture()
        self.runtime.task_namespace["replacement"] = encoded_image((100, 80))
        await self.execute("obs = read()\nsaved = bytes(obs.screenshot)")
        result = await self.execute("obs.screenshot[:] = replacement\nprint_image(obs.screenshot)", reset_images=True)
        self.assertFalse(result.get("isError"), result)
        images = [block for block in result["content"] if block["type"] == "image"]
        self.assertEqual([png_size(base64.b64decode(block["data"])) for block in images], [{"width": 100, "height": 80}])
        self.assertIn("source unknown", images[0]["_meta"]["computer-use"]["caption"])
        with self.assertRaisesRegex(ComputerUseError, "not been returned"):
            self.store.map_parameters("click", {"x": 515, "y": 125})
        # The earlier immutable bytes still denote the exact original view.
        await self.execute("print_image(saved)")
        self.assertEqual(self.store.map_parameters("click", {"x": 515, "y": 125}), {"x": 1030, "y": 250})

    async def test_explicit_image_before_text_keeps_one_matching_delivery(self):
        self.capture()
        result = await self.execute("obs = read()\nprint_image(obs.screenshot)\nprint(obs.text)")
        self.assert_delivery(result)
        # Caption/observation now precede the image, rather than printing an
        # explicit image first and an automatic copy after its text.
        self.assertEqual([block["type"] for block in result["content"]], ["text", "text", "image"])

    async def test_only_the_latest_buffered_capture_commits_its_observation_context(self):
        _, _, first = self.capture()
        self.runtime.task_namespace["first_read"] = self.runtime.task_namespace["read"]
        _, _, second = self.capture()
        result = await self.execute("old = first_read()\nobs = read()\nassert_unseen()")
        self.assert_delivery(result)
        self.assertEqual(len(self.deliveries), 1)
        self.assertIs(self.deliveries[0], second)
        self.assertIsNot(self.deliveries[0], first)

    async def test_mutating_png_bytes_cannot_reuse_a_prepared_output(self):
        self.runtime.task_namespace.update(png=encoded_image((4096, 1278)), replacement=encoded_image((100, 80)))
        result = await self.execute("print_image(png)\npng[:] = replacement\nprint_image(png)")
        self.assertFalse(result.get("isError"), result)
        sizes = [png_size(base64.b64decode(block["data"])) for block in result["content"] if block["type"] == "image"]
        self.assertEqual(sizes, [dict(width=2048, height=639), dict(width=100, height=80)])

    async def test_a_mutated_source_cannot_acknowledge_its_unprinted_replacement(self):
        original, replacement = encoded_image((20, 20)), encoded_image((30, 30))
        deliveries = []

        def register_replacement():
            register_image(original, dict(kind="window", selector="fixture", delivered=lambda: deliveries.append(True)))

        register_image(original, dict(kind="window", selector="fixture"))
        self.runtime.task_namespace.update(
            png=original,
            replacement=replacement,
            register_replacement=lambda: self.runtime.executor.call(register_replacement),
        )
        result = await self.execute("print_image(png)\npng[:] = replacement\nregister_replacement()")
        self.assertFalse(result.get("isError"), result)
        images = [block for block in result["content"] if block["type"] == "image"]
        self.assertEqual([png_size(base64.b64decode(block["data"])) for block in images], [dict(width=20, height=20)])
        self.assertEqual(deliveries, [])
        self.assertIn("source unknown", images[0]["_meta"]["computer-use"]["caption"])

    async def test_explicit_desktop_full_keeps_its_captured_view_before_a_later_default_read(self):
        _, _, first = self.capture(full=True)
        self.runtime.task_namespace["first_read"] = self.runtime.task_namespace["read"]
        _, _, second = self.capture((100, 80))
        result = await self.execute("old = first_read()\nobs = read()\nassert_unseen()")
        self.assertFalse(result.get("isError"), result)
        images = [block for block in result["content"] if block["type"] == "image"]
        self.assertEqual(
            [png_size(base64.b64decode(block["data"])) for block in images],
            [dict(width=2048, height=639), dict(width=100, height=80)],
        )
        self.assertEqual(self.deliveries, [first, second])

    async def test_explicit_desktop_print_keeps_its_captured_view_before_a_later_default_read(self):
        _, _, first = self.capture()
        self.runtime.task_namespace["first_read"] = self.runtime.task_namespace["read"]
        _, _, second = self.capture((100, 80))
        result = await self.execute("old = first_read()\nprint(old)\nobs = read()\nassert_unseen()")
        self.assertFalse(result.get("isError"), result)
        self.assertEqual(sum(block["type"] == "image" for block in result["content"]), 2)
        self.assertEqual(self.deliveries, [first, second])


class DeliveryProjectionTests(unittest.TestCase):
    def test_projection_preserves_nested_structure_and_non_image_geometry(self):
        _, desktop = desktop_document((4096, 1278))
        source = DisplayDocument.join(["Window screen bounds: (814, 150, 800, 600)", desktop.indented()])
        projected = project_document(source, prepare_image(encoded_image((4096, 1278))).coordinates)
        self.assertIn("Window screen bounds: (814, 150, 800, 600)", projected.text)
        self.assertIn("Screenshot: desktop 2048x639", projected.text)
        self.assertIn("Screenshot: desktop 4096x1278", source.text)

    def test_projection_rejects_a_different_source_canvas(self):
        _, document = desktop_document((4096, 1278))
        coordinates = prepare_image(encoded_image((2301, 933))).coordinates
        with self.assertRaisesRegex(ValueError, "does not belong"):
            project_document(document, coordinates)
