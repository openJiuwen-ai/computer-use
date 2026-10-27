"""OCR is bounded image evidence, never a synthetic element or input grant."""

import unittest

from sdk.parity.test_protocol_parity import CAPTURE, STAMP

from computer_use.client import images
from computer_use.protocol.captures import captureMetadata, decodeCapture
from computer_use.protocol.png import PNG


def visual():
    return dict(
        source="windowsOCR",
        status="complete",
        language="en-US",
        truncated=False,
        lines=[dict(text="Schedule meeting", rect=dict(x=0, y=0, width=1, height=1))],
    )


class CaptureVisualTextTests(unittest.TestCase):
    def test_image_text_keeps_exact_pixels_and_immutable_geometry_without_element_authority(self):
        source = visual()
        png = decodeCapture({**CAPTURE, "visualText": source}, "window", "scope", STAMP)
        self.assertEqual(png, decodeCapture(CAPTURE, "window", "scope", STAMP))
        source["lines"][0]["rect"]["x"] = 9
        annotation = captureMetadata(png)["visualText"]
        self.assertEqual(annotation["lines"][0]["rect"]["x"], 0)
        with self.assertRaises(TypeError):
            annotation["lines"][0]["text"] = "changed"
        self.assertIsNone(captureMetadata(PNG(png)))
        images.registerImage(png, dict(kind="window", selector="Fixture", current=lambda: True))
        label = images.imageLabel(png)
        self.assertIn("Windows OCR", label)
        self.assertIn("Schedule meeting", label)
        self.assertIn("not UIA elements or proof of clickability", label)
        self.assertNotIn("ref=", label)

    def test_malformed_or_outside_image_annotations_are_rejected(self):
        values = [
            None,
            {**visual(), "source": "guessedButtons"},
            {**visual(), "status": "verified"},
            {**visual(), "truncated": "false"},
            {**visual(), "status": "unavailable"},
            {**visual(), "lines": visual()["lines"] * 65},
        ]
        for key, value in [("x", -1), ("width", 0), ("height", float("inf")), ("x", 1), ("y", 1)]:
            item = visual()
            item["lines"][0]["rect"][key] = value
            values.append(item)
        item = visual()
        item["lines"][0]["role"] = "button"
        values.append(item)
        item = visual()
        item["lines"][0]["text"] = "x" * 257
        values.append(item)
        item = visual()
        item["lines"] *= 33
        for line in item["lines"]:
            line["text"] = "x" * 256
        values.append(item)
        for value in values:
            with self.subTest(value=str(value)[:150]), self.assertRaises(ValueError):
                decodeCapture({**CAPTURE, "visualText": value}, "window", "scope", STAMP)

    def test_unavailable_ocr_preserves_the_image_and_stale_caption(self):
        unavailable = dict(
            source="windowsOCR", status="unavailable", language=None, lines=[], truncated=False, reason="no recognizer"
        )
        png = decodeCapture({**CAPTURE, "visualText": unavailable}, "window", "scope", STAMP)
        images.registerImage(png, dict(kind="window", selector="Fixture", current=lambda: False))
        label = images.imageLabel(png)
        self.assertIn("This image is stale", label)
        self.assertIn("unavailable. no recognizer", label)
        self.assertEqual(png, decodeCapture(CAPTURE, "window", "scope", STAMP))


if __name__ == "__main__":
    unittest.main()
