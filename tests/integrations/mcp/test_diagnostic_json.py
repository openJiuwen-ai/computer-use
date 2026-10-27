"""Large diagnostic encoding preserves facts without presentation work per scalar."""

import json
import math
import unittest
from types import MappingProxyType
from unittest.mock import patch

from computer_use._json import dumps as legacyDumps
from computer_use_mcp import diagnostic_json


class DiagnosticJSONTests(unittest.TestCase):
    def observation(self, **extra):
        return {
            "kind": "command_end",
            "method": "inspect",
            "result": {"kind": "observation", "nodes": [{"value": "value"}]},
            **extra,
        }

    def test_native_envelope_and_journal_preserve_values_without_scalar_formatter(self):
        event = self.observation()
        event["result"]["nodes"][0].update(text="测试\n\ud800", number=0.125, boolean=True, absent=None)
        for record in (event, {"diagnostic": event, "executionId": 3}):
            with self.subTest(envelope="diagnostic" in record):
                with patch.object(diagnostic_json, "legacyDumps", side_effect=AssertionError("slow scalar walk")):
                    encoded = diagnostic_json.dumps(record).encode("utf-8", "backslashreplace")
                self.assertEqual(json.loads(encoded), record)

    def test_other_messages_keep_existing_serialization(self):
        for value in ({"output": {"type": "text", "text": "body"}}, {"diagnostic": {"kind": "command_start"}}):
            with self.subTest(value=value):
                self.assertEqual(diagnostic_json.dumps(value), legacyDumps(value))

    def test_custom_diagnostics_retain_nonfinite_and_immutable_fallback(self):
        event = self.observation(extension=MappingProxyType({"value": math.nan}))
        self.assertEqual(diagnostic_json.dumps(event), legacyDumps(event))
        self.assertIsNone(json.loads(diagnostic_json.dumps(event))["extension"]["value"])


if __name__ == "__main__":
    unittest.main()
