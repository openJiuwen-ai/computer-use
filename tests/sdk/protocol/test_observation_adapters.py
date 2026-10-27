"""Platform normalization retains independent semantics and exact native selectors."""

import hashlib
import math
import unittest
from datetime import datetime, timezone

from fixtures.native_graph import observation, typed

from computer_use.protocol.observation import (
    SpecialValue,
    decode_observation,
    encode_observation,
)
from computer_use.protocol.observation.adapters import (
    decode_driver_observation,
    decode_driver_reply,
    decode_query_facts,
)


def carrier(backend, values, *, statuses=None, actions=(), relations=(), unread=()):
    """Encode one genuine driver field table through the wire fixture writer."""
    statuses = statuses or {}
    fields = list(dict.fromkeys([*values, *statuses]))
    return observation(
        dict(
            kind="nodes",
            platform=backend,
            session="session",
            surface="surface",
            observed=dict(id="capture", startedAt=1, completedAt=2),
            roots=["root"],
            requestedRelations=[edge["property"] for edge in relations],
            relations=list(relations),
            unread=list(unread),
            stopReasons=[],
            changeToken="change",
            nodes=[
                dict(
                    node="root",
                    requestedFields=fields,
                    values=values,
                    fieldStatus=statuses,
                    actions=dict(state="complete", items=list(actions), total=len(actions)),
                )
            ],
        )
    )


def declaration(name, description="perform operation"):
    return dict(name=name, description=description)


def edge(property, targets, **changes):
    return (
        dict(
            source="root",
            property=property,
            targets=list(targets),
            certainty="confirmed",
            state="complete",
            offset=0,
            total=len(targets),
        )
        | changes
    )


def anchor(text):
    return dict(
        source="Text.DocumentRange",
        unit="utf16",
        units=len(text.encode("utf-16le")) // 2,
        complete=True,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
    )


class ObservationAdapterTests(unittest.TestCase):
    def normalized(self, raw):
        """Also verify the public object is valid and codec-stable."""
        result = decode_driver_observation(raw)
        self.assertEqual(result, decode_observation(encode_observation(result)))
        return result

    def test_three_platforms_keep_the_same_element_meaning(self):
        inputs = [
            ("macos", {"AXRole": "AXButton", "AXTitle": "Save", "AXEnabled": False}, "AXPress"),
            ("windows", {"ControlType": "Button", "Name": "Save", "IsEnabled": False}, "Invoke.Invoke"),
            ("linux", {"role": "push button", "name": "Save", "enabled": False}, "click"),
        ]
        for backend, values, key in inputs:
            with self.subTest(backend=backend):
                graph = self.normalized(carrier(backend, values, actions=[declaration(key)]))
                node = graph.nodes["root"]
                self.assertEqual(graph.backend, backend)
                self.assertEqual((node.role, node.name, node.states.enabled), ("button", "Save", False))
                self.assertEqual(node.actions[0].kind, "invoke")
                self.assertEqual(node.actions[0].key, key)
                self.assertIsNone(node.actions[0].available)

    def test_name_value_document_and_numeric_channels_coexist(self):
        graph = self.normalized(
            carrier(
                "windows",
                {
                    "ControlType": "Edit",
                    "Name": "Amount",
                    "Value.Value": "formatted 1,000",
                    "Value.IsReadOnly": False,
                    "Text.DocumentRange": "invoice body",
                    "Text.IsReadOnly": "mixed",
                    "Text.SupportedTextSelection": "Multiple",
                    "RangeValue.Value": 1000,
                    "RangeValue.Minimum": 0,
                    "RangeValue.Maximum": 5000,
                    "RangeValue.SmallChange": 10,
                    "SupportedPatterns": ["ValuePattern", "RangeValuePattern", "TextPattern"],
                },
            )
        )
        node = graph.nodes["root"]
        self.assertEqual(node.control_value.value, "formatted 1,000")
        self.assertEqual(node.text.content, "invoice body")
        self.assertIsNone(node.text.content_field)
        self.assertEqual(node.text.read_only, SpecialValue.MIXED)
        self.assertEqual(node.text.supported_selection, "multiple")
        self.assertEqual(node.numeric_value.value, 1000)
        self.assertEqual(
            {action.target for action in node.actions}, {("control_value", "value"), ("numeric_value", "value")}
        )
        self.assertTrue(node.actions[0].available)
        self.assertIsNone(node.actions[1].available)

    def test_exact_queries_do_not_roundtrip_through_semantics(self):
        values = {
            "AXRole": "AXTextField",
            "AXValue": "",
            "AXEnabled": False,
            "AXMinValue": 0,
            "AXSelectedTextRange": {"location": 0, "length": 0},
        }
        raw = carrier("macos", values, statuses={"AXHelp": {"state": "timeout", "detail": "budget"}})
        reply = decode_driver_reply(raw)
        native = decode_query_facts(raw)
        self.assertEqual(native["nodes"][0]["values"], values)
        self.assertEqual(native["nodes"][0]["fieldStatus"]["AXHelp"]["state"], "timeout")
        self.assertEqual(reply.metadata.surface, "surface")
        self.assertEqual(reply.metadata.change_token, "change")
        self.assertEqual(reply.metadata.field_sources[("root", ("control_value", "value"))], "AXValue")
        self.assertFalse(hasattr(reply.observation, "surface"))

    def test_empty_false_unobserved_and_failed_fields_are_distinct(self):
        graph = self.normalized(
            carrier(
                "macos",
                {"AXRole": "AXButton", "AXTitle": "", "AXEnabled": False},
                statuses={"AXDescription": {"state": "unsupported"}, "AXHelp": {"state": "not_read"}},
            )
        )
        node = graph.nodes["root"]
        self.assertEqual(node.name, "")
        self.assertIs(node.states.enabled, False)
        self.assertIsNone(node.states.focused)
        issues = {issue.path: issue.state for issue in graph.issues}
        self.assertEqual(issues[("nodes", "root", "description")], "unsupported")
        self.assertEqual(issues[("nodes", "root", "help")], "not_read")
        self.assertNotIn(("nodes", "root", "states", "focused"), issues)

    def test_partial_cyclic_relationships_keep_page_and_query_selector(self):
        raw = carrier(
            "macos",
            {"AXRole": "AXGroup"},
            relations=[
                edge("AXChildren", ["child", "root"], state="partial", offset=4, total=9, cursor="next"),
                edge("AXTitleUIElement", ["child"]),
            ],
            unread=["child"],
        )
        reply = decode_driver_reply(raw)
        self.normalized(raw)
        node = reply.observation.nodes["root"]
        relation = node.relations["children"]
        self.assertEqual(relation.targets, ("child", "root"))
        self.assertEqual(relation.positions, (4, 5))
        self.assertEqual(relation.total, 9)
        self.assertEqual(node.relations["labelled_by"].targets, ("child",))
        self.assertEqual(reply.metadata.relation_fields[("root", "children")], "AXChildren")
        issue = next(i for i in reply.observation.issues if i.path == ("nodes", "root", "relations", "children"))
        self.assertEqual((issue.state, issue.continuation.token), ("partial", "next"))
        self.assertTrue(any(i.path == ("nodes", "child") and i.state == "not_read" for i in reply.observation.issues))

    def test_two_child_views_are_not_overwritten(self):
        raw = carrier(
            "linux",
            {"role": "panel"},
            relations=[edge("children", ["a"]), edge("node-parent-of", ["b"])],
            unread=["a", "b"],
        )
        graph = self.normalized(raw)
        relations = tuple(graph.nodes["root"].relations.values())
        self.assertEqual([r.kind for r in relations], ["children", "children"])
        self.assertEqual([r.targets for r in relations], [("a",), ("b",)])

    def test_partial_geometry_preserves_origin_and_units(self):
        mac = self.normalized(
            carrier("macos", {"AXPosition": {"x": -100, "y": 12}}, statuses={"AXSize": {"state": "timeout"}})
        )
        bounds = mac.nodes["root"].geometry["bounds"]
        self.assertEqual((bounds.x, bounds.y, bounds.width), (-100, 12, None))
        self.assertEqual(mac.coordinate_spaces[bounds.space].unit, "point")
        linux = self.normalized(
            carrier(
                "linux",
                {"Component.extents": {"x": -3, "y": 2, "width": 0, "height": -1}, "visible": True, "showing": False},
            )
        )
        node = linux.nodes["root"]
        self.assertEqual(node.geometry["bounds"].height, -1)
        self.assertIs(node.geometry["bounds"].valid, False)
        self.assertEqual(linux.coordinate_spaces[node.geometry["bounds"].space].unit, "provider")
        self.assertIs(node.states.visible, True)
        self.assertIs(node.states.showing, False)
        self.assertIsNone(node.states.offscreen)

    def test_linux_native_text_offsets_and_capture_continuation_keep_units(self):
        raw = carrier(
            "linux",
            {
                "role": "entry",
                "Text.text": "A😀",
                "Text.characterCount": 10,
                "Text.selections": [{"startOffset": 1, "endOffset": 2}],
            },
            statuses={"Text.text": {"state": "truncated", "textRef": "t", "totalUnits": 20}},
        )
        graph = self.normalized(raw)
        self.assertEqual(graph.nodes["root"].text.unit, "provider")
        self.assertEqual(graph.nodes["root"].text.selections[0].end, 2)
        issue = next(i for i in graph.issues if i.path == ("nodes", "root", "text", "content"))
        self.assertEqual((issue.continuation.unit, issue.continuation.delivered), ("utf16_code_unit", (0, 3)))

    def test_unknown_semantics_remain_but_runtime_identifiers_do_not(self):
        graph = self.normalized(
            carrier(
                "windows",
                {
                    "ControlType": "VendorSurface",
                    "ProcessId": 42,
                    "RuntimeId": [1, 3],
                    "ClassName": "WindowClass",
                    "Vendor.Semantic": {"label": "Drawing Canvas", "mode": "pencil"},
                    "IsControlElement": False,
                },
            )
        )
        node = graph.nodes["root"]
        self.assertEqual(node.role, "unknown")
        supplements = {item.key: item.value for item in node.supplements}
        self.assertEqual(supplements["provider.role"], "VendorSurface")
        self.assertEqual(supplements["provider.windows.Vendor.Semantic"]["mode"], "pencil")
        self.assertIs(supplements["windows.is_control_element"], False)
        self.assertFalse(any("RuntimeId" in key or "ClassName" in key or "ProcessId" in key for key in supplements))

    def test_nonfinite_ranges_and_dates_are_semantic_values(self):
        raw = carrier("macos", {"AXValue": "placeholder", "AXMinValue": 0})
        raw["nodes"][0]["properties"][0]["value"] = dict(nativeType="CFDate", data=0)
        raw["nodes"][0]["properties"][1]["value"] = dict(nativeType="CFNumber", data={"float": "nan"})
        graph = self.normalized(raw)
        self.assertEqual(graph.nodes["root"].control_value.value, datetime(2001, 1, 1, tzinfo=timezone.utc))
        self.assertTrue(math.isnan(graph.nodes["root"].numeric_value.minimum))

    def test_malformed_enums_remain_evidence_without_boolean_coercion(self):
        for key in (
            "Toggle.ToggleState",
            "ExpandCollapse.ExpandCollapseState",
            "Window.WindowVisualState",
            "Orientation",
        ):
            for value in (["Expanded"], {"value": "On"}, 77):
                with self.subTest(key=key, value=value):
                    graph = self.normalized(carrier("windows", {key: value}))
                    self.assertTrue(
                        any(item.key == "provider.windows." + key for item in graph.nodes["root"].supplements)
                    )

    def test_mixed_toggle_and_selected_are_independent(self):
        graph = self.normalized(
            carrier(
                "linux",
                {
                    "role": "toggle button",
                    "checked": True,
                    "indeterminate": True,
                    "selected": False,
                    "selectable": True,
                },
            )
        )
        node = graph.nodes["root"]
        self.assertEqual(node.states.checked, "mixed")
        self.assertIs(node.states.selected, False)
        self.assertIs(node.states.selectable, True)

    def test_absent_atspi_flags_do_not_make_normal_controls_toggles(self):
        for role in ("frame", "push button", "entry"):
            graph = self.normalized(carrier("linux", {"role": role, "checked": False, "expanded": False}))
            node = graph.nodes["root"]
            self.assertTrue(node.states is None or node.states.checked is None)
            self.assertTrue(node.states is None or node.states.expanded is None)
            values = {item.key: item.value for item in node.supplements}
            self.assertIs(values["provider.linux.checked"], False)
            self.assertIs(values["provider.linux.expanded"], False)
        check = self.normalized(carrier("linux", {"role": "check box", "checked": False}))
        tree = self.normalized(carrier("linux", {"role": "tree item", "expanded": False}))
        self.assertEqual(check.nodes["root"].states.checked, "off")
        self.assertEqual(tree.nodes["root"].states.expanded, "collapsed")

    def test_action_name_only_and_failed_catalogues_are_retained(self):
        raw = carrier("macos", {}, actions=[{"name": "AXPress"}])
        self.assertEqual(self.normalized(raw).nodes["root"].actions[0].key, "AXPress")
        for state in ("error", "timeout"):
            action_property = raw["nodes"][0]["properties"][0]
            action_property["read"]["details"] = [["state", typed(state)], ["total", typed(1)]]
            self.assertTrue(
                any(
                    issue.path == ("nodes", "root", "actions") and issue.state == state
                    for issue in self.normalized(raw).issues
                )
            )

    def test_uia_selection_keeps_unlocated_text_and_geometry(self):
        selection = dict(
            ranges=[
                dict(
                    values={
                        "text": "selected",
                        "degenerate": False,
                        "readOnly": "mixed",
                        "bounds": [{"x": 20, "y": 30, "width": 80, "height": 16}],
                    },
                    fieldStatus={},
                )
            ],
            total=1,
            state="complete",
        )
        graph = self.normalized(carrier("windows", {"Text.Selection": selection}))
        span = graph.nodes["root"].text.selections[0]
        self.assertEqual(span.text, "selected")
        self.assertIsNone(span.start)
        self.assertEqual(span.attributes["read_only"], SpecialValue.MIXED)
        self.assertEqual(span.geometry[0].width, 80)

    def test_uia_verified_caret_is_independent_of_selection(self):
        text = "A😀B"
        caret = dict(
            source="IUIAutomationTextPattern2.GetCaretRange",
            state="complete",
            fieldStatus={},
            values=dict(isActive=True, anchor=anchor(text), range=dict(start=3, end=3, degenerate=True)),
        )
        graph = self.normalized(carrier("windows", {"Text.DocumentRange": text, "Text.CaretRange": caret}))
        info = graph.nodes["root"].text
        self.assertEqual((info.caret.start, info.caret.end, info.caret_active), (3, 3, True))
        self.assertIsNone(info.selections)
        caret["values"]["range"] = dict(start=2, end=2, degenerate=True)
        graph = self.normalized(carrier("windows", {"Text.DocumentRange": text, "Text.CaretRange": caret}))
        self.assertIsNone(graph.nodes["root"].text.caret.start)
        self.assertTrue(any("UTF-16" in (issue.detail or "") for issue in graph.issues))

    def test_uia_mismatched_anchor_retains_object_without_false_offsets(self):
        value = dict(
            source="IUIAutomation.TextPattern.RangeFromChild",
            anchor=anchor("old"),
            objects=[dict(node="child", values=dict(start=0, end=1, degenerate=False), fieldStatus={})],
            total=1,
            state="complete",
            order="nativeEndpoints",
        )
        raw = carrier("windows", {"Text.DocumentRange": "new", "Text.ObjectRanges": value}, unread=["child"])
        graph = self.normalized(raw)
        identity, span = graph.nodes["root"].text.embedded[0]
        self.assertEqual(identity, "child")
        self.assertIsNone(span.start)
        self.assertTrue(any(issue.state == "conflict" for issue in graph.issues))
        self.assertTrue(any(s.key == "text.unconfirmed_position" for s in graph.nodes["root"].supplements))

    def test_uia_provider_line_units_keep_their_limitations(self):
        value = dict(
            source="IUIAutomation.TextPattern.TextUnit.Line",
            requestedUnit="Line",
            unitSupport="unconfirmed",
            anchor=anchor("abc"),
            state="complete",
            documentEndReached=True,
            detail="provider may substitute larger unit",
            ranges=[dict(values=dict(start=0, end=3, degenerate=False, text="abc"), fieldStatus={})],
        )
        graph = self.normalized(carrier("windows", {"Text.DocumentRange": "abc", "Text.LineRanges": value}))
        self.assertEqual(graph.nodes["root"].text.line_ranges[0].end, 3)
        coverage = next(s.value for s in graph.nodes["root"].supplements if s.key == "text.line_ranges.coverage")
        self.assertEqual(coverage["unit_support"], "unconfirmed")

    def test_attributed_text_keeps_semantic_runs_and_value_alias(self):
        graph = self.normalized(
            carrier(
                "macos",
                {
                    "AXValue": {
                        "text": "Hello",
                        "runs": [
                            {
                                "range": {"location": 0, "length": 5},
                                "attributes": {"AXLanguage": "en", "AXHeadingLevel": 1},
                            }
                        ],
                    }
                },
            )
        )
        node = graph.nodes["root"]
        self.assertEqual(node.control_value.value, "Hello")
        self.assertEqual(node.text.content_field, ("control_value", "value"))
        self.assertEqual(node.text.runs[0].attributes["language"], "en")

    def test_relative_native_url_preserves_its_effective_target(self):
        raw = carrier("macos", {"AXURL": "page"})
        raw["nodes"][0]["properties"][0]["value"] = dict(
            nativeType="CFURL",
            data="../target?q=1",
            metadata=[["base", dict(nativeType="CFURL", data="tauri://localhost/profile/current")]],
        )
        graph = self.normalized(raw)
        self.assertEqual(graph.nodes["root"].url, "tauri://localhost/target?q=1")
        self.assertEqual(decode_query_facts(raw)["nodes"][0]["values"]["AXURL"], "../target?q=1")

    def test_complete_relation_note_is_not_a_collection_failure(self):
        graph = self.normalized(
            carrier("macos", {}, relations=[edge("AXChildren", [], detail="Provider explanatory note")])
        )
        self.assertFalse(any(issue.path == ("nodes", "root", "relations", "children") for issue in graph.issues))
        self.assertEqual(graph.nodes["root"].supplements[0].value["text"], "Provider explanatory note")

    def test_bad_provider_parts_do_not_reject_adjacent_useful_fields(self):
        graph = self.normalized(
            carrier(
                "linux",
                {"role": "entry", "name": "Find", "attributes": {"posinset": "0"}, "Text.text": "body"},
                statuses={"Text.text": {"state": "truncated", "textRef": "text", "totalUnits": -1}},
            )
        )
        node = graph.nodes["root"]
        self.assertEqual(node.name, "Find")
        self.assertEqual(node.text.content, "body")
        self.assertTrue(node.structure is None or node.structure.index is None)
        self.assertTrue(
            any(item.key == "provider.attribute.posinset" and item.value == "0" for item in node.supplements)
        )
        self.assertTrue(
            any(issue.state == "error" and "continuation" in (issue.detail or "") for issue in graph.issues)
        )
        windows = self.normalized(
            carrier(
                "windows",
                {
                    "Name": "Grid",
                    "UIA.Collection": {
                        "isVirtualizedItem": False,
                        "mayContainVirtualizedItems": True,
                        "logicalContentComplete": None,
                        "vendorOrder": "reverse",
                    },
                },
            )
        )
        self.assertEqual(windows.nodes["root"].structure.collection_mode, "virtualized")
        self.assertTrue(any(item.value.get("vendorOrder") == "reverse" for item in windows.nodes["root"].supplements))

    def test_invalid_composite_cell_record_keeps_each_reported_component(self):
        raw = {"row": -1, "column": -3, "rowSpan": 2, "columnSpan": 1, "extra": "future"}
        graph = self.normalized(carrier("linux", {"name": "Cell", "TableCell.rowColumnSpan": raw}))
        node = graph.nodes["root"]
        self.assertIsNone(node.structure)
        value = next(s.value for s in node.supplements if s.key == "provider.linux.TableCell.rowColumnSpan")
        self.assertEqual(dict(value), raw)

    def test_parameterized_method_results_keep_arguments_and_complete_description(self):
        raw = carrier(
            "macos",
            {"AXRole": "AXButton"},
            actions=[
                dict(name="AXPress", description="Press", descriptionStatus={"state": "truncated", "detail": "preview"})
            ],
        )
        raw["nodes"][0]["properties"].extend(
            [
                {
                    "key": ["toolkit", "action_description"],
                    "arguments": [typed("AXPress")],
                    "value": typed("Press the currently selected option"),
                },
                {
                    "key": ["AX", "StringForRange"],
                    "arguments": [typed({"location": 3, "length": 2})],
                    "value": typed("ab"),
                },
            ]
        )
        graph = self.normalized(raw)
        node = graph.nodes["root"]
        self.assertEqual(node.actions[0].description, "Press the currently selected option")
        self.assertFalse(any(issue.path == ("nodes", "root", "actions", 0, "description") for issue in graph.issues))
        method = next(item for item in node.supplements if item.key == "provider.macos.AX.StringForRange")
        self.assertEqual(method.value["arguments"][0]["location"], 3)
        self.assertEqual(method.value["result"], "ab")

    def test_malformed_capability_and_geometry_do_not_hide_other_facts(self):
        graph = self.normalized(
            carrier(
                "macos",
                {
                    "AXRole": "AXGroup",
                    "AXTitle": "Editor",
                    "settable.AXFocused": "true",
                    "AXPosition": {"x": 10, "y": 20},
                    "AXSize": 7,
                },
            )
        )
        node = graph.nodes["root"]
        self.assertEqual(node.name, "Editor")
        self.assertEqual(node.geometry["bounds"].x, 10)
        self.assertIsNone(node.geometry["bounds"].width)
        self.assertFalse(any(action.kind == "focus" for action in node.actions))
        self.assertTrue(
            any(item.key.endswith("settable.AXFocused") and item.value == "true" for item in node.supplements)
        )
        self.assertTrue(any(item.key.endswith("AXSize") and item.value == 7 for item in node.supplements))
        self.assertTrue(any(issue.state == "error" for issue in graph.issues))

    def test_empty_relation_page_keeps_the_observed_start(self):
        graph = self.normalized(
            carrier("macos", {}, relations=[edge("AXChildren", [], offset=7, total=7, state="partial")])
        )
        self.assertEqual(graph.nodes["root"].relations["children"].offset, 7)
        self.assertEqual(graph.nodes["root"].relations["children"].positions, ())

    def test_malformed_role_and_collection_parts_are_local_uncertainties(self):
        graph = self.normalized(
            carrier(
                "windows",
                {
                    "Name": "Still readable",
                    "ControlType": 4,
                    "BoundingRectangle": {
                        "x": 1,
                        "y": {"nativeType": "UIA.Double", "value": "NaN"},
                        "width": 40,
                        "height": 30,
                    },
                    "UIA.Collection": {"isVirtualizedItem": "false", "logicalContentComplete": "maybe"},
                },
            )
        )
        node = graph.nodes["root"]
        self.assertIsNone(node.role)
        self.assertEqual(node.name, "Still readable")
        self.assertEqual(node.geometry["bounds"].x, 1)
        self.assertIsNone(node.geometry["bounds"].y)
        self.assertTrue(
            any(issue.path == ("nodes", "root", "role") and issue.state == "error" for issue in graph.issues)
        )
        evidence = {item.key: item.value for item in node.supplements}
        self.assertEqual(evidence["provider.windows.ControlType"], 4)
        self.assertTrue(math.isnan(evidence["geometry.provider_components"]["y"]))
        self.assertEqual(evidence["provider.windows.UIA.Collection.extra"]["logicalContentComplete"], "maybe")


if __name__ == "__main__":
    unittest.main()
