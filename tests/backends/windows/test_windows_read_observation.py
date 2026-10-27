import copy
import math
import unittest
from types import SimpleNamespace

from observation_fixture import facts_fixture as decodeFacts, from_records, normalize_fixture as normalizeFacts
from sdk.interaction.read.facts.test_native_facts import batch, edge, node

from computer_use.interaction.read.acquisition.reader import NativeReader
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.native.contracts import Binding
from computer_use.native.semantics import WINDOWS_ON_DEMAND_FIELDS, WINDOWS_READ_V2_FIELDS

"Windows raw-read contract and presentation, without pretending to run UIA."


def windows(nodes, relations=()):
    result = batch(nodes, relations)
    result["platform"] = "windows"
    return result


def selection(*items, state="complete", total=None):
    return dict(
        state=state,
        total=len(items) if total is None else total,
        ranges=list(items),
        coordinateSpace="screenPhysicalPixels",
    )


def text_range(content, degenerate=False, **statuses):
    return dict(values=dict(text=content, degenerate=degenerate, readOnly=False, bounds=[]), fieldStatus=statuses)


class WindowsReadObservationTests(unittest.TestCase):
    @staticmethod
    def layout_metadata():
        return dict(
            IsControlElement=False,
            IsContentElement=False,
            IsOffscreen=False,
            FrameworkId="WPF",
            ClassName="Layout",
            NativeWindowHandle=0,
            IsKeyboardFocusable=False,
            IsPassword=False,
            SupportedPatterns=[],
            BoundingRectangle=dict(x=10, y=20, width=400, height=300),
            **{
                "UIA.ReadSource": "native raw provider",
                "UIA.ReferenceScope": (
                    "observedRelation; input requires independent live identity and window validation"
                ),
                "UIA.Collection": dict(
                    view="raw", mayContainVirtualizedItems=False, isVirtualizedItem=False, logicalContentComplete=None
                ),
            },
        )

    def test_typed_metadata_folds_empty_layout_but_keeps_content_actions_and_native_targets(self):
        action = node("action", ControlType="ControlType.Button", Name="", IsEnabled=True)
        action["actions"]["items"] = [dict(name="Invoke.Invoke", description="Invoke.Invoke")]
        raw = windows(
            [
                node("window", ControlType="ControlType.Window"),
                node("layout", ControlType="ControlType.Group", Name="", **self.layout_metadata()),
                node("nested", ControlType="ControlType.Pane", Name="", **self.layout_metadata()),
                node(
                    "text",
                    ControlType="ControlType.Text",
                    Name="Raw-only content stays",
                    IsControlElement=False,
                    IsContentElement=False,
                    IsOffscreen=True,
                ),
                action,
            ],
            [
                edge("window", "layout", property="RawViewChildren"),
                edge("layout", "nested", property="RawViewChildren"),
                edge("nested", "text", "action", property="RawViewChildren"),
            ],
        )
        snapshot, rendered = self.render(raw)
        visible = {ref for part in rendered["projections"] for ref in part["refs"]}
        self.assertNotIn("1", visible)
        self.assertNotIn("2", visible)
        self.assertIn("Raw-only content stays", rendered["text"])
        self.assertIn("4", visible)
        self.assertEqual(rendered["refs"]["4"], "action")
        self.assertIn("Invoke.Invoke", rendered["details"])
        self.assertNotIn(
            "FrameworkId", rendered["details"]
        )  # Runtime implementation metadata is deliberately excluded.
        self.assertEqual(set(rendered["refs"].values()), {n["node"] for n in raw["nodes"]})

    def test_unknown_malformed_or_incomplete_layout_facts_remain_boundaries(self):
        changes = [
            {"IsControlElement": "false"},
            {"SupportedPatterns": ["FuturePattern"]},
            {"BoundingRectangle": dict(x=0, y=0, width=-1, height=20)},
            {"VendorState": dict(unknown=True)},
            {"IsEnabled": False},
            {"HasKeyboardFocus": True},
            {"Window.IsModal": True},
            {"ItemStatus": "Needs attention"},
            {
                "UIA.Collection": dict(
                    view="raw", mayContainVirtualizedItems=True, isVirtualizedItem=False, logicalContentComplete=None
                )
            },
        ]
        for change in changes:
            with self.subTest(change=change):
                values = dict(ControlType="ControlType.Group", Name="", **self.layout_metadata()) | change
                raw = windows(
                    [
                        node("window", ControlType="ControlType.Window"),
                        node("layout", **values),
                        node("text", ControlType="ControlType.Text", Name="Status"),
                    ],
                    [
                        edge("window", "layout", property="RawViewChildren"),
                        edge("layout", "text", property="RawViewChildren"),
                    ],
                )
                _, rendered = self.render(raw)
                self.assertIn("1", {ref for part in rendered["projections"] for ref in part["refs"]})
        raw["relations"][1]["state"] = "partial"
        raw["nodes"][1] = node("layout", ControlType="ControlType.Group", **self.layout_metadata())
        _, rendered = self.render(raw)
        self.assertIn("observed children partly collected", rendered["text"])

    def test_body_value_and_range_sources_have_separate_display_and_accounting(self):
        raw = windows(
            [
                node(
                    "document",
                    ControlType="ControlType.Document",
                    **{
                        "Text.DocumentRange": "Full body\nsecond paragraph",
                        "Value.Value": "status",
                        "RangeValue.Value": 0,
                    },
                )
            ]
        )
        snapshot, rendered = self.render(raw)
        self.assertIn("Document text:", rendered["text"])
        self.assertIn("Full body", rendered["text"])
        self.assertIn("Control value: status", rendered["text"])
        self.assertIn("Range value: 0", rendered["text"])
        destinations = {
            item["property"]: item["kind"]
            for part in rendered["projections"]
            for item in part["factAccounting"]["values"]
        }
        self.assertEqual(destinations["text.content"], "display")
        self.assertEqual(destinations["control_value.value"], "display")
        self.assertEqual(snapshot.value_fields["0"], "text.content")

    def test_equal_complete_sources_share_display_but_equal_truncated_previews_do_not(self):
        raw = windows(
            [
                node(
                    "document",
                    ControlType="ControlType.Document",
                    **{"Text.DocumentRange": "same text", "Value.Value": "same text"},
                )
            ]
        )
        _, rendered = self.render(raw)
        self.assertNotIn("Control value:", rendered["text"])
        raw["nodes"][0]["fieldStatus"]["Value.Value"] = dict(state="truncated", totalUnits=7000, detail="capture limit")
        _, rendered = self.render(raw)
        self.assertIn("Control value: same text", rendered["text"])
        self.assertIn("control_value.value (partial)", rendered["text"])

    def test_secondary_document_text_on_an_empty_value_layout_cannot_be_pruned(self):
        raw = windows(
            [
                node("window", ControlType="ControlType.Window"),
                node(
                    "layout",
                    ControlType="ControlType.Pane",
                    **self.layout_metadata(),
                    **{"Value.Value": "", "Text.DocumentRange": "Independent body content"},
                ),
            ],
            [edge("window", "layout", property="RawViewChildren")],
        )
        _, rendered = self.render(raw)
        self.assertIn("Independent body content", rendered["text"])
        self.assertIn("1", {ref for part in rendered["projections"] for ref in part["refs"]})

    def test_malformed_document_text_is_not_a_number_or_a_control_value_fallback(self):
        for value in (None, 7, False, {"unknown": "text"}):
            with self.subTest(value=value):
                raw = windows(
                    [
                        node(
                            "document",
                            ControlType="ControlType.Document",
                            **{"Text.DocumentRange": value, "Value.Value": "control"},
                        )
                    ]
                )
                snapshot, rendered = self.render(raw)
                self.assertNotIn("value", snapshot.roots[0])
                self.assertNotIn("0", snapshot.value_fields)
                self.assertIn("Document text of 0: " + ("absent" if value is None else "error"), rendered["text"])
                self.assertIsNone(snapshot.native.nodes["document"].text.content)
                self.assertIn("Control value: control", rendered["text"])

    def test_matching_numeric_and_text_values_keep_their_different_sources(self):
        raw = windows(
            [
                node(
                    "document",
                    ControlType="ControlType.Document",
                    **{"Text.DocumentRange": "0", "Value.Value": "0", "RangeValue.Value": 0},
                )
            ]
        )
        _, rendered = self.render(raw)
        self.assertIn("Range value: 0", rendered["text"])
        self.assertNotIn("Control value:", rendered["text"])

    def test_unlabeled_action_leaf_keeps_its_bounds_visible_without_inventing_clickability(self):
        action = node(
            "confirm",
            ControlType="ControlType.Custom",
            Name="",
            IsEnabled=True,
            IsOffscreen=False,
            BoundingRectangle=dict(x=-150, y=480, width=130, height=48),
        )
        action["actions"]["items"] = [dict(name="Invoke.Invoke", description="Invoke.Invoke")]
        raw = windows(
            [node("window", ControlType="ControlType.Window"), action],
            [edge("window", "confirm", property="RawViewChildren"), edge("confirm", property="RawViewChildren")],
        )
        snapshot, rendered = self.render(raw)
        self.assertIn("1=(-150, 480, 130, 48)", rendered["text"])
        self.assertIn("physical screen pixels", rendered["text"])
        self.assertIn("not App PNG coordinates", rendered["text"])
        self.assertIn("do not prove a point is clickable", rendered["text"])
        self.assertEqual(snapshot.native, from_records(raw))

    def test_unknown_or_failed_geometry_and_partial_children_do_not_become_a_visual_map(self):
        for change in ("offscreen", "unknown-name", "failed-bounds", "partial-children", "zero-size"):
            with self.subTest(change=change):
                action = node(
                    "confirm",
                    ControlType="ControlType.Button",
                    Name="",
                    IsEnabled=True,
                    IsOffscreen=False,
                    BoundingRectangle=dict(x=10, y=20, width=100, height=40),
                )
                action["actions"]["items"] = [dict(name="Invoke.Invoke", description="Invoke.Invoke")]
                relation = edge("confirm", property="RawViewChildren")
                if change == "offscreen":
                    action["values"]["IsOffscreen"] = True
                elif change == "unknown-name":
                    action["values"].pop("Name")
                    action["fieldStatus"]["Name"] = dict(state="error", detail="unavailable")
                elif change == "failed-bounds":
                    action["values"].pop("BoundingRectangle")
                    action["fieldStatus"]["BoundingRectangle"] = dict(state="error", detail="unavailable")
                elif change == "partial-children":
                    relation["state"] = "partial"
                else:
                    action["values"]["BoundingRectangle"]["width"] = 0
                _, rendered = self.render(windows([action], [relation]))
                self.assertNotIn("Unlabeled element bounds", rendered["text"])

    def test_nonfinite_uia_numbers_preserve_type_without_invalid_json_or_zero_defaults(self):
        for value in ("NaN", "+Infinity", "-Infinity"):
            raw = dict(nativeType="UIA.Double", value=value)
            source = node(
                "slider", ControlType="ControlType.Slider", **{"RangeValue.Value": raw, "RangeValue.LargeChange": raw}
            )
            snapshot, text = self.render(windows([source]))
            self.assertIn(value.removeprefix("+"), text["text"])
            actual = snapshot.native.nodes["slider"].numeric_value.large_step
            self.assertTrue(math.isnan(actual) if value == "NaN" else actual == float(value))
            self.assertEqual(source["values"]["RangeValue.LargeChange"], raw)

    def render(self, raw, compact=True):
        original = copy.deepcopy(raw)
        decoded = decodeFacts(raw)
        snapshot = normalizeFacts(decoded, DisplayReferences("session"), driver_features=("nativeUiaReadV2",))
        result = snapshot.render(compact=compact)
        self.assertEqual(raw, original)
        self.assertEqual(snapshot.native, from_records(original))
        return snapshot, result

    def test_selection_does_not_infer_focus_offsets_or_caret_activity(self):
        raw = windows(
            [
                node(
                    "input",
                    ControlType="ControlType.Edit",
                    Name="Search",
                    HasKeyboardFocus=False,
                    **{"Text.DocumentRange": "beta beta", "Text.Selection": selection(text_range("beta"))},
                )
            ]
        )
        snapshot, rendered = self.render(raw)
        self.assertIn("Text selection of 0 (complete; 1/1 ranges)", rendered["text"])
        self.assertIn('"beta"', rendered["text"])
        self.assertFalse(snapshot.roots[0]["focused"])
        self.assertNotIn("selectionRange", snapshot.roots[0])
        self.assertNotIn("caretOffset", snapshot.roots[0])
        self.assertIn("text.selections", rendered["details"])

    def test_multiple_empty_partial_and_failed_ranges_remain_distinct(self):
        raw = windows(
            [
                node(
                    "editor",
                    ControlType="ControlType.Document",
                    **{
                        "Text.Selection": selection(
                            text_range("", True),
                            text_range("prefix", text=dict(state="truncated", detail="limited")),
                            state="partial",
                            total=3,
                        )
                    },
                )
            ]
        )
        _, rendered = self.render(raw)
        self.assertIn("partial; 2/3 ranges", rendered["text"])
        self.assertIn('empty range 1: ""', rendered["text"])
        self.assertIn("text.selections[1].text", rendered["text"])
        self.assertIn('(partial): "limited"', rendered["text"])
        raw["nodes"][0]["values"].pop("Text.Selection")
        raw["nodes"][0]["fieldStatus"]["Text.Selection"] = dict(state="error", detail="provider failed")
        _, rendered = self.render(raw)
        self.assertNotIn("Text selection of", rendered["text"])
        self.assertIn("provider failed", rendered["text"])

    def test_passive_empty_ranges_share_text_but_focused_and_partial_ranges_stay_explicit(self):
        empty = selection(text_range("", True))
        raw = windows(
            [
                node("window", ControlType="ControlType.Window"),
                node("a", ControlType="ControlType.Text", HasKeyboardFocus=False, **{"Text.Selection": empty}),
                node("b", ControlType="ControlType.Text", HasKeyboardFocus=False, **{"Text.Selection": empty}),
                node("focused", ControlType="ControlType.Text", HasKeyboardFocus=True, **{"Text.Selection": empty}),
                node(
                    "partial",
                    ControlType="ControlType.Text",
                    HasKeyboardFocus=False,
                    **{"Text.Selection": selection(text_range("", True), state="partial", total=2)},
                ),
            ],
            [edge("window", "a", "b", "focused", "partial", property="RawViewChildren")],
        )
        _, rendered = self.render(raw)
        self.assertIn("Text selection of elements 1, 2", rendered["text"])
        self.assertIn("Text selection of 3", rendered["text"])
        self.assertIn("partial; 1/2 ranges", rendered["text"])
        self.assertIn("Text selection of 1", rendered["details"])
        self.assertIn("Text selection of 2", rendered["details"])

    def test_long_identifiers_are_marked_and_retained_exactly_in_details(self):
        identifier = "Application.Dialog." + "Container." * 30 + "final-control"
        snapshot, rendered = self.render(
            windows([node("custom", ControlType="ControlType.Custom", AutomationId=identifier)])
        )
        self.assertIn("ID shortened; see details", rendered["text"])
        self.assertIn("final-control", rendered["text"])
        self.assertNotIn(identifier, rendered["text"])
        self.assertIn(identifier, rendered["details"])
        self.assertEqual(snapshot.facts["nodes"][0]["values"]["identifier"], identifier)

    def test_identical_timeout_coverage_shares_refs_without_hiding_different_failures(self):
        source = node("a", ControlType="ControlType.Text")
        for key in ("Text.Selection", "Text.DocumentRange"):
            source["requestedFields"].append(key)
            source["fieldStatus"][key] = dict(state="timeout", detail="collection budget exhausted")
        source["requestedFields"].append("Name")
        source["fieldStatus"]["Name"] = dict(state="error", detail="provider failed")
        _, rendered = self.render(windows([source]))
        self.assertIn("text.selections; text.content (timeout)", rendered["text"])
        self.assertEqual(rendered["text"].count("collection budget exhausted"), 1)
        self.assertIn('name (error): "provider failed"', rendered["text"])
        self.assertIn("text.content", rendered["details"])

    def test_raw_only_text_and_embedded_button_survive_as_separate_facts(self):
        raw = windows(
            [
                node("document", ControlType="ControlType.Document", **{"Text.DocumentRange": "A sentence."}),
                node(
                    "badge",
                    ControlType="ControlType.Text",
                    Name="Raw-only status: 42",
                    IsControlElement=False,
                    IsContentElement=False,
                ),
                node("button", ControlType="ControlType.Button", Name="Embedded action"),
            ],
            [
                edge("document", "badge", property="RawViewChildren"),
                edge("document", "button", property="Text.Children"),
            ],
        )
        snapshot, rendered = self.render(raw)
        self.assertIn("Raw-only status: 42", rendered["text"])
        self.assertIn("Embedded action", rendered["text"])
        self.assertIn("embedded_children", rendered["details"])
        # Supplementary membership does not create a fabricated RawView edge.
        self.assertEqual(snapshot.facts["relations"][1]["property"], "embedded_children")
        self.assertNotIn("button", snapshot.facts["relations"][0]["targets"])

    def test_virtualized_coverage_is_not_empty_or_a_guessed_total(self):
        raw = windows(
            [
                node(
                    "list",
                    ControlType="ControlType.List",
                    **{
                        "UIA.Collection": dict(
                            view="raw",
                            mayContainVirtualizedItems=True,
                            isVirtualizedItem=False,
                            logicalContentComplete=None,
                        )
                    },
                )
            ],
            [edge("list", property="RawViewChildren", total=0)],
        )
        _, rendered = self.render(raw)
        self.assertIn("exposed children do not establish the logical total", rendered["text"])
        self.assertIsNone(raw["nodes"][0]["values"]["UIA.Collection"]["logicalContentComplete"])

    def test_password_unknown_and_absent_fields_never_fabricate_empty_text(self):
        source = node("password", ControlType="ControlType.Edit", IsPassword=True)
        source["requestedFields"] += ["Value.Value", "Text.DocumentRange", "Text.Selection"]
        source["fieldStatus"] = {
            key: dict(state="unsupported", detail="protected text is not captured")
            for key in ("Value.Value", "Text.DocumentRange", "Text.Selection")
        }
        snapshot, _ = self.render(windows([source]))
        self.assertNotIn("value", snapshot.roots[0])
        self.assertEqual(set(snapshot.facts["nodes"][0]["values"]), {"role", "states.protected"})


class WindowsReadProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_fields_and_text_edges_are_negotiated_without_changing_explicit_queries(self):
        class Port:
            async def nodes(self, *args, **options):
                self.fields, self.options = args[2], options
                from observation_fixture import reply

                return reply(windows([node("window", ControlType="ControlType.Window")]))

        target = Binding("session", "instance", "window", "surface", "window")
        read = SimpleNamespace(target=target, token="operation", remaining=lambda: 3000)
        for feature in (False, True):
            port = Port()
            port.connection = SimpleNamespace(
                platform="windows",
                capabilities=(("nativeUiaReadV2", "nativeFollowRelations") if feature else ("nativeFollowRelations",)),
            )
            reader = NativeReader(port)
            await reader.collect(read)
            self.assertEqual(
                set(port.fields) & WINDOWS_READ_V2_FIELDS.keys(),
                set(WINDOWS_READ_V2_FIELDS) - WINDOWS_ON_DEMAND_FIELDS if feature else set(),
            )
            self.assertEqual("Text.Children" in port.options["follow_relations"], feature)
            self.assertNotIn("Grid.Cells", port.options["follow_relations"])
            self.assertNotIn("ItemContainer.Items", port.options["follow_relations"])
            await reader.collect(read, fields=["Text.Selection"])
            self.assertEqual(port.fields, ["Text.Selection"])
            self.assertEqual(port.options, {"scope_kind": None})
            await reader.collect(read, fields=["Text.VisibleRanges", "Text.SelectionBounds"])
            self.assertEqual(port.fields, ["Text.VisibleRanges", "Text.SelectionBounds"])


if __name__ == "__main__":
    unittest.main()
