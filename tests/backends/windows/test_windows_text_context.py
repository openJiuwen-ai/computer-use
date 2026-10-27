"""Range provenance, partial observations and negotiated Windows reading.

Synthetic wire cases supplement real HWND acceptance; they do not prove a
provider's endpoint/selection behaviour by themselves.
"""

import asyncio
import copy
import hashlib
import unittest
from types import SimpleNamespace

from observation_fixture import from_records, normalize_fixture as normalizeFacts, reply
from sdk.interaction.read.facts.test_native_facts import batch, edge, node

from computer_use.interaction.read.acquisition.reader import NativeReader
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.rendering.formatting import renderTree
from computer_use.native.contracts import Binding
from computer_use.native.semantics import WINDOWS_TEXT_CONTEXT_FIELDS, followed_relations
from computer_use.protocol.execution import decodeInspectQuery


def windows(nodes, relations=()):
    value = batch(nodes, relations)
    value["platform"] = "windows"
    return value


def anchor(text, *, complete=True):
    return dict(
        source="Text.DocumentRange",
        unit="utf16",
        units=len(text.encode("utf-16-le")) // 2,
        complete=complete,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def position(start, end):
    return dict(start=start, end=end, degenerate=start == end)


def objects(text, *items, **overrides):
    return dict(
        dict(
            source="IUIAutomation.TextPattern.RangeFromChild",
            anchor=anchor(text),
            state="complete",
            order="nativeEndpoints",
            objects=list(items),
            total=len(items),
        ),
        **overrides,
    )


def item(token, start, end, **overrides):
    value = dict(node=token, values=position(start, end), fieldStatus={})
    value.update(overrides)
    return value


def caret(text, offset, active=True, **overrides):
    result = dict(
        source="IUIAutomationTextPattern2.GetCaretRange",
        state="complete",
        values=dict(isActive=active, anchor=anchor(text), range=position(offset, offset)),
        fieldStatus={},
    )
    result.update(overrides)
    return result


class WindowsTextContextTests(unittest.TestCase):
    def render(self, raw, *, compact=True):
        snapshot = normalizeFacts(raw, DisplayReferences(raw["session"]))
        rendered = snapshot.render(compact=compact)
        self.assertEqual(snapshot.native, from_records(raw))
        self.assertEqual(set(rendered["refs"].values()), {n["node"] for n in raw["nodes"]} | set(raw["unread"]))
        self.assertFalse(any(p.get("presentation") == "uncompressed" for p in rendered["projections"]))
        return snapshot, rendered

    def test_composed_editor_keeps_independent_reading_semantics(self):
        for field, value in [
            ("headingLevel", 2),
            ("landmarkType", "main"),
            ("fullDescription", "Independent editor description"),
            ("localizedLandmarkType", "Editing region"),
        ]:
            with self.subTest(field=field):
                root = dict(
                    ref="0",
                    role="comboBox",
                    name="Owner",
                    value="",
                    control=dict(kind="textInput", members=[dict(ref="1", part="editor")]),
                    children=[dict(ref="1", role="textField", name="Owner", value="", children=[], **{field: value})],
                )
                rendered = renderTree(root, dict(nativeFacts=True, compact=True))
                self.assertIn("1", rendered["refs"])
                self.assertIn("\t1 text field", rendered["text"])

    def test_heading_landmark_and_full_description_preserve_role_and_actions(self):
        root = node(
            "document",
            ControlType="ControlType.Document",
            Name="Body",
            HeadingLevel=80052,
            LandmarkType=80002,
            LocalizedLandmarkType="Reading area",
            FullDescription="Independent long description",
        )
        root["actions"]["items"] = [dict(name="Invoke.Invoke", description="Invoke.Invoke")]
        snapshot, rendered = self.render(windows([root]))
        self.assertEqual(snapshot.roots[0]["role"], "document")
        self.assertEqual(snapshot.roots[0]["headingLevel"], 2)
        self.assertIn("heading level 2", rendered["text"])
        self.assertIn("main landmark", rendered["text"])
        self.assertIn("Landmark: Reading area", rendered["text"])
        self.assertIn("Description: Independent long description", rendered["text"])
        self.assertIn("Invoke.Invoke", rendered["details"])
        self.assertEqual(snapshot.facts["nodes"][0]["values"]["structure.heading_level"], 2)

    def test_meaningful_reading_semantics_prevent_anonymous_layout_pruning(self):
        for key, value in [
            ("HeadingLevel", 80051),
            ("LandmarkType", 80002),
            ("FullDescription", "Meaningful"),
            ("LocalizedLandmarkType", "Custom region"),
            ("HeadingLevel", "bad"),
            ("LandmarkType", 99999),
        ]:
            with self.subTest(key=key, value=value):
                raw = windows(
                    [
                        node("root", ControlType="ControlType.Window"),
                        node(
                            "layout",
                            ControlType="ControlType.Group",
                            IsControlElement=False,
                            IsContentElement=False,
                            **{key: value},
                        ),
                        node("leaf", ControlType="ControlType.Text", Name="Content"),
                    ],
                    [
                        edge("root", "layout", property="RawViewChildren"),
                        edge("layout", "leaf", property="RawViewChildren"),
                    ],
                )
                _, rendered = self.render(raw)
                self.assertIn("1", {ref for p in rendered["projections"] for ref in p["refs"]})

    def test_verified_utf16_object_positions_keep_duplicate_labels_and_original_refs(self):
        text = "🙂same same end"
        context = objects(text, item("second", 7, 11), item("image", 12, 12))
        raw = windows(
            [
                node(
                    "doc",
                    ControlType="ControlType.Document",
                    **{"Text.DocumentRange": text, "Text.ObjectRanges": context},
                ),
                node("first", ControlType="ControlType.Hyperlink", Name="same"),
                node("second", ControlType="ControlType.Hyperlink", Name="same"),
                node("image", ControlType="ControlType.Image", Name="Non-text image"),
            ],
            [
                edge("doc", "first", "second", "image", property="RawViewChildren"),
                edge("doc", "second", "image", property="Text.Children"),
            ],
        )
        _, rendered = self.render(raw)
        self.assertIn("2@7..11", rendered["text"])
        self.assertIn("3@12..12", rendered["text"])
        self.assertIn("UTF-16", rendered["text"])
        self.assertIn("Non-text image", rendered["text"])
        self.assertIn("Reading sequence of 0", rendered["text"])
        self.assertIn('"🙂same " → element 2 "same" → " " → element 3 → "end"', rendered["text"])
        raw["nodes"][0]["values"]["Text.ObjectRanges"]["objects"][1]["values"] = position(1, 1)
        _, malformed = self.render(raw)
        self.assertNotIn("3@1..1", malformed["text"])
        self.assertNotIn("Reading sequence of", malformed["text"])

    def test_position_failure_changed_anchor_and_unknown_types_never_make_confirmed_offsets(self):
        base = objects("same same", item("child", 5, 9))
        for mutation in (
            "hash",
            "length",
            "anchorStatus",
            "badAnchorStatus",
            "failedPosition",
            "unknownSource",
            "badPosition",
        ):
            with self.subTest(mutation=mutation):
                value = copy.deepcopy(base)
                if mutation == "hash":
                    value["anchor"]["sha256"] = "0" * 64
                if mutation == "length":
                    value["anchor"]["complete"] = True
                    value["anchor"]["units"] = 5
                if mutation == "anchorStatus":
                    value.update(state="partial", anchorStatus=dict(state="error", detail="changed"))
                if mutation == "badAnchorStatus":
                    value.update(state="partial", anchorStatus="unknown")
                if mutation == "failedPosition":
                    value.update(state="partial")
                    value["objects"][0].update(values={}, fieldStatus=dict(position=dict(state="timeout")))
                if mutation == "unknownSource":
                    value["source"] = "UnknownSource"
                if mutation == "badPosition":
                    value["objects"][0]["values"]["start"] = True
                raw = windows(
                    [
                        node(
                            "doc",
                            ControlType="ControlType.Document",
                            **{"Text.DocumentRange": "same same", "Text.ObjectRanges": value},
                        ),
                        node("child", ControlType="ControlType.Button", Name="Keep independent object"),
                    ],
                    [edge("doc", "child", property="Text.Children")],
                )
                _, rendered = self.render(raw)
                self.assertNotIn("1@5..9", rendered["text"])
                self.assertNotIn("Reading sequence of", rendered["text"])
                self.assertIn("Keep independent object", rendered["text"])
                self.assertTrue("text.embedded" in rendered["details"] or "Text.ObjectRanges" in rendered["details"])

    def test_missing_object_identity_is_protocol_error(self):
        raw = windows([node("doc", **{"Text.ObjectRanges": objects("body", item("foreign", 0, 1))})])
        from observation_fixture import wire

        from computer_use.protocol.observation.adapters import validate_driver_payload

        encoded = wire(raw)
        value = encoded["nodes"][0]["properties"][0]["value"]
        value["metadata"] = [["embedded_object", dict(nativeType="IUIAutomationElement", data=dict(ref="foreign"))]]
        with self.assertRaisesRegex(ValueError, "undeclared"):
            validate_driver_payload(encoded)

    def test_body_continuation_does_not_make_verified_positions_unknown(self):
        full = "long " * 5000
        context = objects(full, item("child", 20000, 20004))
        root = node(
            "doc",
            ControlType="ControlType.Document",
            **{"Text.DocumentRange": full[:16384], "Text.ObjectRanges": context},
        )
        root["fieldStatus"]["Text.DocumentRange"] = dict(state="truncated", textRef="immutable", totalUnits=len(full))
        _, rendered = self.render(
            windows(
                [root, node("child", ControlType="ControlType.Hyperlink", Name="Link")],
                [edge("doc", "child", property="Text.Children")],
            )
        )
        self.assertIn("1@20000..20004", rendered["text"])
        self.assertNotIn("anchor extends beyond", rendered["text"])

    def test_caret_activity_and_position_are_independent_from_selection_and_focus(self):
        for active in (False, True):
            root = node(
                "doc",
                ControlType="ControlType.Document",
                HasKeyboardFocus=not active,
                **{"Text.DocumentRange": "abcdef", "Text.CaretRange": caret("abcdef", 4, active)},
            )
            _, rendered = self.render(windows([root]))
            self.assertIn("Caret of 0 (active=" + str(active).lower(), rendered["text"])
            self.assertIn("UTF-16 4..4", rendered["text"])
        absent = node(
            "doc",
            ControlType="ControlType.Document",
            HasKeyboardFocus=True,
            **{"Text.DocumentRange": "abcdef", "Text.Selection": dict(state="complete", total=0, ranges=[])},
        )
        _, rendered = self.render(windows([absent]))
        self.assertNotIn("Caret of", rendered["text"])

    def test_caret_error_and_malformed_non_degenerate_range_do_not_become_cursor(self):
        for value in [
            caret(
                "abcdef", 4, values=dict(isActive=True), state="partial", fieldStatus=dict(range=dict(state="error"))
            ),
            caret("abcdef", 4, values=dict(isActive=True, anchor=anchor("abcdef"), range=position(2, 4))),
        ]:
            _, rendered = self.render(
                windows(
                    [
                        node(
                            "doc",
                            ControlType="ControlType.Document",
                            **{"Text.DocumentRange": "abcdef", "Text.CaretRange": value},
                        )
                    ]
                )
            )
            self.assertNotIn("UTF-16 2..4", rendered["text"])
            self.assertTrue("text.caret" in rendered["details"] or "Text.CaretRange" in rendered["details"])

    def test_flows_descriptions_branch_cycle_and_unread_members_keep_tree_order(self):
        for mode in ("valid", "branch", "cycle", "partial"):
            with self.subTest(mode=mode):
                nodes = [
                    node("root", ControlType="ControlType.Window"),
                    node("a", Name="Alpha"),
                    node("b", Name="Beta"),
                    node("d", Name="Long description"),
                ]
                relations = [
                    edge("root", "b", "a", "d", property="RawViewChildren"),
                    edge("a", "d", property="DescribedBy"),
                    edge("a", "b", property="FlowsTo"),
                ]
                if mode == "branch":
                    relations[-1]["targets"].append("d")
                if mode == "cycle":
                    relations.append(edge("b", "a", property="FlowsTo"))
                if mode == "partial":
                    relations[-1]["state"] = "partial"
                snapshot, rendered = self.render(windows(nodes, relations))
                self.assertEqual([n["ref"] for n in snapshot.roots[0]["children"]], ["2", "1", "3"])
                self.assertIn("Description of 1", rendered["text"])
                self.assertIn("Long description", rendered["text"])
                expected = (
                    "confirmed provider hints"
                    if mode == "valid"
                    else "cyclic/conflicting"
                    if mode == "cycle"
                    else "partial/ambiguous"
                )
                self.assertIn("Reading order (" + expected + ")", rendered["text"])

    def test_new_query_contract_limits_focused_fields(self):
        query = dict(
            kind="nodes", fields=["Text.CaretRange"], maxNodes=1, maxDepth=0, focusedFields=["Text.CaretRange"]
        )
        self.assertEqual(decodeInspectQuery(query), query)
        for fields in ([], ["Name"], ["Text.CaretRange", "Text.CaretRange"]):
            with self.subTest(fields=fields):
                with self.assertRaises(ValueError):
                    decodeInspectQuery(dict(query, focusedFields=fields))
        with self.assertRaises(ValueError):
            decodeInspectQuery(dict(query, fields=["Name"]))

    def test_new_fields_and_followed_relations_are_negotiated(self):
        for supported in (False, True):

            class Port:
                connection = SimpleNamespace(
                    platform="windows",
                    capabilities={"nativeFollowRelations", "nativeUiaReadV2", "nativeUiaTextContext"}
                    if supported
                    else {"nativeFollowRelations", "nativeUiaReadV2"},
                )

                async def nodes(self, operation, target, fields, max_nodes, max_depth, timeout_ms, **options):
                    self.fields, self.options = fields, options
                    root = node("root")
                    return reply(windows([root]))

            port = Port()
            target = Binding("session", "instance", "window", "surface", "root")
            read = SimpleNamespace(target=target, token="operation", remaining=lambda: 1000)
            asyncio.run(NativeReader(port).collect(read))
            self.assertEqual("Text.ObjectRanges" in port.fields, supported)
            self.assertEqual("Text.CaretRange" in port.fields, supported)
            self.assertNotIn("Text.StyleId", port.fields)
            self.assertEqual(port.options.get("focused_fields"), ["Text.CaretRange"] if supported else None)
            expected_relations = [
                "Selection.Selection",
                "Table.RowHeaders",
                "Table.ColumnHeaders",
                "TableItem.RowHeaderItems",
                "TableItem.ColumnHeaderItems",
                "Text.Children",
            ]
            if supported:
                expected_relations += ["DescribedBy", "FlowsTo", "FlowsFrom"]
            self.assertEqual(port.options["follow_relations"], expected_relations)
            self.assertEqual("DescribedBy" in followed_relations("windows", port.connection.capabilities), supported)
            if not supported:
                self.assertTrue(set(WINDOWS_TEXT_CONTEXT_FIELDS).isdisjoint(port.fields))


if __name__ == "__main__":
    unittest.main()
