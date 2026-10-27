"""Full native-fact projection: compact controls without losing their sources."""

import copy
import unittest

from observation_fixture import normalize_fixture as normalizeFacts
from sdk.interaction.read.facts.test_native_facts import batch, edge, group_relation_sources, node
from sdk.interaction.read.presentation.accounting.test_relation_accounting import relation_records

from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.rendering.layout.chains import compactTreeChains, expandTreeChains


def web(ref, role="AXGroup", **values):
    source = node(
        ref,
        AXRole=role,
        AXTitle="",
        AXDescription="",
        AXValue="",
        AXFocused=False,
        AXSelected=False,
        AXDisclosureLevel=0,
        AXSelectedTextRange=dict(location=0, length=0),
        **{"settable.AXFocused": True, "settable.AXValue": False, "AXOrientation": "AXUnknownOrientation"},
    )
    source["values"].update(values)
    source["requestedFields"] = list(source["values"])
    source["actions"]["items"] = [
        dict(name="AXShowMenu", description="Show menu"),
        dict(name="AXScrollToVisible", description="Scroll to visible"),
    ]
    return source


def observe(nodes, relations=(), platform="macos"):
    raw = batch(nodes, relations)
    raw["platform"] = platform
    before = copy.deepcopy(raw)
    snapshot = normalizeFacts(raw, DisplayReferences("session"), driver_features=("nativeActionIntents",))
    result = snapshot.render()
    assert group_relation_sources(snapshot.facts) == group_relation_sources(before)
    assert set(result["refs"].values()) == {n["node"] for n in nodes}
    return result


def displayed(result):
    found = {}

    def visit(n):
        found[n["ref"]] = n
        for child in n["children"]:
            visit(child)

    for projection in result["projections"]:
        for root in projection["roots"]:
            visit(root)
    return found


class FullPresentationTests(unittest.TestCase):
    def test_web_wrappers_do_not_turn_metadata_into_visual_controls(self):
        a, b = web("a"), web("b")
        result = observe(
            [node("window", AXRole="AXWindow"), a, b, node("button", AXRole="AXButton", AXTitle="Save")],
            [edge("window", "a"), edge("a", "b"), edge("b", "button")],
        )
        self.assertEqual(set(displayed(result)), {"0", "3"})
        self.assertIn('1, 2 text.selections: [{"start":0,"end":0,"collapsed":true,"attributes":{}}]', result["text"])
        self.assertIn("AXScrollToVisible", result["text"])
        self.assertEqual(relation_records(result)[("1", "children")]["targets"], ["2"])

    def test_custom_role_and_role_detail_remain_structural_boundaries(self):
        for role, subrole in [("AXVendorRegion", None), ("AXGroup", "AXVendorPanel")]:
            with self.subTest(role=role, subrole=subrole):
                custom = web("custom", role, **({"AXSubrole": subrole} if subrole else {}))
                result = observe(
                    [node("window", AXRole="AXWindow"), custom, node("button", AXRole="AXButton")],
                    [edge("window", "custom"), edge("custom", "button")],
                )
                self.assertIn("1", displayed(result))
                self.assertIn(subrole or role, result["details"])
                self.assertEqual(result["refs"]["1"], "custom")
        # The standard unknown-role declaration has no extra provider meaning.
        result = observe(
            [node("window", AXRole="AXWindow"), web("layout", "AXUnknown"), node("button", AXRole="AXButton")],
            [edge("window", "layout"), edge("layout", "button")],
        )
        self.assertFalse(displayed(result)["1"]["native"].get("preserveGroup"))
        self.assertNotIn("provider.role", result["details"])

    def test_clickable_custom_control_gets_caption_but_action_sources_stay_distinct(self):
        button = web("button")
        button["actions"]["items"].insert(0, dict(name="AXPress", description="Press"))
        label = web("label", "AXStaticText", AXValue="Refresh", **{"settable.AXFocused": False})
        result = observe([button, label], [edge("button", "label")])
        self.assertIn("0 container (clickable) Refresh", result["text"])
        self.assertEqual(set(displayed(result)), {"0"})
        self.assertIn("Secondary Actions [elements 0, 1]: Show menu; Scroll to visible", result["text"])
        self.assertEqual(result["refs"], {"0": "button", "1": "label"})
        self.assertEqual(result["projections"][0]["projection"]["foldedInto"]["1"], "0")

    def test_native_outline_cell_folds_but_focus_coordinates_and_label_actions_survive(self):
        cell = node(
            "cell",
            AXRole="AXCell",
            AXFocused=True,
            AXSelected=True,
            AXRowIndexRange=dict(location=5, length=1),
            AXColumnIndexRange=dict(location=0, length=1),
            **{"settable.AXFocused": False},
        )
        label = node("label", AXRole="AXStaticText", AXValue="Wi-Fi", **{"settable.AXValue": False})
        label["actions"]["items"] = [dict(name="AXShowMenu", description="Show menu")]
        result = observe(
            [node("row", AXRole="AXRow", AXSelected=True), cell, label], [edge("row", "cell"), edge("cell", "label")]
        )
        self.assertEqual(set(displayed(result)), {"0"})
        self.assertIn("0 row (selected) Wi-Fi", result["text"])
        self.assertIn("1 states.focused: true", result["text"])
        self.assertIn("1 structure.row_index: 5", result["text"])
        self.assertIn("1 structure.row_span: 1", result["text"])
        self.assertIn("1 structure.column_index: 0", result["text"])
        self.assertIn("1 structure.column_span: 1", result["text"])
        self.assertIn("Show menu [element 2]", result["text"])
        self.assertNotIn("clickable", result["text"])

    def test_tables_multicolumn_records_and_nested_buttons_are_not_flattened_into_labels(self):
        for table in (False, True):
            nodes = [node("owner", AXRole="AXTable" if table else "AXRow")]
            nodes += [
                node("a", AXRole="AXCell"),
                node("b", AXRole="AXCell"),
                node("text", AXRole="AXStaticText", AXValue="Name"),
                node("button", AXRole="AXButton", AXTitle="Delete"),
            ]
            result = observe(nodes, [edge("owner", "a", "b"), edge("a", "text"), edge("b", "button")])
            self.assertEqual(set(displayed(result)), {"0", "1", "2", "3", "4"})
            self.assertNotIn("Name", displayed(result)["0"].get("name", ""))

    def test_different_or_unknown_actions_values_and_states_keep_their_control(self):
        variants = [
            dict(AXTitle="Named region"),
            dict(AXValue="Independent content"),
            dict(AXSelected=True),
            dict(AXFocused=True),
            dict(AXEditable=True),
            dict(VendorState=False),
            {"settable.AXFocused": "true"},
            dict(AXSelectedTextRange=dict(location=2, length=1)),
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                wrapper = web("wrapper", **variant)
                result = observe(
                    [node("window", AXRole="AXWindow"), wrapper, node("button", AXRole="AXButton")],
                    [edge("window", "wrapper"), edge("wrapper", "button")],
                )
                self.assertIn("1", displayed(result))
        for action in ("AXPress", "AXScrollDown", "VendorOperation"):
            wrapper = web("wrapper")
            wrapper["actions"]["items"].append(dict(name=action, description=action))
            result = observe([wrapper, node("button", AXRole="AXButton")], [edge("wrapper", "button")])
            self.assertIn("0", displayed(result))

    def test_a_failed_incidental_range_is_visible_without_blocking_static_layout(self):
        wrapper = web("wrapper")
        wrapper["values"].pop("AXSelectedTextRange")
        wrapper["fieldStatus"]["AXSelectedTextRange"] = dict(state="error", detail="provider cannot read range")
        result = observe(
            [node("window", AXRole="AXWindow"), wrapper, node("button", AXRole="AXButton")],
            [edge("window", "wrapper"), edge("wrapper", "button")],
        )
        self.assertNotIn("1", displayed(result))
        self.assertIn("provider cannot read range", result["text"])
        for field in ("AXTitle", "AXValue"):
            protected = copy.deepcopy(wrapper)
            protected["values"].pop(field)
            protected["fieldStatus"][field] = dict(state="timeout")
            result = observe([protected, node("button", AXRole="AXButton")], [edge("wrapper", "button")])
            self.assertIn("0", displayed(result))

    def test_input_caret_modal_scope_and_incomplete_children_remain_explicit(self):
        field = web("input", "AXTextField", AXFocused=True)
        result = observe([field])
        self.assertIn("Selection: 0:0", result["text"].split("Additional native facts")[0])
        modal = web("modal", AXModal=True)
        result = observe(
            [node("w", AXRole="AXWindow"), modal, node("b", AXRole="AXButton")],
            [edge("w", "modal"), edge("modal", "b", state="partial", total=2)],
        )
        self.assertIn("1", displayed(result))
        self.assertIn("partial", result["text"])
        self.assertIn("modal", result["text"])

    def test_complete_relation_maps_round_trip_offsets_duplicates_and_unknown_metadata(self):
        relations = [
            edge("a", "b", "c", "b", total=3, offset=0),
            edge("b", "a", property="AXParent", total=1, offset=0),
            edge(
                "c",
                "b",
                property="Vendor\nRelation",
                state="partial",
                certainty="unknown",
                total=4,
                offset=2,
                detail="x",
            ),
        ]
        result = observe([web("a"), web("b"), web("c")], relations)
        recovered = relation_records(result)
        self.assertEqual(recovered[("0", "children")]["targets"], ["1", "2", "1"])
        self.assertEqual(recovered[("0", "children")]["positions"], [0, 1, 2])
        self.assertEqual(recovered[("0", "children")]["total"], 3)
        vendor = recovered[("2", "provider.macos.Vendor\nRelation")]
        self.assertEqual(vendor["targets"], ["1"])
        self.assertEqual(vendor["positions"], [2])
        self.assertEqual(vendor["offset"], 2)
        self.assertEqual((vendor["state"], vendor["certainty"], vendor["total"]), ("partial", "unknown", 4))
        self.assertEqual(vendor["detail"], "x")

    def test_an_empty_partial_relation_does_not_claim_an_empty_complete_collection(self):
        result = observe(
            [web("a")],
            [
                edge(
                    "a",
                    property="VendorRelation",
                    state="partial",
                    certainty="unknown",
                    total=4,
                    offset=2,
                    detail="unread page",
                )
            ],
        )
        record = relation_records(result)[("0", "provider.macos.VendorRelation")]
        self.assertEqual(record["targets"], [])
        self.assertEqual(record["positions"], [])
        self.assertEqual(record["offset"], 2)
        self.assertEqual((record["state"], record["certainty"], record["total"]), ("partial", "unknown", 4))
        self.assertEqual(record["detail"], "unread page")

    def test_named_wrapper_chain_is_reversible_not_a_name_based_deletion(self):
        result = observe(
            [
                web("a", AXTitle="First"),
                web("b", AXTitle="Second"),
                node("t", AXRole="AXStaticText", AXValue="Content"),
            ],
            [edge("a", "b"), edge("b", "t")],
        )
        tree = result["projections"][0]
        text = tree["text"]
        self.assertIn("First", text)
        self.assertIn("Second", text)
        expanded = expandTreeChains(text.splitlines())
        self.assertEqual(compactTreeChains(expanded, tree["roots"]), text.splitlines())


if __name__ == "__main__":
    unittest.main()
