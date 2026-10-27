"""Full observation text is an explicit, cached view of one captured document."""

import unittest

from computer_use._json import dumps
from computer_use.client.computer import observationState
from computer_use.session.observation import lazyObservation, peekObservationText


class CountingDocument:
    def __init__(self, text="captured full text"):
        self.value, self.renders = text, 0

    @property
    def text(self):
        self.renders += 1
        return self.value


class ObservationTextTests(unittest.TestCase):
    def test_images_keys_and_cached_text_probe_do_not_render(self):
        document, pixels = CountingDocument(), b"pixels"
        state = lazyObservation(document, pixels)
        self.assertEqual(tuple(state), ("text", "screenshot"))
        self.assertEqual(len(state), 2)
        self.assertIn("text", state)
        self.assertIn("screenshot", state)
        self.assertIn("text", state.keys())
        self.assertNotIn("details", state)
        self.assertNotIn("details", state.keys())
        self.assertIs(state.screenshot, pixels)
        self.assertIs(state["screenshot"], pixels)
        self.assertIsNone(peekObservationText(state))
        self.assertEqual(document.renders, 0)

    def test_explicit_text_access_and_repr_share_the_same_cached_capture(self):
        document = CountingDocument()
        state = lazyObservation(document)
        self.assertEqual(state.text, document.value)
        self.assertEqual(state["text"], document.value)
        self.assertEqual(repr(state), document.value)
        self.assertEqual(peekObservationText(state), document.value)
        self.assertEqual(document.renders, 1)
        with self.assertRaises(AttributeError):
            state.text = "changed"
        with self.assertRaises(TypeError):
            state["text"] = "changed"

    def test_json_serialization_is_a_text_output_boundary(self):
        document = CountingDocument()
        state = lazyObservation(document)
        self.assertEqual(dumps(state), '{"text":"captured full text","screenshot":null}')
        self.assertEqual(document.renders, 1)

    def test_observation_factory_preserves_plain_text_and_supports_documents(self):
        document = CountingDocument()
        state = observationState(dict(screenshot=b"pixels"), document=document)
        self.assertEqual(state.screenshot, b"pixels")
        self.assertEqual(document.renders, 0)
        self.assertEqual(state.text, document.value)
        plain = observationState(dict(text="existing", screenshot=None, details="retained"))
        self.assertEqual(peekObservationText(plain), "existing")
        self.assertEqual(plain.details, "retained")

    def test_internal_details_are_lazy_and_do_not_expand_public_observations(self):
        document, details = CountingDocument("full"), CountingDocument("source details")
        state = lazyObservation(document, details_document=details, overview=b"overview")
        self.assertEqual(state.overview, b"overview")
        self.assertEqual((document.renders, details.renders), (0, 0))
        self.assertEqual(state.details, "source details")
        self.assertEqual(state["details"], "source details")
        self.assertEqual((document.renders, details.renders), (0, 1))
        self.assertIsNone(peekObservationText(state))


if __name__ == "__main__":
    unittest.main()
