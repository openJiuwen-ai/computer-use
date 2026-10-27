"""Window visibility samples are guidance, never image or input authority."""

import base64
import unittest
from dataclasses import replace
from types import SimpleNamespace

from sdk.interaction.read.acquisition.images.test_window_overview import preview
from sdk.parity.test_protocol_parity import CAPTURE, STAMP

from computer_use.client import images
from computer_use.interaction.read.acquisition.images.overview import compose_overview
from computer_use.interaction.read.app import NativeAppReader
from computer_use.interaction.read.delivery.image_caption import windowStateNotes
from computer_use.protocol.captures import captureMetadata, decodeCapture
from computer_use.protocol.png import PNG

STATE = dict(sampledAt=1300, onScreen=True, ownerForeground=False)


class CaptureWindowStateTests(unittest.TestCase):
    @staticmethod
    def capture(state=STATE):
        return decodeCapture({**CAPTURE, "windowState": state}, "window", "scope", STAMP)

    def test_optional_samples_are_immutable_and_bounded_by_the_capture(self):
        legacy = decodeCapture(CAPTURE, "window", "scope", STAMP)
        state = dict(STATE)
        png = self.capture(state)
        state["ownerForeground"] = True
        self.assertEqual(png, legacy)
        self.assertEqual(captureMetadata(png)["id"], captureMetadata(legacy)["id"])
        self.assertEqual(dict(captureMetadata(png)["windowState"]), STATE)
        self.assertNotIn("windowState", captureMetadata(legacy))
        self.assertIsNone(captureMetadata(PNG(png)))
        with self.assertRaises(TypeError):
            captureMetadata(png)["windowState"]["onScreen"] = False
        for sample in (1200, 1500):
            self.capture({**STATE, "sampledAt": sample})
        for state in (
            None,
            {**STATE, "sampledAt": 1199},
            {**STATE, "sampledAt": 1501},
            {**STATE, "onScreen": 1},
            {**STATE, "ownerForeground": "false"},
            {**STATE, "occluded": False},
            {"sampledAt": 1300, "onScreen": True},
        ):
            with self.subTest(state=state), self.assertRaises(ValueError):
                self.capture(state)
        for kind in ("overview", "desktop"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                decodeCapture({**CAPTURE, "kind": kind, "windowState": STATE}, kind)

    def test_only_confirmed_false_samples_add_conditional_guidance(self):
        for on_screen, foreground in (
            (None, None),
            (True, True),
            (None, True),
            (True, None),
            (True, False),
            (False, True),
            (False, None),
        ):
            with self.subTest(onScreen=on_screen, ownerForeground=foreground):
                png = self.capture({**STATE, "onScreen": on_screen, "ownerForeground": foreground})
                notes = windowStateNotes(captureMetadata(png))
                self.assertEqual(len(notes), 2 if False in (on_screen, foreground) else 1)
                self.assertIn("do not measure occlusion or repaint", notes[0])
                self.assertNotIn("synchronized", "\n".join(notes))
                self.assertNotIn("stale", "\n".join(notes))
        unknown = windowStateNotes(captureMetadata(self.capture({**STATE, "onScreen": None, "ownerForeground": None})))
        self.assertIn("onScreen=unknown; ownerForeground=unknown", unknown[0])
        self.assertEqual(windowStateNotes(captureMetadata(decodeCapture(CAPTURE, "window"))), ())

    def test_plain_read_and_standalone_caption_keep_guidance_without_repeating_long_text(self):
        png = self.capture()
        surface = dict(surface="surface", window="root", title="Document", accessibility=True, nativeWindow=True)
        directory = dict(surfaces=[surface], focusedWindow=None)
        text = NativeAppReader._text(
            SimpleNamespace(name="Fixture"),
            SimpleNamespace(surface="surface"),
            directory,
            directory,
            {},
            dict(text='0 textfield "new AX value"'),
            SimpleNamespace(image=png),
            [],
            True,
            None,
        )
        self.assertIn("ownerForeground=false", text)
        self.assertIn("pixels may lag accessibility", text)
        self.assertNotIn("do not replay", text)
        self.assertIn("new AX value", text)
        images.registerImage(
            png,
            dict(
                kind="window",
                selector="Fixture",
                context="Window: Document.",
                window="0",
                observed=STAMP,
                current=lambda: True,
            ),
        )
        for shown in (False, True):
            caption = images.imageLabel(png, shown)
            self.assertEqual(caption.count("Capture time is not draw time"), 1)
            self.assertIn("do not replay input based only on old pixels", caption)
            self.assertNotIn("image is stale", caption)
            self.assertEqual("ownerForeground=false" in caption, not shown)
        self.assertIn("source unknown", images.imageLabel(PNG(png)))

    def test_overview_keeps_conditional_guidance_in_context_without_capture_limits(self):
        first, second = preview("0"), preview("1")
        original = first.image.image
        changed = decodeCapture(
            {
                **captureMetadata(original),
                "png": base64.b64encode(original).decode(),
                "windowState": {**STATE, "sampledAt": 1.5},
            },
            "window",
        )
        first = replace(first, image=replace(first.image, image=changed))
        overview = compose_overview([first, second], [], scope="app")
        self.assertEqual(overview.sources[0]["windowState"]["ownerForeground"], False)
        self.assertNotIn("windowState", overview.sources[1])
        self.assertNotIn("windowState", captureMetadata(overview.image))
        self.assertNotIn("stopReasons", captureMetadata(overview.image))
        self.assertNotIn("stopReasons", overview.sources[0])
        self.assertEqual(overview.sources[0]["capture"], first.image.target.capture)
        self.assertEqual(bytes(changed), bytes(original))
        self.assertIn("Window 0:", overview.text)
        self.assertEqual(overview.text.count("pixels may lag accessibility"), 1)
        images.registerImage(overview.image, dict(kind="overview", context=overview.text))
        for shown in (False, True):
            caption = images.imageLabel(overview.image, shown)
            self.assertEqual(caption.count("pixels may lag accessibility"), 1)
            self.assertIn("ownerForeground=false", caption)
            self.assertIn("Window 1:", caption)
            self.assertNotIn("capture limitations", caption)
            self.assertNotIn("stale", caption)


if __name__ == "__main__":
    unittest.main()
