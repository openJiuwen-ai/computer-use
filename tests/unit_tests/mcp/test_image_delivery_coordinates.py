# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""MCP output geometry and subsequent input share the actual delivered PNG space."""

import base64
import unittest
from io import BytesIO, StringIO

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
from computer_use.protocol.png import PNG, png_size
from computer_use.session import images as images_state
from computer_use.session.desktop_store import DesktopImageAccess, DesktopStore
from computer_use.session.events import observation_completed, register_observation_delivery
from computer_use.session.observation import lazy_observation
from computer_use_mcp.image_delivery import prepare_image, project_document
from computer_use_mcp.runtime import Runtime
from PIL import Image


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
            map_point=lambda x, y: self.store.map_parameters("click", dict(x=x, y=y)),
            assert_unseen=self.assert_unseen,
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

    def capture(self, size=(4096, 1278), *, screenshot=True):
        facts, document = desktop_document(size, captured=screenshot)
        png = encoded_image(size) if screenshot else None
        state = lazy_observation(document, png)

        def publish():
            self.store.record(dict(desktopRef="desktop:fixture", observation=facts, screenshot=png))
            register_observation_delivery(state, lambda: self.deliveries.append(state))
            observation_completed(
                dict(
                    source=self.source,
                    scope="desktop",
                    view="desktop",
                    state=state,
                    document=document,
                    refs=[],
                    full=True,
                )
            )
            return state

        self.runtime.task_namespace["read"] = publish
        return facts, document, state

    def assert_unseen(self):
        self.assertEqual(self.deliveries, [])
        with self.assertRaises(ComputerUseError) as failure:
            self.store.target(True)
        self.assertIn("not been returned", str(failure.exception))

    async def execute(self, code, *, reset_images=False):
        return await self.runtime.execute(
            dict(
                code=code,
                executionId=len(self.events) + 1,
                history=dict(resetText=False, resetImages=reset_images, incremental=True),
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
        self.assertIn("computer.window(7); application: computer.app(2)", text)
        self.assertNotIn("4096x1278", text)
        self.assertEqual(text.count("Desktop: full view"), 1)
        return text

    async def test_automatic_delivery_geometry_maps_next_input_and_preserves_sdk_capture(self):
        facts, document, state = self.capture()
        original_png = memoryview(state.screenshot).tobytes()
        result = await self.execute("obs = read()\nassert_unseen()")
        text = self.assert_delivery(result)
        self.assertIn('"Display" (0, 0, 2048, 639)', text)
        self.assertIn('"Target document" (407, 75, 400, 300)', text)
        self.assertEqual(self.deliveries, [state])
        # A button visually at (515, 125) returns to the source target (1030, 250),
        # rather than the failing log's doubled (2060, 500).
        self.assertEqual(self.store.map_parameters("click", dict(x=515, y=125)), dict(x=1030, y=250))
        self.store.check_coordinates(515, 125)
        self.assertIn("Screenshot: desktop 4096x1278", state.text)
        self.assertEqual(state.text, document.text)
        self.assertEqual(facts["windows"][0]["rect"]["x"], 814)
        self.assertEqual(memoryview(state.screenshot).tobytes(), original_png)

    async def test_explicit_observation_and_full_text_prints_use_delivery_geometry(self):
        for print_code in ("print(obs)", "print(obs.text)"):
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
        delivery = prepare_image(state.screenshot)
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
        self.assertNotIn("Use this displayed image's pixels", text)
        with self.assertRaises(ComputerUseError):
            self.store.map_parameters("click", dict(x=515, y=125))
        # Printing the known source also does not grant authority inside that call.
        result = await self.execute("print_image(obs.screenshot)\nmap_point(515, 125)")
        self.assertTrue(result.get("isError"), result)
        self.assertIn("not been returned", result["content"][-1]["text"])
        self.assertEqual(self.store.map_parameters("click", dict(x=515, y=125)), dict(x=1030, y=250))

    async def test_source_file_output_remains_raw_while_model_output_is_projected(self):
        _, _, state = self.capture()
        # A text file sink is not a model delivery boundary.
        file = StringIO()
        self.runtime.task_namespace["file"] = file
        result = await self.execute("obs = read()\nprint(obs.text, file=file)")
        self.assert_delivery(result)
        self.assertIn("4096x1278", file.getvalue())
        self.assertIn("4096x1278", state.text)

    async def test_explicit_image_before_text_keeps_one_matching_delivery(self):
        self.capture()
        result = await self.execute("obs = read()\nprint_image(obs.screenshot)\nprint(obs.text)")
        self.assert_delivery(result)
        self.assertEqual([block["type"] for block in result["content"]], ["text", "image", "text"])

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
