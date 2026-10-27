"""Text presentation passes must conserve fields, boundaries and targets."""

import copy
import unittest

from sdk.interaction.read.facts.test_native_facts import edge, node
from sdk.interaction.read.presentation.fixtures import FixtureProjection, n
from sdk.interaction.read.presentation.test_full_presentation import displayed, observe, web

from computer_use.interaction.read.presentation.accounting.fields import RenderedFields
from computer_use.interaction.read.presentation.actions.layout import action_fields
from computer_use.interaction.read.presentation.compression.model import Scope
from computer_use.interaction.read.presentation.compression.passes.merge_repeated_text import merge_repeated_text
from computer_use.interaction.read.presentation.compression.projection import projectTree
from computer_use.interaction.read.presentation.ir import thaw
from computer_use.interaction.read.presentation.rendering.formatting import field, renderedActions
from computer_use.interaction.read.presentation.rendering.layout.text_parts import compactTextParts, expandTextParts


class RepeatedTextPassTests(unittest.TestCase):
    @staticmethod
    def project(root, **context):
        state = FixtureProjection(root, dict(nativeFacts=True, **context))
        merge_repeated_text(state, state.edit, state.view, Scope(in_table=context.get("in_table", False)))
        state.assert_integrity()
        return state

    def test_exact_heading_caption_keeps_heading_level_and_distinct_action_source(self):
        root = n(
            0,
            "heading",
            n(1, "staticText", value="Network", actions=[dict(name="AXShowMenu", kind="show_menu")]),
            name="Network",
            value="6",
            valueIsNumber=True,
        )
        before = copy.deepcopy(root)
        state = self.project(root)
        self.assertEqual(state.view["children"], ())
        self.assertEqual(state.view["value"], "6")
        self.assertEqual(state.foldedInto, {"1": "0"})
        self.assertEqual(root, before)
        self.assertEqual(thaw(state.nativeNodes["1"]["actions"]), [dict(name="AXShowMenu", kind="show_menu")])
        self.assertNotIn("actions", state.view)

    def test_value_equivalence_accounts_for_both_sources_without_changing_value(self):
        root = n(0, "staticText", n(1, "staticText", value="10 MB"), value="10 MB")
        state = self.project(root)
        fields = RenderedFields()
        fields.record(state.view, "value")
        self.assertEqual(
            {(e["ref"], e["field"], e["value"], e["target"]) for e in fields.entries},
            {("0", "value", "10 MB", "0"), ("1", "value", "10 MB", "0")},
        )
        fields = RenderedFields()
        fields.record(state.view, "value", kind="continuation")
        self.assertEqual(fields.entries, [dict(ref="0", field="value", value="10 MB", target="0", kind="continuation")])

    def test_substrings_whitespace_and_independent_fields_do_not_disappear(self):
        for extra in (
            dict(value="Net"),
            dict(value=" Network"),
            dict(value="Network "),
            dict(description="Different"),
            dict(help="Help"),
            dict(url="https://example.org"),
            dict(placeholder="Enter"),
            dict(titleRef="7"),
            dict(labelTargets=["7"]),
            dict(subrole="paragraph"),
            dict(preserveGroup=True),
            dict(valueIsNumber=True),
            dict(partialReason="pending"),
            dict(actionsUnavailable=True),
            dict(focusable=True),
            dict(focused=True),
            dict(selected=True),
            dict(enabled=False),
            dict(selectable=True),
            dict(pressable=True),
            dict(modal=True),
            dict(busy=True),
            dict(selectionRange=dict(location=1, length=1)),
            dict(selectedText="N"),
            dict(actions=[dict(name="Connect")]),
            dict(capabilities=dict(click="available")),
            dict(rowRange=dict(location=0, length=1)),
        ):
            with self.subTest(extra=extra):
                child = n(1, "staticText", **{**dict(value="Network"), **extra})
                state = self.project(n(0, "heading", child, name="Network"))
                self.assertEqual(state.foldedInto, {})
                self.assertEqual(state.view["children"][0]["ref"], "1")

    def test_table_editor_unknown_parent_subtree_and_live_children_keep_boundaries(self):
        cases = [
            ("textField", {}, {}),
            ("unknown", {}, {}),
            ("heading", {"preserveGroup": True}, {}),
            ("heading", {"partialReason": "pending"}, {}),
            ("heading", {}, {"in_table": True}),
            ("heading", {}, {"focusedRef": "1"}),
            ("heading", {}, {"subtreeRef": "1"}),
        ]
        for role, fields, context in cases:
            with self.subTest(role=role, fields=fields, context=context):
                state = self.project(
                    n(0, role, n(1, "staticText", value="Network"), name="Network", **fields), **context
                )
                self.assertEqual(state.foldedInto, {})
        state = self.project(n(0, "heading", n(1, "staticText", n(2, "button"), value="Network"), name="Network"))
        self.assertEqual(state.foldedInto, {})

    def test_legacy_projection_and_composite_caption_are_not_reinterpreted(self):
        root = n(0, "heading", n(1, "staticText", value="Network"), name="Network")
        self.assertEqual(projectTree(root)["foldedInto"], {})
        state = FixtureProjection(
            n(0, "heading", n(1, "staticText", value="Network"), n(2, "staticText", value="Other")),
            dict(nativeFacts=True),
        )
        state.edit.adopt(
            state.view,
            [
                dict(sourceRef="1", attribute="value", text="Network"),
                dict(sourceRef="2", attribute="value", text="Other"),
            ],
            "name",
        )
        merge_repeated_text(state, state.edit, state.view, Scope())
        self.assertEqual(state.view["name"], "Network, Other")
        self.assertEqual(state.foldedInto, {})

    def test_numeric_levels_and_binary_display_values_do_not_consume_text_labels(self):
        for role, value, extra in (
            ("heading", "6", {"valueIsNumber": True}),
            ("checkBox", "1", {}),
            ("switch", "true", {}),
            ("radioButton", "0", {}),
        ):
            with self.subTest(role=role):
                state = self.project(n(0, role, n(1, "staticText", value=value), name="Label", value=value, **extra))
                self.assertEqual(state.foldedInto, {})


class FullTextAccountingTests(unittest.TestCase):
    def test_repeated_pass_runs_on_normalized_facts_and_keeps_all_native_targets(self):
        heading = web("heading", "AXHeading", AXTitle="Network", AXValue=6)
        label = web("label", "AXStaticText", AXValue="Network", **{"settable.AXFocused": False})
        result = observe([heading, label], [edge("heading", "label")])
        self.assertEqual(set(displayed(result)), {"0"})
        self.assertIn("heading Network, Value: 6", result["text"])
        self.assertIn("Secondary Actions [elements 0, 1]: Show menu; Scroll to visible", result["text"])
        self.assertEqual(result["refs"], {"0": "heading", "1": "label"})
        facts = result["projections"][0]["factAccounting"]["values"]
        value = next(f for f in facts if f["ref"] == "1" and f["property"] == "control_value.value")
        self.assertEqual((value["kind"], value["target"]), ("equivalent", "0"))

    def test_link_keeps_its_role_url_action_and_unrelated_child(self):
        link = node("link", AXRole="AXLink", AXTitle="Help", AXURL="https://example.org/help?q=1#section")
        link["actions"]["items"] = [dict(name="AXPress", description="Press")]
        result = observe(
            [
                link,
                node("text", AXRole="AXStaticText", AXValue="Help"),
                node("button", AXRole="AXButton", AXTitle="Copy"),
            ],
            [edge("link", "text", "button")],
        )
        self.assertEqual(set(displayed(result)), {"0", "2"})
        self.assertEqual(displayed(result)["0"]["role"], "link")
        self.assertIn("https://example.org/help?q=1#section", result["text"])
        self.assertEqual(result["refs"]["1"], "text")

    def test_unknown_or_truncated_field_and_shared_label_are_not_consumed(self):
        for variant in ("unknown", "truncated", "action-unknown", "shared"):
            with self.subTest(variant=variant):
                label = node("label", AXRole="AXStaticText", AXValue="Network")
                edges = [edge("heading", "label")]
                if variant == "unknown":
                    label["values"]["VendorField"] = "x"
                    label["requestedFields"].append("VendorField")
                if variant == "truncated":
                    label["fieldStatus"]["AXValue"] = dict(state="truncated", totalUnits=4000, textRef="t")
                if variant == "action-unknown":
                    label["actions"] = dict(state="partial", items=[], total=1)
                if variant == "shared":
                    edges += [edge("label", "a", "b", property="AXServesAsTitleForUIElements")]
                nodes = [node("heading", AXRole="AXHeading", AXTitle="Network"), label]
                if variant == "shared":
                    nodes += [node("a", AXRole="AXButton"), node("b", AXRole="AXButton")]
                result = observe(nodes, edges)
                self.assertIn("1", displayed(result))

    def test_value_aliases_survive_multiple_nested_folds_and_platform_normalization(self):
        for platform, role_key, role, value, children in (
            ("macos", "AXRole", "AXStaticText", "AXValue", "AXChildren"),
            ("windows", "ControlType", "ControlType.Text", "Value.Value", "RawViewChildren"),
            ("linux", "role", "label", "Text.text", "children"),
        ):
            result = observe(
                [node(ref, **{role_key: role, value: "Network"}) for ref in ("owner", "middle", "leaf")],
                [edge("owner", "middle", property=children), edge("middle", "leaf", property=children)],
                platform,
            )
            self.assertEqual(set(displayed(result)), {"0"})
            self.assertEqual(result["projections"][0]["projection"]["foldedInto"], {"2": "0", "1": "0"})
            values = [
                f
                for f in result["projections"][0]["factAccounting"]["values"]
                if f["property"] == ("text.content" if platform == "linux" else "control_value.value")
            ]
            self.assertEqual([(v["kind"], v["target"]) for v in values], [("equivalent", "0")] * 3)


class AdjacentTextLayoutTests(unittest.TestCase):
    @staticmethod
    def project(children, **fields):
        return projectTree(n(0, "group", *children, name="Card", **fields), dict(nativeFacts=True))["roots"]

    def pack(self, roots, lines, focused=None):
        packed = compactTextParts(lines, roots, focused, field)
        self.assertEqual(expandTextParts(packed, field), lines)
        return packed

    def test_projected_siblings_pack_without_concatenating_values_or_refs(self):
        roots = self.project(
            [n(1, "group", n(2, "staticText", value="Title")), n(3, "group", n(4, "staticText", value="Details"))]
        )
        lines = ["0 container Card", "\t2 text Value: Title", "\t4 text Value: Details"]
        packed = self.pack(roots, lines)
        self.assertEqual(packed, ["0 container Card", '\tText parts [text]: 2 "Title", 4 "Details"'])
        self.assertEqual([c["value"] for c in roots[0]["children"]], ["Title", "Details"])

    def test_meaningful_wrappers_selection_actions_and_unknowns_break_runs(self):
        for props in ({"subrole": "paragraph"}, {"preserveGroup": True}, {"partialReason": "pending"}):
            roots = self.project([n(1, "staticText", value="Title"), n(2, "staticText", value="Details")], **props)
            lines = ["0 container Card", "\t1 text Value: Title", "\t2 text Value: Details"]
            self.assertEqual(self.pack(roots, lines), lines)
        for props in (
            {"focused": True},
            {"selected": True},
            {"labelTargets": ["9"]},
            {"actions": [dict(name="Connect")]},
            {"preserveGroup": True},
        ):
            roots = self.project([n(1, "staticText", value="Title"), n(2, "staticText", value="Details", **props)])
            lines = ["0 container Card", "\t1 text Value: Title", "\t2 text Value: Details"]
            self.assertEqual(self.pack(roots, lines), lines)
        roots = self.project(
            [
                n(1, "group", n(2, "staticText", value="A"), name="First"),
                n(3, "group", n(4, "staticText", value="B"), name="Second"),
            ]
        )
        lines = [
            "0 container Card",
            "\t1 container First",
            "\t\t2 text Value: A",
            "\t3 container Second",
            "\t\t4 text Value: B",
        ]
        self.assertEqual(self.pack(roots, lines), lines)

    def test_quoted_whitespace_and_unicode_parts_round_trip_exactly(self):
        values = [" ", 'a"b', "c\\d", " Wi-Fi ", "👨‍👩‍👧‍👦"]
        roots = self.project([n(i, "staticText", value=v) for i, v in enumerate(values, 1)])
        lines = ["0 container Card"] + ["\t" + str(i) + " text Value: " + field(v) for i, v in enumerate(values, 1)]
        self.pack(roots, lines)

    def test_web_regions_pack_only_passive_contiguous_fragments(self):
        for role in ("webArea", "scrollArea"):
            root = n(
                0,
                role,
                n(1, "staticText", value="Used"),
                n(2, "staticText", value="1 MB"),
                n(3, "button", name="Refresh"),
                n(4, "staticText", value="Total"),
                n(5, "staticText", value="2 MB"),
            )
            roots = projectTree(root, dict(nativeFacts=True))["roots"]
            lines = [
                "0 region",
                "\t1 text Value: Used",
                "\t2 text Value: 1 MB",
                "\t3 button Refresh",
                "\t4 text Value: Total",
                "\t5 text Value: 2 MB",
            ]
            packed = self.pack(roots, lines)
            self.assertEqual(len(packed), 4)
            self.assertEqual(packed[2], "\t3 button Refresh")
            self.assertEqual(self.pack(roots, lines, focused="0"), lines)
        root["capabilities"] = dict(typeText="available")
        roots = [FixtureProjection(root, dict(nativeFacts=True)).view]
        self.assertEqual(self.pack(roots, lines), lines)


class ActionLayoutTests(unittest.TestCase):
    def test_identical_secondary_actions_keep_every_original_target(self):
        action = dict(name="AXShowMenu", kind="show_menu", description="Show menu")
        owner = n(
            0, "button", actions=[dict(name="AXPress", kind="invoke"), action], capabilities=dict(click="available")
        )
        parts = [n(i, "staticText", actions=[action]) for i in (1, 2)]
        self.assertEqual(action_fields(owner, parts, renderedActions), ["Secondary Actions [elements 0–2]: Show menu"])
        self.assertEqual(owner["actions"][0]["name"], "AXPress")
        self.assertNotIn("AXPress", [a["name"] for p in parts for a in p["actions"]])

    def test_equal_labels_different_native_names_are_not_grouped(self):
        owner = n(0, "heading", actions=[dict(name="MenuA", description="Menu")])
        part = n(1, "staticText", actions=[dict(name="MenuB", description="Menu")])
        self.assertEqual(action_fields(owner, [part], renderedActions), ["Secondary Actions: Menu; Menu [element 1]"])

    def test_distinct_or_ambiguous_lists_keep_their_own_names_and_refs(self):
        owner = n(0, "heading", actions=[dict(name="AXShowMenu", description="Menu")])
        part = n(
            1,
            "staticText",
            actions=[dict(name="AXShowMenu", description="Menu"), dict(name="AXOther", description="Menu")],
        )
        fields = action_fields(owner, [part], renderedActions)
        self.assertEqual(
            fields,
            ['Secondary Actions: Menu; Menu [name: "AXShowMenu"] [element 1]; Menu [name: "AXOther"] [element 1]'],
        )


if __name__ == "__main__":
    unittest.main()
