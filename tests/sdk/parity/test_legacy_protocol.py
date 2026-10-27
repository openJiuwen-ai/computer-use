"""Historical fixture handshakes must preserve structured backend failures."""

import unittest

from legacy_protocol import decodeHello

from computer_use.errors import ComputerUseError


class LegacyHelloFailureTests(unittest.TestCase):
    @staticmethod
    def reply(**changes):
        failure = dict(
            code="input_unavailable", target="fixture", effect="none", detail="Not available", next="read again"
        )
        return dict(version=3, session="session", id=0, error={**failure, **changes})

    def test_error_reply_preserves_backend_failure(self):
        with self.assertRaises(ComputerUseError) as caught:
            decodeHello(self.reply(), "shared")
        self.assertEqual(caught.exception.code, "input_unavailable")
        self.assertEqual(caught.exception.effect, "none")
        self.assertEqual(caught.exception.target, "fixture")
        self.assertEqual(caught.exception.detail, "Not available")
        self.assertEqual(caught.exception.next, "read again")

    def test_malformed_failure_is_rejected_by_protocol_decoder(self):
        with self.assertRaisesRegex(ValueError, "invalid error effect"):
            decodeHello(self.reply(effect="unknown"), "shared")
