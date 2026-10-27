"""Reading context summaries use public text spans and relationships only."""

import unittest

from computer_use.interaction.read.facts.index import observation_index
from computer_use.interaction.read.presentation.summaries import read_summaries
from computer_use.protocol.observation import (
    Action,
    CoordinateSpace,
    Geometry,
    NativeNode,
    NativeObservation,
    ReadIssue,
    Relation,
    States,
    Supplement,
    TextInfo,
    TextSpan,
)


def summarize(nodes, *, issues=(), spaces=None, compact=True):
    observation = NativeObservation(
        "linux", "session", "capture", (0, 1), tuple(nodes)[:1], nodes, issues=issues, coordinate_spaces=spaces or {}
    )
    return "\n".join(
        read_summaries(
            observation_index(observation), {str(i): token for i, token in enumerate(nodes)}, compact=compact
        )
    )


class NormalizedTextContextTests(unittest.TestCase):
    def test_selection_coverage_keeps_empty_partial_and_caret_independent(self):
        text = TextInfo(
            "document",
            unit="utf16_code_unit",
            selections=(TextSpan(text="", collapsed=True), TextSpan(text="prefix")),
            caret=TextSpan(4, 4),
            caret_active=False,
        )
        node = NativeNode(
            role="document",
            states=States(focused=False),
            text=text,
            supplements=(Supplement("text.selections.coverage", {"total": 3}),),
        )
        output = summarize({"editor": node}, issues=(ReadIssue(("nodes", "editor", "text", "selections"), "partial"),))
        self.assertIn("partial; 2/3 ranges", output)
        self.assertIn('empty range 1: ""', output)
        self.assertIn("Caret of 0 (active=false): UTF-16 4..4", output)
        self.assertIn("keyboard focus and selection are independent", output)

    def test_identical_empty_ranges_group_only_in_compact_view(self):
        node = NativeNode(
            role="static_text",
            states=States(focused=False),
            text=TextInfo("document", selections=(TextSpan(text="", collapsed=True),)),
            supplements=(Supplement("text.selections.coverage", {"total": 1}),),
        )
        compact = summarize({"a": node, "b": node})
        self.assertIn("Text selection of elements 0, 1 (complete; 1/1 ranges)", compact)
        full = summarize({"a": node, "b": node}, compact=False)
        self.assertIn("Text selection of 0", full)
        self.assertIn("Text selection of 1", full)

    def test_utf16_embedded_positions_form_sequence_without_reparenting(self):
        node = NativeNode(
            role="document",
            text=TextInfo("document", unit="utf16_code_unit", content="A😀B", embedded=(("button", TextSpan(1, 3)),)),
        )
        output = summarize({"editor": node, "button": NativeNode(role="button")})
        self.assertIn("Embedded objects of 0 (UTF-16): 1@1..3", output)
        self.assertIn('Reading sequence of 0: "A" → element 1 "😀" → "B"', output)
        self.assertIn("membership is separate from containment", output)

    def test_unproven_provider_offsets_partial_and_broken_surrogates_do_not_invent_sequence(self):
        for unit, content, span, issues in (
            ("provider", "ABC", TextSpan(0, 1), ()),
            ("utf16_code_unit", "A😀B", TextSpan(1, 2), ()),
            ("utf16_code_unit", "A\ud800B", TextSpan(0, 1), ()),
            (
                "unicode_codepoint",
                "ABC",
                TextSpan(0, 1),
                (ReadIssue(("nodes", "editor", "text", "embedded"), "partial"),),
            ),
        ):
            with self.subTest(unit=unit, content=repr(content)):
                node = NativeNode(text=TextInfo("document", unit=unit, content=content, embedded=(("button", span),)))
                output = summarize({"editor": node, "button": NativeNode(role="button")}, issues=issues)
                self.assertIn("Embedded objects of 0", output)
                self.assertNotIn("Reading sequence", output)

    def test_context_previews_are_bounded_and_coincident_empty_objects_not_interleaved(self):
        content = "A" * 2000 + "B" + "C" * 2000
        node = NativeNode(
            text=TextInfo(
                "document", unit="unicode_codepoint", content=content, embedded=(("button", TextSpan(2000, 2001)),)
            )
        )
        output = summarize({"editor": node, "button": NativeNode(role="button")})
        self.assertIn("preview; full text and ranges remain in details", output)
        self.assertLess(len(output), 1000)
        self.assertEqual(node.text.content, content)
        duplicate = NativeNode(
            text=TextInfo(
                "document",
                unit="unicode_codepoint",
                content="AB",
                embedded=(("a", TextSpan(1, 1)), ("b", TextSpan(1, 1))),
            )
        )
        output = summarize({"editor": duplicate, "a": NativeNode(role="button"), "b": NativeNode(role="button")})
        self.assertIn("1@1..1, 2@1..1", output)
        self.assertNotIn("Reading sequence", output)

    def test_line_ranges_keep_gaps_and_unconfirmed_document_end(self):
        node = NativeNode(
            text=TextInfo(
                "document",
                unit="unicode_codepoint",
                content="abcdefgh",
                line_ranges=(TextSpan(2, 4, "cd"), TextSpan(6, 8, "gh")),
            ),
            supplements=(
                Supplement(
                    "text.line_ranges.coverage", {"unit_support": "unconfirmed", "document_end_observed": False}
                ),
            ),
        )
        output = summarize({"editor": node})
        self.assertIn("unsegmented source 0..2", output)
        self.assertIn("unsegmented source 4..6", output)
        self.assertIn("provider may substitute a larger unit", output)
        self.assertIn("remaining layout unconfirmed", output)

    def test_relationship_branches_and_cycles_are_described_without_reordering(self):
        for links, label in (
            ({"a": ("b",), "b": ("a",)}, "cyclic/conflicting"),
            ({"a": ("b", "c"), "b": ("a",), "c": ()}, "cyclic/conflicting"),
            ({"a": ("c",), "b": ("c",), "c": ()}, "partial/ambiguous"),
            ({"a": ("b",), "b": ("c",), "c": ()}, "confirmed provider hints"),
        ):
            nodes = {
                key: NativeNode(relations={"flow": Relation("flows_to", targets)}) for key, targets in links.items()
            }
            output = summarize(nodes)
            self.assertIn("Reading order (" + label + ")", output)
            self.assertIn("containment order is unchanged", output)

    def test_conflicting_coverage_supplements_do_not_guess_one_total(self):
        node = NativeNode(
            text=TextInfo("document", selections=(TextSpan(text="A"),)),
            supplements=(
                Supplement("text.selections.coverage", {"total": 2}),
                Supplement("text.selections.coverage", {"total": 3}),
            ),
        )
        output = summarize({"editor": node})
        self.assertIn("conflict; 1 ranges", output)
        self.assertNotIn("1/2", output)
        self.assertNotIn("1/3", output)

    def test_unnamed_leaf_geometry_uses_declared_space_not_capture_coordinates(self):
        node = NativeNode(
            role="button",
            name="",
            states=States(offscreen=False),
            actions=(Action("invoke", "invoke"),),
            relations={"children": Relation("children", ())},
            geometry={"bounds": Geometry("screen", -100, 20, 40, 50, True)},
        )
        output = summarize(
            {"button": node}, spaces={"screen": CoordinateSpace("physical_pixel", "primary_screen_top_left", "down")}
        )
        self.assertIn("0=(-100, 20, 40, 50)", output)
        self.assertIn("physical screen pixels", output)
        self.assertIn("not App PNG coordinates", output)
        self.assertIn("do not prove a point is clickable", output)


if __name__ == "__main__":
    unittest.main()
