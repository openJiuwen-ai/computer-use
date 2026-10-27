"""Nonfatal pixel acquisition limits stay with the image, never its authority."""

import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from legacy_results import decodeResult
from repository import ROOT as REPO_ROOT
from sdk.interaction.read.acquisition.images.test_window_overview import preview
from sdk.parity.test_protocol_parity import CAPTURE, STAMP
from v5_handles import nativeComputer  # Frozen facade: private pipeline behavior baseline.

from computer_use.client import images
from computer_use.interaction.read.acquisition.images.overview import compose_overview
from computer_use.native.connection import Connection
from computer_use.native.contracts import HostPolicy
from computer_use.native.transport import ConnectionPort
from computer_use.protocol.captures import captureMetadata, decodeCapture
from computer_use.protocol.png import PNG
from computer_use.session import images as images_state

ROOT = REPO_ROOT
NOTE = "Initial client repaint is unconfirmed within the capture budget."


class CaptureNoteContractsTests(unittest.TestCase):
    def test_optional_notes_are_bounded_nonblank_and_immutable_without_changing_png(self):
        old = decodeCapture(CAPTURE, "window", "scope", STAMP)
        notes = [NOTE]
        png = decodeCapture({**CAPTURE, "stopReasons": notes}, "window", "scope", STAMP)
        self.assertEqual(png, old)
        self.assertNotIn("stopReasons", captureMetadata(old))
        self.assertEqual(captureMetadata(png)["stopReasons"], (NOTE,))
        notes.append("mutated caller state")
        self.assertEqual(captureMetadata(png)["stopReasons"], (NOTE,))
        with self.assertRaises(TypeError):
            captureMetadata(png)["stopReasons"][0] = "replace"
        self.assertIsNone(captureMetadata(PNG(png)), "Notes and coordinate authority are object-local")
        for value in [None, "reason", [False], [""], [" \n"], ["😀" * 2049], ["reason"] * 17]:
            with self.subTest(value=str(value)[:40]), self.assertRaises(ValueError):
                decodeCapture({**CAPTURE, "stopReasons": value}, "window", "scope", STAMP)
        valid = decodeCapture({**CAPTURE, "stopReasons": ["😀" * 2048] * 16}, "window", "scope", STAMP)
        self.assertEqual(len(captureMetadata(valid)["stopReasons"]), 16)
        with self.assertRaises(ValueError):
            decodeCapture({**CAPTURE, "stopReasons": [NOTE], "ready": True}, "window", "scope", STAMP)

    def test_legacy_desktop_default_text_keeps_pixel_notes_separate_from_catalogue(self):
        value = dict(
            desktopRef="image",
            scope="desktop",
            observed=STAMP,
            screenshot={**CAPTURE, "kind": "desktop", "scope": "desktop", "stopReasons": [NOTE]},
            observation=dict(
                mode="shared",
                canvas=dict(width=1, height=1),
                frontmost=None,
                displays=[dict(name="Screen", rect=dict(x=0, y=0, width=1, height=1), captured=True)],
                windows=[],
                omittedWindows=0,
            ),
        )
        result = decodeResult("readDesktop", value)
        self.assertIn('Image note: "' + NOTE + '"', result["text"])
        self.assertIsNotNone(result["screenshot"])
        self.assertNotIn("catalogError", result["observation"])
        self.assertEqual(result["desktopRef"], "image")

    def test_standalone_image_caption_keeps_notes_even_when_context_was_shown(self):
        for kind in ("window", "desktop", "overview"):
            with self.subTest(kind=kind):
                png = decodeCapture({**CAPTURE, "kind": kind, "stopReasons": [NOTE]}, kind)
                images.registerImage(
                    png, dict(kind=kind, selector="Fixture", context="Window: Fixture.", current=lambda: True)
                )
                for shown in (False, True):
                    caption = images.imageLabel(png, shown)
                    self.assertIn('Image note: "' + NOTE + '"', caption)
                    self.assertNotIn("image is stale", caption)
                self.assertNotIn(NOTE, images.imageLabel(PNG(png)))

    def test_overview_retains_every_tile_note_and_bounded_composed_image_summary(self):
        previews = []
        for i in range(6):
            original = preview(str(i))
            png = original.image.image
            import base64

            data = {
                **captureMetadata(png),
                "png": base64.b64encode(png).decode(),
                "stopReasons": [NOTE + " " + str(j) for j in range(16)],
            }
            changed = decodeCapture(data, "window")
            previews.append(replace(original, image=replace(original.image, image=changed)))
        overview = compose_overview(previews, [], scope="app")
        self.assertIsNotNone(overview.image)
        for source in overview.sources:
            self.assertEqual(len(source["stopReasons"]), 16)
            self.assertIsNone(source["error"], "Incomplete rendering is not missing PNG")
        self.assertEqual(overview.text.count("Image note:"), 96)
        notes = captureMetadata(overview.image)["stopReasons"]
        self.assertEqual(len(notes), 1)
        self.assertIn("0, 1, 2, 3, 4, 5", notes[0])
        images.registerImage(overview.image, dict(kind="overview", context=overview.text))
        self.assertIn(notes[0], images.imageLabel(overview.image))
        self.assertIn(NOTE + " 15", images.imageLabel(overview.image))
        self.assertNotIn("Preview unavailable", overview.text)


class CaptureNoteSDKTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="capture-notes-")
        self.journal = Path(self.temp.name) / "wire.jsonl"
        self.connections = []
        self.env = patch.dict(os.environ, {"COMPUTER_USE_DESKTOP": "shared"})
        self.env.start()
        self.delivery = images_state._deliveryRequired
        images_state._deliveryRequired = False

    async def asyncTearDown(self):
        images_state._deliveryRequired = self.delivery
        for connection in self.connections:
            connection.close()
            await connection.waitClosed()
        self.env.stop()
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
        return [r for line in self.journal.read_text().splitlines() if "method" in (r := json.loads(line))]

    async def test_app_keeps_pixels_scope_and_coordinates_while_reporting_image_gap(self):
        computer = self.computer("observation-sdk-capture-notes")
        app = computer.app("Native fixture")
        result = await app.read()
        observed = computer._controller.state(app).observation
        self.assertIsNotNone(result.screenshot)
        self.assertTrue(observed.scope_confirmed)
        self.assertEqual(observed.image.target.capture, captureMetadata(result.screenshot)["id"])
        self.assertEqual(
            [(g.component, g.code, g.detail) for g in observed.gaps], [("image", "observation_incomplete", NOTE)]
        )
        self.assertIn(NOTE, result.text)
        self.assertIn(NOTE, images.imageLabel(result.screenshot, True))
        self.assertNotIn("image is stale", images.imageLabel(result.screenshot))
        before = len(self.requests())
        await app.click(0, 0)
        later = self.requests()[before:]
        self.assertEqual(sum(r["method"] == "execute" for r in later), 1)
        self.assertFalse(any(r["method"] == "capture" for r in later))
        self.assertTrue(all(r["params"].get("query", {}).get("kind") != "nodes" for r in later))
        old = result.screenshot
        await app.read()
        self.assertEqual(captureMetadata(old)["stopReasons"], (NOTE,))
        self.assertIn(NOTE, images.imageLabel(old))
        self.assertIn(
            "image is stale", images.imageLabel(old), "A later read, not the acquisition note, expires presentation"
        )

    async def test_unbound_overview_carries_notes_for_each_independent_window(self):
        result = await self.computer("observation-sdk-overview-capture-notes").app("Native fixture").read()
        self.assertIsNone(result.screenshot)
        self.assertIsNotNone(result.overview)
        self.assertEqual(result.text.count(NOTE), 2)
        self.assertIn("0, 1", captureMetadata(result.overview)["stopReasons"][0])
        self.assertIn(NOTE, images.imageLabel(result.overview))

    async def test_desktop_retains_png_and_reports_notes_without_changing_catalogue(self):
        computer = self.computer("desktop-capture-notes")
        with patch(
            "computer_use.interaction.read.presentation.rendering.document.render_document",
            side_effect=AssertionError("desktop read rendered text before delivery"),
        ):
            result = await computer.read()
        self.assertIsNotNone(result.screenshot)
        self.assertIn(NOTE, result.text)
        self.assertIn(NOTE, images.imageLabel(result.screenshot))
        self.assertIn("Document", result.text)
        self.assertNotIn("catalogError", computer._controller.desktop._view["observation"])
        self.assertEqual([r["method"] for r in self.requests()], ["hello", "begin", "capture", "finish"])


if __name__ == "__main__":
    unittest.main()
