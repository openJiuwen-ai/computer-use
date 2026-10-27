"""Native absent ranges stay distinct from real carets and exact integer offsets."""

import unittest

from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.formatting import numberString
from computer_use.interaction.read.presentation.preparation import prepareSnapshot
from computer_use.protocol.observation import decode_observation, encode_observation
from computer_use.protocol.observation.adapters import decode_query_facts
from observation_fixture import from_records, normalize_fixture

from sdk.interaction.read.facts.test_native_facts import batch, edge, node

NOT_FOUND = 2**63 - 1


class MacOSSelectionSentinelTests(unittest.TestCase):
    def test_native_absence_has_no_position_and_retains_exact_raw_evidence(self):
        for role in ("AXButton", "AXGroup", "AXTextField"):
            with self.subTest(role=role):
                value = {"location": NOT_FOUND, "length": 0}
                raw = batch([node("target", AXRole=role, AXSelectedTextRange=value)])
                graph = from_records(raw)
                target = graph.nodes["target"]
                self.assertTrue(target.text is None or target.text.selections is None)
                issue = next(issue for issue in graph.issues if issue.path[-2:] == ("text", "selections"))
                self.assertEqual(issue.state, "absent")
                self.assertIn("NSNotFound", issue.detail)
                evidence = next(item for item in target.supplements if item.key == "text.unlocated_range")
                self.assertEqual(evidence.value, value)
                self.assertEqual(graph, decode_observation(encode_observation(graph)))
                from observation_fixture import wire

                self.assertEqual(decode_query_facts(wire(raw))["nodes"][0]["values"]["AXSelectedTextRange"], value)

    def test_real_zero_length_and_nonempty_ranges_keep_their_positions(self):
        for location, length in ((0, 0), (3, 0), (2, 4), (NOT_FOUND - 1, 0)):
            with self.subTest(location=location, length=length):
                raw = batch(
                    [node("target", AXRole="AXTextField", AXSelectedTextRange={"location": location, "length": length})]
                )
                graph = from_records(raw)
                span = graph.nodes["target"].text.selections[0]
                self.assertEqual((span.start, span.end, span.collapsed), (location, location + length, length == 0))
                snapshot = normalize_fixture(raw, DisplayReferences("session"))
                self.assertEqual(snapshot.roots[0]["selectionRange"], {"location": location, "length": length})
                self.assertFalse(graph.issues)

    def test_plural_absent_and_mixed_ranges_do_not_claim_complete_offsets(self):
        sentinel = {"location": NOT_FOUND, "length": 0}
        for ranges, state, expected in (
            ([sentinel], "absent", None),
            ([sentinel, {"location": 1, "length": 2}], "partial", (1, 3)),
            ([{"location": NOT_FOUND, "length": 1}], "error", None),
        ):
            with self.subTest(ranges=ranges):
                graph = from_records(batch([node("target", AXRole="AXTextField", AXSelectedTextRanges=ranges)]))
                text = graph.nodes["target"].text
                if expected is None:
                    self.assertTrue(text is None or text.selections is None)
                else:
                    self.assertEqual([(span.start, span.end) for span in text.selections], [expected])
                self.assertEqual(next(issue.state for issue in graph.issues if issue.path[-1] == "selections"), state)

    def test_visible_range_absence_is_not_a_text_selection_or_caret(self):
        raw = batch([node("target", AXRole="AXTextArea", AXVisibleCharacterRange={"location": NOT_FOUND, "length": 0})])
        graph = from_records(raw)
        self.assertIsNone(graph.nodes["target"].text)
        self.assertTrue(any(issue.path[-1] == "visible_ranges" and issue.state == "absent" for issue in graph.issues))

    def test_non_macos_provider_range_does_not_use_cocoa_sentinel_semantics(self):
        raw = batch([node("target", role="entry", **{"Text.selections": [{"start": NOT_FOUND, "end": NOT_FOUND}]})])
        raw["platform"] = "linux"
        graph = from_records(raw)
        span = graph.nodes["target"].text.selections[0]
        self.assertEqual((span.start, span.end), (NOT_FOUND, NOT_FOUND))

    def test_default_tree_groups_absence_instead_of_repeating_false_selections(self):
        raw = batch(
            [node("window", AXRole="AXWindow")]
            + [
                node(
                    str(i),
                    AXRole="AXButton",
                    AXTitle=f"Button {i}",
                    AXSelectedTextRange={"location": NOT_FOUND, "length": 0},
                )
                for i in range(43)
            ],
            [edge("window", *(str(i) for i in range(43)))],
        )
        snapshot = normalize_fixture(raw, DisplayReferences("session"))
        prepared = prepareSnapshot(snapshot)
        output = DisplayDocument.tree(prepared).text
        self.assertNotIn("Selection:", output)
        self.assertNotIn("Text selection of", output)
        self.assertNotIn("9223372036854776000", output)
        self.assertNotIn("AX reports NSNotFound", output)
        details = DisplayDocument.tree(prepared, details=True).text
        self.assertEqual(details.count("AX reports NSNotFound"), 1)
        self.assertEqual(details.count(str(NOT_FOUND)), 1)
        self.assertNotIn("Text selection of", details)
        self.assertLess(len(output), 3000)

    def test_integer_formatting_is_lossless_without_changing_float_formatting(self):
        for value in (0, 123, 2**53 + 1, NOT_FOUND, -NOT_FOUND, 10**21):
            self.assertEqual(numberString(value), str(value))
        for value, text in ((1.5, "1.5"), (1e-6, "0.000001"), (1e21, "1e+21"), (float("inf"), "Infinity")):
            self.assertEqual(numberString(value), text)
