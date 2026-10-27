"""Explicit PNG bytes conversion retains its original observation only."""

import gc
import unittest
import weakref

from sdk.parity.test_protocol_parity import CAPTURE, STAMP

from computer_use.client import images
from computer_use.protocol.captures import decodeCapture
from computer_use.protocol.png import PNG
from computer_use.session import images as images_state


class ImageProvenanceTests(unittest.TestCase):
    @staticmethod
    def source(name="one", current=lambda: True):
        png = decodeCapture(CAPTURE, "window", "scope", STAMP)
        delivered = []
        images.registerImage(
            png,
            dict(kind="window", selector=name, window=name, current=current, delivered=lambda: delivered.append(name)),
        )
        return png, delivered

    def test_bytes_conversion_retains_caption_metadata_and_delivery(self):
        png, delivered = self.source()
        converted = bytes(png)
        self.assertIs(type(converted), bytes)
        self.assertEqual(converted, png)
        self.assertIs(bytes(png), converted, "At most one immutable copy is retained per source")
        self.assertEqual(images.imageLabel(converted), images.imageLabel(png))
        self.assertEqual(images.imageMetadata(converted), images.imageMetadata(png))
        images.imageDelivered(converted)
        self.assertEqual(delivered, ["one"])

    def test_equal_pixels_do_not_transfer_delivery_between_captures(self):
        first, delivered_first = self.source("one")
        second, delivered_second = self.source("two")
        a, b = bytes(first), bytes(second)
        self.assertEqual(a, b)
        self.assertIsNot(a, b)
        images.imageDelivered(a)
        self.assertEqual(delivered_first, ["one"])
        self.assertEqual(delivered_second, [])
        images.imageDelivered(b)
        self.assertEqual(delivered_second, ["two"])

    def test_stale_copy_cannot_mark_a_new_observation_delivered(self):
        current = [True]
        png, delivered = self.source(current=lambda: current[0])
        converted = bytes(png)
        current[0] = False
        self.assertIn("image is stale", images.imageLabel(converted))
        images.imageDelivered(converted)
        self.assertEqual(delivered, [])

    def test_modified_and_unregistered_copies_remain_unknown(self):
        png, delivered = self.source()
        unrelated = [memoryview(png).tobytes(), bytearray(png), PNG(png)]
        png[-1] ^= 1
        unrelated.append(bytes(png))
        for copy in unrelated:
            self.assertIn("source unknown", images.imageLabel(copy))
            self.assertIsNone(images.imageMetadata(copy))
            images.imageDelivered(copy)
        self.assertEqual(delivered, [])

    def test_re_registration_does_not_reassign_an_old_conversion(self):
        png, delivered = self.source()
        old = bytes(png)
        newer = []
        images.registerImage(png, dict(kind="window", selector="new", delivered=lambda: newer.append(True)))
        images.imageDelivered(old)
        self.assertEqual(delivered, [])
        self.assertEqual(newer, [])
        self.assertIn("source unknown", images.imageLabel(old))
        images.imageDelivered(bytes(png))
        self.assertEqual(newer, [True])

    def test_conversion_index_does_not_keep_original_captures_alive(self):
        png, delivered = self.source()
        converted = bytes(png)
        identity, source = id(converted), weakref.ref(png)
        del png
        gc.collect()
        self.assertIsNone(source())
        self.assertNotIn(identity, images_state._byteSources)
        self.assertIn("source unknown", images.imageLabel(converted))
        images.imageDelivered(converted)
        self.assertEqual(delivered, [])
