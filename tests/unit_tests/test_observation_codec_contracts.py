# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Decoder optimizations preserve immutable facts and complete validation."""

import base64
import math
from dataclasses import fields, replace
from datetime import UTC, datetime
from types import MappingProxyType
from unittest import TestCase

from computer_use.protocol.observation import (
    Action,
    CoordinateSpace,
    ElementValue,
    Geometry,
    NativeNode,
    NativeObservation,
    ReadIssue,
    ReadRef,
    Relation,
    Supplement,
    TextInfo,
    decode_observation,
    encode_observation,
    validate_query_observation,
)
from computer_use.protocol.observation.adapters.wire import DriverWire


def wire():
    return DriverWire(
        {
            "kind": "observation",
            "session": "session",
            "platform": "macos",
            "roots": ["root"],
            "nodes": [
                {"id": "root", "source": "macos.ax", "kind": "element", "nativeType": "AXUIElement", "properties": []}
            ],
            "read": {
                "intervalNs": [1, 2],
                "details": [
                    ["surface", {"nativeType": "CFString", "data": "surface"}],
                    ["observation_id", {"nativeType": "CFString", "data": "capture"}],
                ],
            },
        }
    )


def observation(node=None, **changes):
    return replace(
        NativeObservation(
            backend="macos",
            session="session",
            id="capture",
            interval_ns=(1, 2),
            roots=("root",),
            nodes={"root": node or NativeNode(name="window")},
            coordinate_spaces={"desktop": CoordinateSpace("point", "desktop_top_left", "down")},
        ),
        **changes,
    )


class DriverValueContracts(TestCase):
    def test_scalars_and_metadata_keep_native_interpretation(self):
        reader = wire()
        for scalar in (None, True, 123, "中文\ud800", 1.5, -0.0):
            with self.subTest(scalar=repr(scalar)):
                value = reader.value({"nativeType": "provider", "data": scalar})
                self.assertIs(type(value), type(scalar))
                self.assertEqual(value, scalar)
        self.assertEqual(
            reader.value({"nativeType": "CFDate", "data": 12}), {"nativeType": "CFDate", "absoluteTime": 12}
        )
        self.assertEqual(
            reader.value(
                {
                    "nativeType": "provider",
                    "data": 1,
                    "metadata": [["enum_name", {"nativeType": "CFString", "data": "one"}]],
                }
            ),
            "one",
        )
        utf16 = "a\ud800".encode("utf-16le", "surrogatepass")
        self.assertEqual(
            reader.value(
                {
                    "nativeType": "CFString",
                    "data": "preview",
                    "raw": base64.b64encode(utf16).decode("ascii"),
                    "metadata": [["raw_encoding", {"nativeType": "CFString", "data": "utf-16le"}]],
                }
            ),
            "a\ud800",
        )

    def test_shortcut_does_not_accept_unknown_fields_or_invalid_payloads(self):
        reader = wire()
        for payload in (
            {"nativeType": "CFString", "data": "value", "unknown": True},
            {"nativeType": "CFString", "data": "value", "metadata": {}},
            {"nativeType": "CFString", "data": "value", "raw": "%%%"},
            {"nativeType": "CFNumber", "data": math.nan},
            {"nativeType": "CFString", "data": {"ref": "missing"}},
            {"nativeType": "CFString", "data": ["not tagged"]},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                reader.value(payload)


class ImmutableObservationContracts(TestCase):
    def test_external_sequence_subclasses_are_detached_like_builtin_containers(self):
        class ExternalList(list):
            pass

        class ExternalTuple(tuple):
            pass

        nested = {"label": "original"}
        source = ExternalList([ExternalTuple((nested,))])
        value = Supplement("provider", source)
        nested["label"] = "changed"
        source.append("new")
        self.assertIs(type(value.value), tuple)
        self.assertIs(type(value.value[0]), tuple)
        self.assertEqual(value.value, (({"label": "original"},),))
        validate_query_observation(observation(NativeNode(supplements=(value,))))

    def test_nested_mutable_values_detach_even_through_external_mapping_proxy(self):
        source = {"items": [{"label": "original"}]}
        value = Supplement("provider", MappingProxyType(source))
        source["items"][0]["label"] = "changed"
        self.assertEqual(value.value["items"][0]["label"], "original")
        with self.assertRaises(TypeError):
            value.value["items"][0]["label"] = "mutated"
        cycle = []
        cycle.append(cycle)
        with self.assertRaisesRegex(ValueError, "acyclic"):
            Supplement("provider", cycle)

    def test_issue_index_is_not_a_schema_field_and_roundtrip_is_unchanged(self):
        capture = observation(issues=(ReadIssue(("nodes", "root", "name"), "partial"),))
        expected = encode_observation(capture)
        self.assertIs(capture.issue_index, capture.issue_index)
        self.assertFalse(any(item.name.startswith("_cached") for item in fields(capture)))
        self.assertEqual(encode_observation(capture), expected)
        self.assertEqual(decode_observation(expected), capture)

    def test_recursive_extra_values_and_all_record_checks_remain_valid(self):
        node = NativeNode(
            name="window",
            geometry={"bounds": Geometry("desktop", 1, 2, 3, 4, True)},
            actions=(Action("click", key="AXPress"),),
            relations={"children": Relation("children", ("root",), total=1, offset=0, positions=(0,))},
            supplements=(
                Supplement(
                    "provider",
                    {"nested": (ElementValue("root"), datetime(2026, 1, 1, tzinfo=UTC), math.nan)},
                ),
            ),
        )
        capture = observation(node)
        validate_query_observation(capture)
        self.assertEqual(decode_observation(encode_observation(capture)), capture)

    def test_type_validation_rejects_wrong_nested_values_and_excessive_depth(self):
        nested = "leaf"
        for _ in range(100):
            nested = (nested,)
        for node in (
            NativeNode(name=12),
            NativeNode(geometry={"bounds": Geometry("desktop", x=True)}),
            NativeNode(actions=(Action("click", target=(False,)),)),
            NativeNode(supplements=(Supplement("provider", datetime(2026, 1, 1)),)),
            NativeNode(supplements=(Supplement("provider", nested),)),
            NativeNode(text=TextInfo(source="provider", character_count=True)),
        ):
            with self.subTest(node=node), self.assertRaises(ValueError):
                validate_query_observation(observation(node))

    def test_semantic_validation_rejects_invalid_references_ranges_and_paths(self):
        invalid = (
            observation(roots=("missing",)),
            observation(NativeNode(supplements=(Supplement("provider", ElementValue("missing")),))),
            observation(NativeNode(geometry={"bounds": Geometry("missing", 1, 2, 3, 4)})),
            observation(NativeNode(relations={"children": Relation("children", ("missing",))})),
            observation(NativeNode(relations={"children": Relation("children", ("root",), total=0)})),
            observation(issues=(ReadIssue(("nodes", "missing"), "partial"),)),
            observation(issues=(ReadIssue(("unknown",), "partial"),)),
            observation(
                issues=(
                    ReadIssue(
                        ("nodes", "root"),
                        "partial",
                        continuation=ReadRef("next", "text", "live", total=2, delivered=(0, 3)),
                    ),
                )
            ),
        )
        for capture in invalid:
            with self.subTest(capture=capture), self.assertRaises(ValueError):
                validate_query_observation(capture)
