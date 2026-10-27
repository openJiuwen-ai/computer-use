"""Window context is useful without being a new coordinate or input binding."""

import asyncio
import base64
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from repository import ROOT as REPO_ROOT
from v5_handles import nativeComputer  # Frozen facade: private pipeline behavior baseline.

from computer_use.client import images
from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.images.capture import WindowImage
from computer_use.interaction.read.acquisition.images.overview import WindowPreview, compose_overview
from computer_use.native.connection import Connection
from computer_use.native.contracts import Binding, HostPolicy
from computer_use.native.transport import ConnectionPort
from computer_use.protocol.captures import captureMetadata, decodeCapture
from computer_use.session import images as images_state
from computer_use.session.observation_data import observationData

ROOT = REPO_ROOT


def preview(ref, color="red", size=(200, 100)):
    data = BytesIO()
    with Image.new("RGB", size, color) as image:
        image.save(data, format="PNG")
    png = decodeCapture(
        dict(
            id="image-" + ref,
            kind="window",
            scope="surface-" + ref,
            width=size[0],
            height=size[1],
            startedAt=1,
            completedAt=2,
            png=base64.b64encode(data.getvalue()).decode(),
        ),
        "window",
    )
    target = Binding("session", "instance", "window-" + ref, "surface-" + ref, capture="image-" + ref)
    return WindowPreview(
        ref,
        target.root_window,
        "窗口 " + ref,
        WindowImage(target, png, dict(id="observation-" + ref, startedAt=0, completedAt=3), {}),
    )


class OverviewCompositionTests(unittest.TestCase):
    def test_overview_captures_structured_labels_until_explicit_text_access(self):
        from computer_use.interaction.read.presentation.content import DisplayText
        from computer_use.interaction.read.presentation.rendering import document

        original = preview("0")
        previews, omitted = [original], ["4", "5"]
        with patch.object(document, "render_document", wraps=document.render_document) as renderer:
            overview = compose_overview(previews, omitted, scope="app")
            self.assertEqual(renderer.call_count, 0)
            self.assertNotIn("text", overview.__dict__)
            self.assertTrue(all(isinstance(part, DisplayText) for part in overview.document.parts))
            before = overview.document.to_data()
            previews.clear()
            omitted[0] = "Later unrelated alias"
            self.assertEqual(overview.document.to_data(), before)
            body = overview.text
            self.assertEqual(renderer.call_count, 1)
            self.assertIn('Window 0: "窗口 0"', body)
            self.assertIn("Not pictured (overview budget): 4, 5", body)
            self.assertEqual(overview.text, body)
            self.assertEqual(renderer.call_count, 1)

    def test_uncropped_tiles_keep_colors_sources_and_independent_bindings(self):
        first, second = preview("0"), preview("1", "blue", (100, 200))
        before = bytes(first.image.image)
        result = compose_overview([first, second], [], scope="app", max_dimension=800)
        self.assertEqual(captureMetadata(result.image)["kind"], "overview")
        self.assertEqual([s["capture"] for s in result.sources], ["image-0", "image-1"])
        self.assertEqual(bytes(first.image.image), before)
        with Image.open(BytesIO(result.image)) as pixels:
            self.assertEqual(pixels.size, (800, 300))
            self.assertEqual(pixels.getpixel((200, 160)), (255, 0, 0))
            self.assertEqual(pixels.getpixel((600, 160)), (0, 0, 255))
            self.assertEqual(pixels.getpixel((410, 160)), (37, 44, 54), "tall window is contained, not stretched")
        self.assertIn("not desktop layout or click coordinates", result.text)
        self.assertIn("窗口 0", result.text)
        with self.assertRaises(TypeError):
            result.sources[0]["capture"] = "replacement"

    def test_missing_omitted_and_all_unavailable_remain_explicit(self):
        missing = WindowPreview("1", "window-1", "", None, "permission denied")
        result = compose_overview([preview("0"), missing], ["2", "3"], scope="app")
        self.assertIsNotNone(result.image)
        self.assertIn("permission denied", result.text)
        self.assertIn("Not pictured (overview budget): 2, 3", result.text)
        empty = compose_overview([missing], [], scope="app")
        self.assertIsNone(empty.image)
        self.assertEqual(empty.sources[0]["error"], "permission denied")

    def test_capture_identity_cannot_be_inferred_from_equal_pixels(self):
        source = preview("0")
        bad = replace(source, image=replace(source.image, target=replace(source.image.target, capture="another-image")))
        with self.assertRaises(ValueError):
            compose_overview([bad], [], scope="app")
        self.assertNotEqual(
            captureMetadata(compose_overview([source], [], scope="app").image)["id"],
            captureMetadata(compose_overview([source], [], scope="app").image)["id"],
        )

    def test_image_decoder_failure_is_a_local_preview_gap(self):
        with patch(
            "computer_use.interaction.read.acquisition.images.overview.Image.open",
            side_effect=OSError("decoder refused"),
        ):
            result = compose_overview([preview("0")], [], scope="app")
        self.assertIsNone(result.image)
        self.assertIn("decoder refused", result.text)


class OverviewIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="window-overview-")
        self.journal = Path(self.temp.name) / "wire.jsonl"
        self.connections = []
        self.delivery_required = images_state._deliveryRequired

    async def asyncTearDown(self):
        images_state._deliveryRequired = self.delivery_required
        for connection in self.connections:
            connection.close()
            await connection.waitClosed()
        self.temp.cleanup()

    def computer(self, mode):
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
        return nativeComputer(ConnectionPort(connection, HostPolicy()))

    def requests(self):
        if not self.journal.exists():
            return []
        return [v for line in self.journal.read_text().splitlines() if "method" in (v := json.loads(line))]

    async def test_window_choices_keep_overview_and_image_caption_structured_until_output(self):
        from computer_use.interaction.read.presentation.rendering import document
        from computer_use.session.observation import peekObservationText

        app = self.computer("observation-sdk-multiple").app("Native fixture")
        with patch.object(document, "render_document", wraps=document.render_document) as renderer:
            observed = await app.read()
            self.assertEqual(renderer.call_count, 0)
            self.assertIsNone(peekObservationText(observed))
            self.assertIsNotNone(observed.overview)
            self.assertEqual(renderer.call_count, 0)
            self.assertIn("Window 0:", observed.text)
            self.assertEqual(renderer.call_count, 1)
            self.assertIn("Window 0:", images.imageLabel(observed.overview))
            self.assertEqual(renderer.call_count, 2)

    async def test_window_image_context_is_captured_before_terminal_caption_formatting(self):
        from computer_use.interaction.read.presentation import content
        from computer_use.interaction.read.presentation.content import DisplayText

        app = self.computer("observation-sdk").app("Native fixture")
        with patch.object(content, "render_text", wraps=content.render_text) as renderer:
            observed = await app.read()
            self.assertEqual(renderer.call_count, 0)
            source = images_state._source(observed.screenshot)["source"]
            self.assertIsInstance(source["context"], DisplayText)
            caption = images.imageLabel(observed.screenshot)
            self.assertIn('Window: "Native fixture", App: "Native fixture".', caption)
            self.assertGreater(renderer.call_count, 0)

    async def test_unbound_window_choices_have_pictures_without_binding_or_input(self):
        computer = self.computer("observation-sdk-multiple")
        app = computer.app("Native fixture")
        observed = await app.read()
        self.assertIsNone(observed.screenshot)
        self.assertIsNotNone(observed.overview)
        self.assertIsNone(computer._controller.state(app).selection)
        self.assertIn('Window 0: "Native fixture"', observed.text)
        self.assertIn('Window 1: "Second document"', observed.text)
        self.assertEqual(
            [r["params"]["target"]["surface"] for r in self.requests() if r["method"] == "capture"],
            ["surface", "second-surface"],
        )
        self.assertTrue(all(r["params"]["mode"] == "read" for r in self.requests() if r["method"] == "begin"))
        self.assertNotIn("execute", [r["method"] for r in self.requests()])

    async def test_overview_delivery_never_substitutes_for_the_selected_window_image(self):
        images_state._deliveryRequired = True
        computer = self.computer("observation-sdk-multiple")
        app = computer.app("Native fixture")
        first = await app.read()
        images.imageDelivered(first.overview)
        with self.assertRaises(ComputerUseError):
            await app.click(0, 0)
        selected = app.window("1")
        observed = await selected.read()
        captures = [r for r in self.requests() if r["method"] == "capture"]
        self.assertEqual(
            [r["params"]["target"]["surface"] for r in captures],
            ["surface", "second-surface", "second-surface", "surface"],
        )
        self.assertIn("context only", images.imageLabel(observed.overview))
        images.imageDelivered(observed.overview)
        with self.assertRaises(ComputerUseError) as caught:
            await selected.click(0, 0)
        self.assertEqual(caught.exception.code, "input_unavailable")
        images.imageDelivered(observed.screenshot)
        await selected.click(0, 0)
        writes = [r for r in self.requests() if r["method"] == "begin" and r["params"]["mode"] == "write"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(
            (writes[0]["params"]["target"]["surface"], writes[0]["params"]["target"]["capture"]),
            ("second-surface", captureMetadata(observed.screenshot)["id"]),
        )

    async def test_visual_window_choices_need_no_accessibility_root_or_known_parent(self):
        app = self.computer("observation-sdk-overview-no-ax").app("Native fixture")
        observed = await app.read()
        self.assertIsNotNone(observed.overview)
        self.assertIn("parent unknown", observed.text)
        self.assertFalse(any(r["params"].get("query", {}).get("kind") == "nodes" for r in self.requests()))

    async def test_ambiguous_modal_views_preserve_the_bound_root_and_offer_each_window(self):
        computer = self.computer("observation-multiple-modal")
        app = computer.app("Native fixture")
        await app.read()
        original = computer._controller.state(app).selection
        observed = await app.read()
        self.assertEqual(computer._controller.state(app).selection, original)
        self.assertIsNone(observed.screenshot)
        self.assertIsNotNone(observed.overview)
        self.assertIn("fixed working window has no unique current view", observed.text)
        self.assertIn("Other dialog", observed.text)
        self.assertIn("(root)", observed.text)
        self.assertEqual(observationData(observed)["instance"], original.root.root_window)
        self.assertFalse(any(r["method"] == "execute" for r in self.requests()))

    async def test_one_capture_failure_keeps_other_tiles_and_window_choices(self):
        observed = await self.computer("observation-sdk-overview-image-error").app("Native fixture").read()
        self.assertIsNotNone(observed.overview)
        self.assertIn("Second window capture unavailable", observed.text)
        self.assertIn("Second document", observed.text)

    async def test_many_windows_have_bounded_images_and_an_explicit_omission_list(self):
        observed = await self.computer("observation-sdk-overview-many").app("Native fixture").read()
        self.assertEqual(sum(r["method"] == "capture" for r in self.requests()), 6)
        self.assertIn("Not pictured (overview budget)", observed.text)
        self.assertIn("Window 8", observed.text)
        self.assertIsNotNone(observed.overview)

    async def test_cancellation_during_optional_captures_still_cleans_up_and_publishes_nothing(self):
        computer = self.computer("observation-sdk-overview-wait")
        app = computer.app("Native fixture")
        task = asyncio.create_task(app.read())
        for _ in range(100):
            if any(r["method"] == "capture" for r in self.requests()):
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(computer._controller.state(app).observation)
        self.assertEqual(sum(r["method"] == "capture" for r in self.requests()), 1)
        self.assertFalse(any(r["method"] == "execute" for r in self.requests()))
