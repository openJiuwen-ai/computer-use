"""Native line hints supplement UTF-16 text; they never rewrite it."""

import asyncio
import copy
import unittest
from types import SimpleNamespace

from backends.windows.test_windows_text_context import anchor, position, windows
from observation_fixture import normalize_fixture as normalizeFacts, reply
from sdk.interaction.read.facts.test_native_facts import batch, node

from computer_use.interaction.read.acquisition.reader import NativeReader
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.native.contracts import Binding
from computer_use.protocol.execution import decodeInspectQuery
from computer_use.protocol.observation import Supplement
from computer_use.protocol.observation.adapters.windows_text import line_ranges


def freeze(value):
    return Supplement("fixture", value).value


def lines(text, spans, **overrides):
    encoded = text.encode("utf-16-le")
    records = [
        dict(values=dict(position(start, end), text=encoded[start * 2 : end * 2].decode("utf-16-le")), fieldStatus={})
        for start, end in spans
    ]
    return dict(
        dict(
            source="IUIAutomation.TextPattern.TextUnit.Line",
            requestedUnit="Line",
            unitSupport="unconfirmed",
            anchor=anchor(text),
            ranges=records,
            state="complete",
            documentEndReached=True,
            detail="Native provider may substitute a larger unit.",
        ),
        **overrides,
    )


def render(text, context):
    raw = windows(
        [
            node(
                "editor",
                ControlType="ControlType.Edit",
                Name="Body",
                **{"Value.Value": text, "Text.DocumentRange": text, "Text.LineRanges": context},
            )
        ]
    )
    snapshot = normalizeFacts(raw, DisplayReferences(raw["session"]))
    return snapshot, snapshot.render()


class WindowsLineRangesTests(unittest.TestCase):
    def test_line_positions_refer_to_document_text_when_control_value_differs(self):
        context = lines("abc", [(0, 3)])
        raw = windows(
            [
                node(
                    "editor",
                    ControlType="ControlType.Edit",
                    Name="Body",
                    **{
                        "Value.Value": "independent control value",
                        "Text.DocumentRange": "abc",
                        "Text.LineRanges": context,
                    },
                )
            ]
        )
        snapshot = normalizeFacts(raw, DisplayReferences(raw["session"]))
        shown = snapshot.render()
        self.assertEqual(snapshot.roots[0]["value"], "independent control value")
        self.assertEqual(snapshot.facts["nodes"][0]["values"]["text.content"], "abc")
        self.assertIn('0..3 "abc"', shown["text"])
        self.assertIn("Native line-unit spans of 0 (UTF-16)", shown["text"])

    def test_layout_units_do_not_insert_newlines_into_the_original_value(self):
        text = "APP_LINE_BEGIN\nTail 中文 😀\n"
        context = lines(text, [(0, 14), (14, 15), (15, 25), (25, 26)])
        self.assertTrue(line_ranges(context))
        snapshot, shown = render(text, context)
        self.assertEqual(snapshot.roots[0]["value"], text)
        self.assertEqual(snapshot.facts["nodes"][0]["values"]["text.content"], text)
        self.assertIn("Native line-unit spans of 0", shown["text"])
        self.assertIn('14..15 "\\n"', shown["text"])
        self.assertIn('25..26 "\\n"', shown["text"])
        self.assertIn("provider may substitute a larger unit", shown["text"])

    def test_unsegmented_text_is_explicit_and_cannot_be_marked_complete(self):
        value = lines("a\nb", [(0, 1), (2, 3)], state="partial")
        self.assertTrue(line_ranges(value))
        _, shown = render("a\nb", value)
        self.assertIn("unsegmented source 1..2", shown["text"])
        value["state"] = "complete"
        self.assertFalse(line_ranges(value))

    def test_conflicting_text_and_surrogate_endpoints_are_not_confirmed(self):
        value = lines("abc", [(0, 3)])
        value["ranges"][0]["values"]["text"] = "xyz"
        self.assertTrue(line_ranges(value))  # Wire types alone cannot prove agreement.
        _, shown = render("abc", value)
        self.assertIn("span unconfirmed", shown["text"])
        self.assertNotIn('0..3 "xyz"', shown["text"])
        value = lines("😀x", [(0, 3)])
        value["ranges"] = [dict(values=dict(start=0, end=1, degenerate=False, text="x"), fieldStatus={})]
        value["state"], value["documentEndReached"] = "partial", False
        _, shown = render("😀x", value)
        self.assertIn("span unconfirmed", shown["text"])
        self.assertNotIn('0..1 "x"', shown["text"])

    def test_anchor_change_clears_confirmed_positions(self):
        value = lines("abc", [(0, 3)])
        value.update(
            state="partial", documentEndReached=False, anchorStatus=dict(state="error", detail="document changed")
        )
        value["ranges"] = [dict(values={}, fieldStatus=dict(position=dict(state="error", detail="document changed")))]
        self.assertTrue(line_ranges(value))
        _, shown = render("changed", value)
        self.assertIn("anchor unconfirmed", shown["text"])
        self.assertIn("span unconfirmed", shown["text"])
        self.assertNotIn("0..3", shown["text"])

    def test_limits_failures_and_unknown_sources_remain_raw_facts(self):
        valid = lines("abc", [(0, 3)])
        cases = []
        for key, bad in [
            ("source", "invented"),
            ("requestedUnit", "Paragraph"),
            ("unitSupport", "confirmed"),
            ("documentEndReached", 1),
            ("ranges", []),
            ("anchor", anchor("ab")),
        ]:
            value = copy.deepcopy(valid)
            value[key] = bad
            cases.append(value)
        value = copy.deepcopy(valid)
        value["ranges"] *= 33
        cases.append(value)
        value = copy.deepcopy(valid)
        value["ranges"][0]["values"]["end"] = 4
        cases.append(value)
        value = copy.deepcopy(valid)
        value["ranges"][0]["values"]["text"] = "longer"
        cases.append(value)
        for value in cases:
            with self.subTest(value=value):
                self.assertFalse(line_ranges(value))
                snapshot, shown = render("abc", value)
                self.assertEqual(
                    next(
                        item.value
                        for item in snapshot.native.nodes["editor"].supplements
                        if item.key == "provider.windows.Text.LineRanges"
                    ),
                    freeze(value),
                )
                self.assertNotIn("Native line-unit spans of", shown["text"])

    def test_partial_prefix_empty_document_and_native_larger_unit_are_honest(self):
        self.assertTrue(line_ranges(lines("", [(0, 0)])))
        self.assertTrue(line_ranges(lines("a\nb", [(0, 3)])))
        value = lines("abc", [(0, 1)], state="partial", documentEndReached=False)
        self.assertTrue(line_ranges(value))
        _, shown = render("abc", value)
        self.assertIn("remaining layout unconfirmed", shown["text"])
        value["ranges"][0]["fieldStatus"]["text"] = dict(state="truncated")
        value["ranges"][0]["values"]["text"] = ""
        self.assertTrue(line_ranges(value))

    def test_primary_probe_query_is_an_exact_subset(self):
        query = dict(
            kind="nodes", fields=["Text.LineRanges"], maxNodes=1, maxDepth=0, primaryFields=["Text.LineRanges"]
        )
        self.assertEqual(decodeInspectQuery(query), query)
        for fields in ([], ["Name"], ["Text.LineRanges", "Text.LineRanges"]):
            with self.subTest(fields=fields):
                with self.assertRaises(ValueError):
                    decodeInspectQuery(dict(query, primaryFields=fields))
        with self.assertRaises(ValueError):
            decodeInspectQuery(dict(query, fields=["Name"]))

    def test_default_profile_negotiates_only_windows_line_support(self):
        for platform, enabled in [("windows", False), ("windows", True), ("macos", True), ("linux", True)]:
            with self.subTest(platform=platform, enabled=enabled):

                class Port:
                    connection = SimpleNamespace(
                        platform=platform, capabilities={"nativeUiaTextLines"} if enabled else set()
                    )

                    async def nodes(
                        self,
                        operation,
                        target,
                        fields,
                        max_nodes,
                        max_depth,
                        timeout_ms,
                        *,
                        platform=platform,
                        **options,
                    ):
                        self.fields, self.options = fields, options
                        result = batch([node("root")])
                        result["platform"] = platform
                        return reply(result)

                port = Port()
                read = SimpleNamespace(
                    target=Binding("session", "instance", "window", "surface", "root"),
                    token="operation",
                    remaining=lambda: 1000,
                )
                asyncio.run(NativeReader(port).collect(read))
                expected = enabled and platform == "windows"
                self.assertEqual("Text.LineRanges" in port.fields, expected)
                self.assertEqual(port.options.get("primary_fields"), ["Text.LineRanges"] if expected else None)
