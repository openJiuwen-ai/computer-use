"""Unified observations retain useful facts without platform object wrappers."""

from __future__ import annotations

import json
import math
import unittest
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

from computer_use.protocol.observation import (
    Action,
    ControlValue,
    CoordinateSpace,
    ElementValue,
    Geometry,
    NativeNode,
    NativeObservation,
    NumericValue,
    ReadIssue,
    ReadRef,
    Relation,
    ScrollAxis,
    Shortcut,
    SpecialValue,
    States,
    Structure,
    Supplement,
    TextInfo,
    TextSpan,
    decode_observation,
    encode_observation,
    validate_query_observation,
)


def observation(nodes=None, **changes):
    values = dict(
        backend="macos",
        session="driver-1",
        id="capture-1",
        interval_ns=(1_791_000_000_000_000_000, 1_791_000_000_000_000_001),
        roots=("root",),
        nodes=nodes if nodes is not None else {"root": NativeNode()},
    )
    values.update(changes)
    return NativeObservation(**values)


class NormalizedObservationTests(unittest.TestCase):
    def roundtrip(self, original):
        encoded = encode_observation(original)
        restored = decode_observation(json.loads(json.dumps(encoded, ensure_ascii=True, allow_nan=False)))
        self.assertEqual(original, restored)
        self.assertEqual(encoded, encode_observation(restored))
        return restored

    def test_complete_semantic_graph_roundtrip(self):
        original = observation(
            {
                "root": NativeNode(
                    role="window",
                    name="Budget",
                    states=States(main_window=True, focused=False),
                    geometry={
                        "bounds": Geometry("screen", -20, 40, 800, 600, True),
                        "client": Geometry("local", 0, 0, 780, 570),
                    },
                    relations={
                        "children": Relation("children", ("editor", "slider")),
                        "window": Relation("window", ("root",)),
                    },
                ),
                "editor": NativeNode(
                    role="text_field",
                    role_detail="search",
                    name="Find",
                    placeholder="Type…",
                    description="Filter documents",
                    control_value=ControlValue("a😀z", read_only=False),
                    states=States(enabled=True, focusable=True, focused=True, selected=False, has_popup=True),
                    text=TextInfo(
                        "text-editor",
                        unit="utf16_code_unit",
                        content_field=("control_value", "value"),
                        character_count=4,
                        selected_text="😀",
                        selections=(TextSpan(1, 3, "😀"),),
                        caret=TextSpan(3, 3, collapsed=True),
                        caret_active=True,
                        runs=(TextSpan(0, 4, attributes={"language": "en"}),),
                        visible_ranges=(TextSpan(0, 4),),
                        line_ranges=(TextSpan(0, 4),),
                        embedded=(("icon", TextSpan(0, 0)),),
                        supported_selection="single",
                    ),
                    actions=(
                        Action("invoke", "AXPress", "Open", "Open suggestion"),
                        Action("set_value", target=("control_value", "value"), available=True),
                    ),
                    relations={"parent": Relation("parent", ("root",)), "labels": Relation("labelled_by", ("label",))},
                    shortcuts=(Shortcut("⌘F", "accelerator"),),
                ),
                "slider": NativeNode(
                    role="slider",
                    numeric_value=NumericValue(3, 0, 10, 1, description="Medium"),
                    structure=Structure(
                        orientation="horizontal", scroll_x=ScrollAxis(True, 0.3, 0, 1, 0.2, "fraction")
                    ),
                ),
                "icon": NativeNode(role="image", description="Search"),
                "label": NativeNode(),
            },
            coordinate_spaces={
                "screen": CoordinateSpace("point", "primary_screen_top_left", "down"),
                "local": CoordinateSpace("point", "window_top_left", "down", "root", "screen", (1, 0, 0, 1, -20, 40)),
            },
            issues=(ReadIssue(("nodes", "label"), "not_read"),),
        )
        self.roundtrip(original)

    def test_zero_false_empty_and_unknown_are_distinct(self):
        original = observation(
            {
                "root": NativeNode(
                    name="",
                    control_value=ControlValue(0),
                    actions=(),
                    shortcuts=(),
                    text=TextInfo("empty", content="", selections=()),
                    states=States(enabled=False, focused=False),
                    relations={"children": Relation("children", (), total=0)},
                )
            }
        )
        restored = self.roundtrip(original)
        node = restored.nodes["root"]
        self.assertEqual(node.name, "")
        self.assertEqual(node.actions, ())
        self.assertIsNone(node.states.selected)
        self.assertIn("actions", encode_observation(original)["nodes"]["root"])
        self.assertNotIn("actions", encode_observation(observation())["nodes"]["root"])

    def test_semantic_special_values_are_json_portable(self):
        instant = datetime(2026, 10, 23, 13, 30, 1, 123456, tzinfo=timezone(timedelta(hours=8)))
        original = observation(
            {
                "root": NativeNode(
                    control_value=ControlValue(instant),
                    numeric_value=NumericValue(float("nan"), float("-inf"), float("inf"), -0.0),
                    text=TextInfo(
                        "mixed",
                        read_only=SpecialValue.MIXED,
                        runs=(TextSpan(attributes={"read_only": SpecialValue.MIXED}),),
                    ),
                    supplements=(Supplement("large", 2**80), Supplement("ref", ElementValue("root"))),
                )
            }
        )
        restored = self.roundtrip(original)
        self.assertTrue(math.isnan(restored.nodes["root"].numeric_value.value))
        self.assertEqual(math.copysign(1, restored.nodes["root"].numeric_value.small_step), -1)
        self.assertEqual(restored.nodes["root"].control_value.value.utcoffset(), timedelta(hours=8))
        self.assertEqual(original, deepcopy(restored))

    def test_literal_tag_keys_survive_as_user_data(self):
        original = observation(
            {
                "root": NativeNode(
                    supplements=(
                        Supplement(
                            "provider.custom",
                            {
                                "$type": "element",
                                "node": "ordinary text",
                                "nested": {"$type": "datetime", "value": "not a date"},
                            },
                        ),
                    )
                ),
                "$type": NativeNode(name="literal identity"),
            }
        )
        self.roundtrip(original)

    def test_unicode_and_isolated_surrogates_remain_text(self):
        self.roundtrip(observation({"root": NativeNode(name="保存😀\ud800", description="A\u0000B")}))

    def test_relation_views_and_duplicate_slots_are_retained(self):
        original = observation(
            {
                "root": NativeNode(
                    relations={
                        "raw-children": Relation("children", ("a", "b"), view="raw", positions=(0, 1), total=3),
                        "content-children": Relation("children", ("b",), view="content", total=1),
                        "grid-cells": Relation("cells", ("a", "a", "b"), positions=(0, 1, 2), total=3),
                        "viewport": Relation(
                            "viewport_candidates",
                            ("b",),
                            positions=(12,),
                            total=100,
                            enumeration="sampled",
                            evidence="candidate",
                        ),
                    }
                ),
                "a": NativeNode(),
                "b": NativeNode(),
            },
            issues=(ReadIssue(("nodes", "root", "relations", "raw-children"), "partial"),),
        )
        self.roundtrip(original)

    def test_collection_and_text_continuations_keep_independent_units(self):
        original = observation(
            {
                "root": NativeNode(
                    text=TextInfo("captured", unit="unicode_codepoint", content="A😀"),
                    relations={"children": Relation("children", ("stub",), positions=(3,), total=8)},
                ),
                "stub": NativeNode(),
            },
            issues=(
                ReadIssue(
                    ("nodes", "root", "text", "content"),
                    "partial",
                    continuation=ReadRef("text-token", "text", "captured", "utf16_code_unit", 20, (0, 3)),
                ),
                ReadIssue(
                    ("nodes", "root", "relations", "children"),
                    "partial",
                    continuation=ReadRef("relation-token", "relation", "live"),
                ),
            ),
        )
        self.roundtrip(original)

    def test_empty_relationship_page_retains_its_start_and_live_continuation(self):
        original = observation(
            {
                "root": NativeNode(
                    relations={
                        "children": Relation("children", (), positions=(), total=8, offset=3),
                    }
                )
            },
            issues=(
                ReadIssue(
                    ("nodes", "root", "relations", "children"),
                    "partial",
                    continuation=ReadRef("next-page", "relation", "live"),
                ),
            ),
        )
        restored = self.roundtrip(original)
        self.assertEqual(restored.nodes["root"].relations["children"].offset, 3)
        self.assertEqual(restored.nodes["root"].relations["children"].positions, ())
        self.assertEqual(restored.issues[0].continuation.token, "next-page")

    def test_reference_only_text_range_does_not_require_offsets(self):
        original = observation(
            {
                "root": NativeNode(
                    text=TextInfo(
                        "uia",
                        selected_text="chosen",
                        selections=(
                            TextSpan(text="chosen", collapsed=False, read_ref=ReadRef("range", "text_range", "live")),
                        ),
                    )
                )
            }
        )
        self.roundtrip(original)

    def test_partial_geometry_and_explicit_invalid_geometry_are_preserved(self):
        original = observation(
            {
                "root": NativeNode(
                    geometry={
                        "bounds": Geometry("screen", x=-400, y=0),
                        "client": Geometry("screen", width=-1, valid=False),
                    }
                )
            },
            coordinate_spaces={"screen": CoordinateSpace("point", "primary_screen_top_left", "down")},
            issues=(ReadIssue(("nodes", "root", "geometry", "bounds", "width"), "timeout"),),
        )
        self.roundtrip(original)

    def test_nested_containers_are_detached_and_frozen(self):
        extra = {"items": ["one", {"label": "two"}]}
        attributes = {"language": "zh"}
        node = NativeNode(
            supplements=(Supplement("custom", extra),), text=TextInfo("t", runs=(TextSpan(attributes=attributes),))
        )
        nodes = {"root": node}
        original = observation(nodes)
        extra["items"][1]["label"] = "changed"
        attributes["language"] = "en"
        nodes.clear()
        self.assertEqual(original.nodes["root"].supplements[0].value["items"][1]["label"], "two")
        self.assertEqual(original.nodes["root"].text.runs[0].attributes["language"], "zh")
        with self.assertRaises(TypeError):
            original.nodes["new"] = node
        with self.assertRaises(TypeError):
            original.nodes["root"].supplements[0].value["new"] = "bad"
        with self.assertRaises(FrozenInstanceError):
            node.name = "bad"

    def test_native_graph_cycles_are_legal_but_container_cycles_are_not(self):
        self.roundtrip(observation({"root": NativeNode(relations={"children": Relation("children", ("root",))})}))
        extra = {}
        extra["self"] = extra
        with self.assertRaisesRegex(ValueError, "acyclic"):
            Supplement("custom", extra)

    def test_conflict_and_missing_fields_keep_explicit_states(self):
        issues = tuple(
            ReadIssue(("nodes", "root", "name"), state)
            for state in (
                "not_read",
                "absent",
                "unsupported",
                "partial",
                "error",
                "timeout",
                "unavailable",
                "redacted",
                "stale",
            )
        )
        original = observation(
            issues=(*issues, ReadIssue(("nodes", "root", "states", "selected"), "conflict", alternatives=(True, False)))
        )
        self.roundtrip(original)

    def test_nan_equality_does_not_generate_spurious_changes(self):
        a = NativeNode(numeric_value=NumericValue(float("nan")))
        b = NativeNode(numeric_value=NumericValue(float("nan")))
        self.assertEqual(a, b)
        self.assertNotEqual(a, replace(b, name="changed"))
        self.assertNotEqual(ControlValue(True), ControlValue(1))

    def test_old_wire_and_unknown_record_fields_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "native_observation"):
            decode_observation({"kind": "observation"})
        raw = encode_observation(observation())
        raw["nodes"]["root"]["native_type"] = "AXUIElement"
        with self.assertRaises(ValueError):
            decode_observation(raw)

    def test_unknown_tags_and_untagged_nonfinite_values_are_rejected(self):
        for malformed in (
            {"$type": "CFString", "value": "x"},
            {"$type": "float", "value": "oops"},
            {"$type": "integer", "value": "01"},
            {"$type": "datetime", "value": "2026-01-01"},
            {"$type": "element", "node": 2},
            float("nan"),
        ):
            with self.subTest(value=malformed):
                raw = encode_observation(observation())
                raw["nodes"]["root"]["control_value"] = {"value": malformed}
                with self.assertRaises(ValueError):
                    decode_observation(raw)

    def test_undeclared_references_are_rejected_everywhere(self):
        bad_nodes = (
            NativeNode(control_value=ControlValue(ElementValue("lost"))),
            NativeNode(relations={"children": Relation("children", ("lost",))}),
            NativeNode(text=TextInfo("t", embedded=(("lost", TextSpan()),))),
            NativeNode(supplements=(Supplement("nested", {"ref": ElementValue("lost")}),)),
        )
        for node in bad_nodes:
            with self.subTest(node=node), self.assertRaises(ValueError):
                encode_observation(observation({"root": node}))
        with self.assertRaises(ValueError):
            encode_observation(
                observation(issues=(ReadIssue(("nodes", "root"), "conflict", alternatives=(ElementValue("lost"),)),))
            )

    def test_invalid_relation_coverage_is_rejected(self):
        for relation in (
            Relation("children", ("root",), positions=()),
            Relation("children", ("root",), positions=(-1,)),
            Relation("children", ("root",), positions=(3,), total=3),
            Relation("children", (), offset=-1),
            Relation("children", (), offset=4, total=3),
            Relation("children", ("root",), offset=3, total=3),
            Relation("children", ("root",), offset=2, positions=(1,)),
            Relation("children", ("root",), offset=2, positions=(3,)),
            Relation("children", ("root",), total=0),
            Relation("children", (), total=-1),
        ):
            with self.subTest(relation=relation), self.assertRaises(ValueError):
                encode_observation(observation({"root": NativeNode(relations={"r": relation})}))

    def test_invalid_text_and_continuation_ranges_are_rejected(self):
        for text in (
            TextInfo("t", content="x", content_field=("name",)),
            TextInfo("t", selections=(TextSpan(3, 1),)),
            TextInfo("t", selections=(TextSpan(-1, 1),)),
            TextInfo("t", content_field=("states", "enabled")),
            TextInfo("t", content_field=("text", "content")),
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                encode_observation(observation({"root": NativeNode(text=text, states=States(enabled=True))}))
        for ref in (
            ReadRef("t", "text", "live", total=1, delivered=(0, 2)),
            ReadRef("t", "text", "live", delivered=(3, 1)),
        ):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                encode_observation(observation(issues=(ReadIssue((), "partial", continuation=ref),)))

    def test_invalid_coordinate_references_and_cycles_are_rejected(self):
        bad = (
            observation({"root": NativeNode(geometry={"bounds": Geometry("lost")})}),
            observation(coordinate_spaces={"s": CoordinateSpace("point", "provider", "down", owner="lost")}),
            observation(coordinate_spaces={"s": CoordinateSpace("point", "provider", "down", parent="s")}),
            observation(
                coordinate_spaces={"s": CoordinateSpace("point", "provider", "down", to_parent=(1, 0, 0, 1, 0, 0))}
            ),
            observation(
                {"root": NativeNode(geometry={"bounds": Geometry("s", x=float("nan"))})},
                coordinate_spaces={"s": CoordinateSpace("point", "provider", "down")},
            ),
        )
        for original in bad:
            with self.subTest(original=original), self.assertRaises(ValueError):
                encode_observation(original)

    def test_booleans_are_not_numbers_and_datetimes_require_timezone(self):
        for original in (
            observation(interval_ns=(True, 2)),
            observation({"root": NativeNode(numeric_value=NumericValue(True))}),
            observation({"root": NativeNode(states=States(enabled=1))}),
            observation({"root": NativeNode(identifier=0)}),
            observation({"root": NativeNode(control_value=ControlValue(datetime(2026, 1, 1)))}),
        ):
            with self.subTest(original=original), self.assertRaises(ValueError):
                encode_observation(original)

    def test_recursive_value_nesting_budget_keeps_its_boundary(self):
        for container in (lambda value: (value,), lambda value: {"child": value}):
            value = "leaf"
            for _ in range(45):
                value = container(value)
            accepted = observation({"root": NativeNode(supplements=(Supplement("nested", value),))})
            validate_query_observation(accepted)
            with self.assertRaises(ValueError):
                validate_query_observation(
                    observation({"root": NativeNode(supplements=(Supplement("nested", container(value)),))})
                )

    def test_nested_semantic_references_are_checked_inside_supplements(self):
        for reference in ("root", "missing"):
            value = {"first": (False, {"nested": (0, "", ElementValue(reference))})}
            original = observation({"root": NativeNode(supplements=(Supplement("nested", value),))})
            if reference == "root":
                validate_query_observation(original)
            else:
                with self.assertRaisesRegex(ValueError, "undeclared element-valued reference"):
                    validate_query_observation(original)

    def test_roots_and_issues_cannot_reference_missing_nodes(self):
        for original in (
            observation(roots=("lost",)),
            observation(roots=("root", "root")),
            observation(issues=(ReadIssue(("nodes", "lost"), "not_read"),)),
            observation(issues=(ReadIssue(("not_a_field",), "error"),)),
        ):
            with self.subTest(original=original), self.assertRaises(ValueError):
                validate_query_observation(original)

    def test_empty_observation_is_valid(self):
        self.roundtrip(observation({}, roots=()))


if __name__ == "__main__":
    unittest.main()
