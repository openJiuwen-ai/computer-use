"""Compact presentation conserves content, boundaries and native routes."""

import copy
import unittest
from unittest.mock import patch

from observation_fixture import normalize_fixture as normalizeFacts
from sdk.interaction.read.facts.test_native_facts import batch, edge, node
from sdk.interaction.read.presentation.accounting.test_relation_accounting import relation_records
from sdk.interaction.read.presentation.test_full_presentation import displayed, web

from computer_use.interaction.read.numbering.references import DisplayReferences


def present(nodes, relations=(), *, platform="macos"):
    raw = batch(nodes, relations)
    raw["platform"] = platform
    before = copy.deepcopy(raw)
    snapshot = normalizeFacts(raw, DisplayReferences("session"), driver_features=("nativeActionIntents",))
    facts_before = copy.deepcopy(snapshot.facts)
    output = snapshot.render(compact=True, trace=True)
    assert raw == before
    assert snapshot.facts == facts_before
    assert output["refs"] == snapshot.refs
    return snapshot, output


def nested_groups(inner=None, outer=None, *, table=False):
    return [
        node("w", AXRole="AXTable" if table else "AXWindow"),
        outer or web("outer"),
        inner or web("inner"),
        node("title", AXRole="AXStaticText", AXValue="Create workspace"),
        node("description", AXRole="AXStaticText", AXValue="Choose a local folder."),
        node("button", AXRole="AXButton", AXTitle="Create"),
        node("other", AXRole="AXButton", AXTitle="Open"),
    ], [edge("w", "outer"), edge("outer", "inner", "other"), edge("inner", "title", "description", "button")]


class CompactObservationTests(unittest.TestCase):
    def test_provider_cr_paragraphs_are_displayed_without_rewriting_the_source(self):
        for ending in ("\r", "\r\n", "\n"):
            text = f"第一行{ending}第二行 😀{ending}"
            source = node("body", ControlType="ControlType.Document", **{"Text.DocumentRange": text})
            snapshot, output = present([source], platform="windows")
            self.assertIn("| 第一行\n", output["text"])
            self.assertIn("| 第二行 😀\n", output["text"])
            self.assertNotIn("\\r", output["text"])
            self.assertEqual(snapshot.native.nodes["body"].text.content, text)

    def test_uia_toggle_state_is_not_a_redundant_wrapper_or_caption(self):
        for role in ("ControlType.Group", "ControlType.Text"):
            _, output = present(
                [
                    node("window", ControlType="ControlType.Window"),
                    node("button", ControlType="ControlType.Button", Name="Setting"),
                    node(
                        "state",
                        **{"ControlType": role, "Name": "Setting", "Toggle.ToggleState": "Off", "Value.Value": ""},
                    ),
                ],
                [
                    edge("window", "button", property="RawViewChildren"),
                    edge("button", "state", property="RawViewChildren"),
                ],
                platform="windows",
            )
            self.assertIn("2", displayed(output))
            self.assertIn("(off)", output["text"])
            self.assertEqual(displayed(output)["2"]["toggleState"], "off")

    def test_equivalent_anonymous_groups_splice_in_order_and_keep_original_relations(self):
        nodes, relations = nested_groups()
        snapshot, output = present(nodes, relations)
        self.assertNotIn("2", displayed(output))
        self.assertEqual([c["ref"] for c in displayed(output)["0"]["children"]], ["3", "4", "5", "6"])
        self.assertEqual(output["refs"]["2"], "inner")
        self.assertEqual(
            relation_records({**output, "text": output["details"]})[("2", "children")]["targets"], ["3", "4", "5"]
        )
        self.assertIn("Create workspace", output["text"])
        self.assertIn("Choose a local folder.", output["text"])
        self.assertNotIn("Text parts", output["text"])
        stages = output["projections"][0]["projection"]["stages"]
        self.assertTrue(next(s for s in stages if s["name"] == "flatten_redundant_hierarchy")["changes"])

    def test_meaningful_groups_are_boundaries_even_if_their_children_are_identical(self):
        variants = [
            dict(AXTitle="Card"),
            dict(AXDescription="Scope"),
            dict(AXValue="Status"),
            dict(AXRoleDescription="相册"),
            dict(AXIdentifier="card-scope"),
            dict(AXSelected=True),
            dict(AXFocused=True),
            dict(AXModal=True),
            dict(AXEditable=True),
            dict(VendorState="unknown semantics"),
            dict(AXSelectedTextRange=dict(location=1, length=0)),
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                _, output = present(*nested_groups(inner=web("inner", **variant)))
                self.assertIn("2", displayed(output))

    def test_hidden_layout_scope_survives_both_splicing_and_single_child_pruning(self):
        for children in (("title",), ("title", "button")):
            for visibility in (dict(visible=False, showing=False), dict(visible=False), dict(showing=False)):
                with self.subTest(children=children, visibility=visibility):
                    _, output = present(
                        [
                            node("w", role="window"),
                            node("outer", role="panel"),
                            node("inner", role="panel", **visibility),
                            node("title", role="label", name="Hidden title"),
                            *([node("button", role="push button", name="Hidden action")] if len(children) > 1 else []),
                            node("other", role="push button", name="Visible action"),
                        ],
                        [
                            edge("w", "outer", property="children"),
                            edge("outer", "inner", "other", property="children"),
                            edge("inner", *children, property="children"),
                        ],
                        platform="linux",
                    )
                    view = displayed(output)
                    self.assertIn("2", view)
                    self.assertEqual(view["2"]["children"][0]["name"], "Hidden title")
                    if len(visibility) == 2:
                        self.assertIn("2 container (hidden)", output["text"])

    def test_described_or_identified_single_child_wrapper_is_preserved(self):
        for attributes in (dict(AXRoleDescription="相册"), dict(AXIdentifier="card-scope")):
            _, output = present(
                [
                    node("w", AXRole="AXWindow"),
                    web("group", **attributes),
                    node("title", AXRole="AXStaticText", AXValue="Title"),
                ],
                [edge("w", "group"), edge("group", "title")],
            )
            self.assertIn("1", displayed(output))

    def test_independent_actions_input_and_missing_children_block_splicing(self):
        action = web("inner")
        action["actions"]["items"].append(dict(name="AXPress", description="Press"))
        _, output = present(*nested_groups(inner=action))
        self.assertIn("2", displayed(output))
        nodes, relations = nested_groups()
        relations[-1].update(state="partial", total=5)
        _, output = present(nodes, relations)
        self.assertIn("2", displayed(output))
        self.assertIn("partial", output["text"])
        _, output = present(*nested_groups(table=True))
        self.assertIn("2", displayed(output))
        for role in ("AXTextField", "AXScrollArea", "AXList", "AXRow", "AXSheet"):
            _, output = present(*nested_groups(inner=web("inner", role)))
            self.assertIn("2", displayed(output))

    def test_supporting_actions_stay_executable_on_the_original_node_in_details(self):
        button = web("button", "AXButton", AXTitle="Save")
        button["actions"]["items"].extend(
            [dict(name="AXPress", description="Press"), dict(name="CustomDelete", description="Delete")]
        )
        snapshot, output = present([button])
        self.assertNotIn("AXScrollToVisible", output["text"])
        self.assertNotIn("Show menu", output["text"])
        self.assertNotIn("clickable", output["text"])
        self.assertIn("Secondary Actions: Delete", output["text"])
        self.assertIn("AXScrollToVisible", output["details"])
        self.assertEqual(
            output["projections"][0]["projection"]["nativeNodes"]["0"]["actions"], snapshot.roots[0]["actions"]
        )
        self.assertEqual(
            [
                {"name": item["key"], "description": item.get("description", "")}
                for item in snapshot.facts["nodes"][0]["actions"]["items"]
                if item.get("key")
            ],
            button["actions"]["items"],
        )

    def test_actionable_nonstandard_roles_still_announce_clickability(self):
        source = web("custom")
        source["actions"]["items"].append(dict(name="AXPress", description="Press"))
        _, output = present([source])
        self.assertIn("(clickable", output["text"])

    def test_button_role_does_not_hide_unknown_click_capability(self):
        _, output = present([node("button", AXRole="AXButton", AXTitle="Save")])
        self.assertIn("Click: unknown", output["text"])

    def test_text_remains_exact_and_two_independent_buttons_are_not_merged(self):
        _, output = present(
            [
                node("w", AXRole="AXWindow"),
                node("a", AXRole="AXStaticText", AXValue="  leading\nline"),
                node("b", AXRole="AXButton", AXTitle="First"),
                node("c", AXRole="AXButton", AXTitle="Second"),
            ],
            [edge("w", "a", "b", "c")],
        )
        self.assertIn('"  leading"', output["text"])
        self.assertEqual(set(displayed(output)), {"0", "1", "2", "3"})

    def test_empty_images_are_observable_even_without_descriptions(self):
        _, output = present([node("w", AXRole="AXWindow"), web("image", "AXImage")], [edge("w", "image")])
        self.assertIn("1", displayed(output))

    def test_urls_use_marked_previews_and_details_keep_every_character(self):
        for value in ("data:image/png;base64," + "ABcd" * 1000, "https://example.org/a?secret=" + "x" * 400 + "#part"):
            with self.subTest(scheme=value.split(":")[0]):
                _, output = present([node("image", AXRole="AXImage", AXURL=value)])
                self.assertLess(len(output["text"]), 260)
                self.assertIn("see details", output["text"])
                self.assertIn(value, output["details"])
        value = "https://example.org/?a=1#part"
        _, output = present([node("link", AXRole="AXLink", AXURL=value)])
        self.assertIn(value, output["text"])

    def test_editor_caret_selection_and_modal_state_remain_in_primary(self):
        _, output = present(
            [
                web("field", "AXTextField", AXFocused=True, AXSelectedText="Selected", AXValue="abc"),
                node("sheet", AXRole="AXSheet", AXModal=True),
            ]
        )
        for expected in ("Selection: 0:0", "reports focus", "Selected text: Selected", "(modal)"):
            self.assertIn(expected, output["text"])
        _, output = present([web("page", "AXWebArea", AXFocused=True)])
        self.assertNotIn("Selection: 0:0", output["text"])
        self.assertIn("text.selections", output["details"])

    def test_failures_and_conflicting_relations_are_not_hidden_as_default_metadata(self):
        source = web("unknown")
        source["values"].pop("AXTitle")
        source["fieldStatus"]["AXTitle"] = dict(state="timeout")
        _, output = present([source, web("child")], [edge("unknown", "child", certainty="conflicting")])
        self.assertIn("name (timeout)", output["text"])
        self.assertIn("conflict", output["text"])

    def test_role_and_all_value_action_relation_records_stay_accounted_across_platforms(self):
        for platform, role, name in (
            ("macos", ("AXRole", "AXButton"), "AXTitle"),
            ("windows", ("ControlType", "ControlType.Button"), "Name"),
            ("linux", ("role", "push button"), "name"),
        ):
            source = node("button", **{role[0]: role[1], name: "Apply"})
            snapshot, output = present([source], platform=platform)
            accounting = output["projections"][0]["factAccounting"]
            self.assertEqual({e["property"] for e in accounting["values"]}, set(snapshot.facts["nodes"][0]["values"]))
            self.assertEqual([a["ref"] for a in accounting["actions"]], ["0"])
            self.assertEqual(output["relationSources"], [])
            self.assertIn("Apply", output["text"])

    def test_projection_failure_is_a_full_uncompressed_primary_not_an_empty_tree(self):
        snapshot, _ = present([node("button", AXRole="AXButton", AXTitle="Apply")])
        with patch(
            "computer_use.interaction.read.presentation.ir.validate_projection",
            side_effect=ValueError("bad projection"),
        ):
            output = snapshot.render(compact=True)
        self.assertEqual(output["projections"][0]["presentation"], "uncompressed")
        self.assertIn("Apply", output["text"])
        self.assertIn("bad projection", output["text"])

    def test_unexpanded_menu_summary_keeps_coverage_and_a_public_read_route(self):
        wire = batch(
            [node("bar", AXRole="AXMenuBar"), node("file", AXRole="AXMenuBarItem", AXTitle="File")],
            [edge("bar", "file"), edge("file", state="partial", offset=0, total=1, cursor="native-page")],
        )
        snapshot = normalizeFacts(wire, DisplayReferences("session"), driver_features=("nativeNodePages",))
        result = snapshot.render(compact=True)
        self.assertIn("Menu contents not expanded: 1 (0/1 children)", result["text"])
        self.assertIn("app.element(ref).read()", result["text"])
        self.assertNotIn("Children: native children offset", result["text"])
        self.assertIn("native-page", result["details"])
        self.assertIn("Click: unknown", result["text"])

    def test_partial_page_keeps_driver_explanation_even_without_an_error(self):
        wire = batch(
            [node("table", AXRole="AXTable")],
            [
                edge(
                    "table",
                    property="AXRows",
                    state="partial",
                    total=10,
                    offset=5,
                    detail="Live coordinate page; values can change.",
                )
            ],
        )
        result = normalizeFacts(wire, DisplayReferences("session")).render(compact=True)
        self.assertIn("Live coordinate page; values can change.", result["text"])

    def test_unexpanded_menu_without_retained_cursor_shares_coverage_and_read_entry(self):
        from computer_use.interaction.read.presentation.preparation import prepareSnapshot

        explanation = "native relation not expanded; read its source to continue"
        wire = batch(
            [
                node("bar", AXRole="AXMenuBar"),
                node("file", AXRole="AXMenuBarItem", AXTitle="File"),
                node("edit", AXRole="AXMenuBarItem", AXTitle="Edit"),
            ],
            [
                edge("bar", "file", "edit"),
                edge("file", state="partial", offset=0, total=1, detail=explanation),
                edge("edit", state="partial", offset=0, total=1, detail=explanation),
            ],
        )
        before = copy.deepcopy(wire)
        snapshot = normalizeFacts(wire, DisplayReferences("session"), driver_features=("nativeNodePages",))
        result = snapshot.render(compact=True)
        self.assertIn("Menu contents not expanded: 1, 2 (0/1 children)", result["text"])
        self.assertIn("app.element(ref).read()", result["text"])
        self.assertNotIn("Children:", result["text"])
        self.assertNotIn("Observation notes", result["text"])
        self.assertNotIn("Collection:", result["text"])
        self.assertIn(explanation, result["details"])
        self.assertEqual(prepareSnapshot(snapshot, compact=True).inline_child_refs, ("1", "2"))
        self.assertNotIn("nextChildOffset", snapshot.roots[0]["children"][0])
        self.assertEqual(wire, before)
        self.assertEqual(result["refs"], snapshot.refs)

    def test_unexpanded_menu_does_not_hide_provider_failures_or_other_relations(self):
        explanation = "native relation not expanded; read its source to continue"
        item = node("file", AXRole="AXMenuBarItem", AXTitle="File")
        item["requestedFields"].append("AXHelp")
        item["fieldStatus"]["AXHelp"] = dict(state="error", detail="provider failed")
        wire = batch(
            [node("bar", AXRole="AXMenuBar"), item],
            [
                edge("bar", "file"),
                edge("file", state="partial", offset=0, total=1, detail=explanation),
                edge(
                    "file",
                    property="AXServesAsTitleForUIElements",
                    state="error",
                    certainty="unknown",
                    detail="relation failed",
                ),
            ],
        )
        snapshot = normalizeFacts(wire, DisplayReferences("session"), driver_features=("nativeNodePages",))
        result = snapshot.render(compact=True)
        self.assertIn("Menu contents not expanded", result["text"])
        self.assertIn("provider failed", result["text"])
        self.assertIn("relation failed", result["text"])
        self.assertNotIn(explanation, result["text"])

    def test_unexpanded_coverage_does_not_suppress_real_retention_or_collection_gaps(self):
        for role, offset, detail in (
            ("AXMenuBarItem", 0, "native relation continuation retention budget; unread targets are not captured"),
            ("AXMenuBarItem", 1, "native relation has an unread continuation"),
            ("AXGroup", 0, "native relation not expanded; read its source to continue"),
        ):
            with self.subTest(role=role, offset=offset, detail=detail):
                wire = batch(
                    [node("source", AXRole=role)],
                    [edge("source", state="partial", offset=offset, total=2, detail=detail)],
                )
                result = normalizeFacts(
                    wire, DisplayReferences("session"), driver_features=("nativeNodePages",)
                ).render(compact=True)
                self.assertIn(detail, result["text"])
                if role != "AXMenuBarItem" or offset:
                    self.assertNotIn("Menu contents not expanded", result["text"])

    def test_uncollected_content_relations_have_target_references_and_a_read_route(self):
        for platform, role, properties in (
            ("macos", dict(AXRole="AXTextField"), ("AXTitleUIElement", "AXSelectedRows", "AXSelectedCells")),
            ("windows", dict(ControlType="ControlType.Edit"), ("LabeledBy", "Selection.Selection")),
            ("linux", dict(role="entry"), ("labelled-by", "Selection.selectedChildren")),
        ):
            for prop in properties:
                with self.subTest(platform=platform, property=prop):
                    wire = batch([node("field", **role)], [edge("field", "target", property=prop)])
                    wire.update(platform=platform, unread=["target"])
                    snapshot = normalizeFacts(wire, DisplayReferences("session"))
                    result = snapshot.render(compact=True)
                    public_kind = next(r.kind for r in snapshot.native.nodes["field"].relations.values())
                    self.assertIn("0: " + public_kind + " -> 1 (not collected;", result["text"])
                    self.assertIn("app.element(ref).read()", result["text"])
                    self.assertEqual(result["refs"]["1"], "target")
        wire = batch(
            [node("field", AXRole="AXTextField")],
            [edge("field", "parent", property="AXParent"), edge("field", "window", property="AXWindow")],
        )
        wire["unread"] = ["parent", "window"]
        result = normalizeFacts(wire, DisplayReferences("session")).render(compact=True)
        self.assertNotIn("not collected", result["text"])
        self.assertIn("Uncollected: 1, 2", result["details"])


if __name__ == "__main__":
    unittest.main()
