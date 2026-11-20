# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Retain observation codecs and compressed frame contracts across the Rust migration."""

import base64
import json
import math
from copy import deepcopy
from dataclasses import fields, replace
from datetime import UTC, datetime
from struct import pack
from unittest import TestCase

import pytest

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
from computer_use.protocol.observation.validation import _check_type


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

    def test_scalar_matches_keep_the_union_depth_limit_and_first_error(self):
        _check_type(None, str | None, ("name",), depth=95)
        with self.assertRaisesRegex(ValueError, r"observation: name: value does not match its declared type"):
            _check_type(None, str | None, ("name",), depth=96)
        with self.assertRaisesRegex(ValueError, r"observation: name: record nesting budget exceeded"):
            _check_type(None, str | None, ("name",), depth=97)

        _check_type(Geometry("desktop"), Geometry, ("bounds",), depth=94)
        with self.assertRaisesRegex(ValueError, r"observation: bounds.x: value does not match its declared type"):
            _check_type(Geometry("desktop"), Geometry, ("bounds",), depth=95)

    def test_scalar_tuple_checks_keep_original_index_and_depth(self):
        field_path = tuple[str | int, ...]
        _check_type(("nodes", 1), field_path, ("path",), depth=94)
        with self.assertRaisesRegex(ValueError, r"observation: path.0: value does not match its declared type"):
            _check_type(("nodes", 1), field_path, ("path",), depth=95)
        with self.assertRaisesRegex(ValueError, r"observation: path.1: value does not match its declared type"):
            _check_type(("nodes", True, None), field_path, ("path",))
        _check_type(("root",), tuple[str, ...], ("roots",), depth=95)
        with self.assertRaisesRegex(ValueError, r"observation: roots.0: record nesting budget exceeded"):
            _check_type(("root",), tuple[str, ...], ("roots",), depth=96)

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


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _native(value, kind="provider"):
    return {"data": value, "nativeType": kind}


def _batch(owner=0, index=1, **coverage):
    return {
        "details": [["batch_owner", {"metadataValue": owner}], ["batch_index", {"metadataValue": index}]],
        **coverage,
    }


def _relative_url(path):
    return {
        "data": path,
        "metadata": [["base", _native("file:///tmp/parent/")]],
        "nativeType": "CFURL",
    }


def _payload():
    return {
        "kind": "observation",
        "session": "session",
        "platform": "macos",
        "roots": ["root"],
        "nodes": [
            {
                "id": "root",
                "source": "macos.ax",
                "kind": "element",
                "nativeType": "AXUIElement",
                "properties": [
                    {"key": ["macos.ax", "AXRole"], "value": _native("AXWindow"), "read": _batch()},
                    {"key": ["macos.ax", "AXTitle"], "value": _native("NaN / Infinity / 雪")},
                    {"key": ["macos.ax", "AXURL"], "value": _relative_url("first.txt")},
                    {
                        "key": ["macos.ax", "AXHelp"],
                        "value": None,
                        "state": "timeout",
                        "read": _batch(
                            complete=False, issues=[{"nativeStatus": {"metadataValue": 0}, "reason": "timeout"}]
                        ),
                    },
                    {
                        "key": ["macos.ax", "AXLineForIndex"],
                        "value": _native(1),
                        "arguments": [_native(2)],
                        "read": _batch(complete=True, issues=[]),
                    },
                    {
                        "key": ["toolkit", "attribute_names"],
                        "value": {
                            "data": {"items": [_native("AXRole", "CFString"), _native("AXTitle", "CFString")]},
                            "nativeType": "CFArray",
                        },
                        "read": {"complete": True, "details": [["producer_role", _native("toolkit")]]},
                    },
                ],
            }
        ],
        "read": {
            "intervalNs": [1, 2],
            "details": [["surface", _native("surface")], ["observation_id", _native("capture")]],
        },
        "metadataValues": [_native(7), _native(0)],
    }


def _pool(items):
    return b"".join(pack("<I", len(item)) + item for item in items)


def _frame(literal, gaps, keys, reads):
    key_bytes, read_bytes = _pool(keys), _pool(reads)
    gap_bytes = b"".join(pack("<II", length, tag) for length, tag in gaps)
    header = pack("<4sIIII", b"CUS2", len(literal), len(key_bytes), len(read_bytes), len(gap_bytes))
    return b"".join((header, literal, key_bytes, read_bytes, gap_bytes))


def _encoded_read(read):
    details = read.get("details") if isinstance(read, dict) else None
    if isinstance(details, list) and len(details) == 2:
        owner, index = (pair[1].get("metadataValue") for pair in details)
        if (
            type(owner) is not int
            or type(index) is not int
            or not 0 <= owner < 2**32
            or not 0 <= index < 2**32
            or details != _batch(owner, index)["details"]
        ):
            return b"j" + _json(read)
        if set(read) == {"details"}:
            return b"b" + pack("<II", owner, index)
        if set(read) == {"details", "complete", "issues"}:
            complete = read["complete"]
            if complete is None or type(complete) is bool:
                tag = {True: b"t", False: b"f", None: b"n"}[complete]
                return tag + pack("<II", owner, index) + _json(read["issues"])
    return b"j" + _json(read)


def _capsule(payload, *, read_encoder=_encoded_read):
    """Produce test packets by explicit placeholder removal, independently of the parser."""
    replaced = deepcopy(payload)
    slots = []
    pools = ({}, {})
    for node in replaced["nodes"]:
        for prop in node["properties"] or ():
            for kind, field in enumerate(("key", "read")):
                if field not in prop:
                    continue
                fragment = _json(prop[field]) if kind == 0 else read_encoder(prop[field])
                pool = pools[kind]
                index = pool.setdefault(fragment, len(pool))
                marker = f"__test_segmented_slot_{len(slots)}__"
                prop[field] = marker
                slots.append((_json(marker), index << 1 | kind))
    gaps = []
    chunks = []
    remaining = _json(replaced)
    for marker, tag in slots:
        before, found, remaining = remaining.partition(marker)
        assert found
        gaps.append((len(before), tag))
        chunks.append(before)
    chunks.append(remaining)
    return _frame(b"".join(chunks), gaps, *pools)


_LIMIT = 64 * 1024 * 1024


@pytest.fixture
def open_segmented(native_read):
    def open_raw(frame, max_bytes=_LIMIT):
        return native_read.open_read_frame("cus2", frame, max_bytes)

    return open_raw


def test_segmented_observation_preserves_reply_budget_and_exact_diagnostics(native_read, open_segmented):
    payload = _payload()
    raw = _json(payload)
    segmented = open_segmented(_capsule(payload), max_bytes=len(raw))
    original = native_read.open_read_frame("json", raw, len(raw))
    assert segmented.diagnostic_bytes() == original.diagnostic_bytes() == raw
    assert segmented.raw_result() == original.raw_result() == payload
    target = ("session", None, None, "surface", "root")
    left, right = [frame.capture("window", []).with_aliases({"root": "0"}) for frame in (segmented, original)]
    assert left.element_details("0", target) == right.element_details("0", target)


def test_repeated_direct_read_outputs_remain_independently_mutable(open_segmented):
    payload = _payload()
    status = _native({"fields": [["rawResult", _native({"items": [_native("native")]})]]})
    read = {"complete": False, "issues": [{"reason": "unsupported", "nativeStatus": status}]}
    payload["nodes"][0]["properties"] = [
        {"key": ["macos.ax", name], "value": None, "state": "unsupported", "read": read}
        for name in ("AXHelp", "AXDescription")
    ]
    frame = open_segmented(_capsule(payload))
    raw = frame.raw_result()
    first, second = raw["nodes"][0]["properties"]
    first["read"]["issues"][0]["nativeStatus"]["data"]["fields"].append(["mutation", _native("mutation")])
    assert second["read"] == read
    assert frame.diagnostic_bytes() == _json(payload)


@pytest.mark.parametrize(
    "changes",
    (
        {"key": ["macos.ax", True], "state": "invalid"},
        {"key": ["", "AXRole"], "value": {"data": []}},
        {"state": "invalid", "read": {"unexpected": True}},
        {"arguments": {}, "read": {"unexpected": True}},
        {"read": _batch(complete=1, issues={})},
        {"read": _batch(complete=False, issues={})},
        {"read": _batch(owner=9, complete=False, issues={})},
    ),
)
def test_segmented_admission_retains_legacy_property_error_order(native_read, open_segmented, changes):
    payload = _payload()
    payload["nodes"][0]["properties"][0].update(changes)

    with pytest.raises(ValueError) as old:
        native_read.open_read_frame("json", _json(payload), _LIMIT)
    with pytest.raises(ValueError) as new:
        open_segmented(_capsule(payload), max_bytes=_LIMIT)
    assert str(new.value) == str(old.value)


def test_declared_frames_are_bounded_by_their_restored_size_before_admission(open_segmented):
    payload = _payload()
    payload["nodes"][0]["properties"] = [
        {"key": ["macos.ax", "AXRole"], "value": _native("AXWindow"), "read": _batch()} for _ in range(50)
    ]
    capsule = _capsule(payload)
    assert len(capsule) < len(_json(payload))
    with pytest.raises(ValueError):
        open_segmented(capsule, max_bytes=len(capsule))


def test_malformed_frames_are_also_bounded_by_their_synthetic_size(open_segmented):
    prefix = b'{"kind":"observation","nodes":['
    frame = _frame(prefix + b"," * 99 + b"]}", [(len(prefix), 1)] + [(1, 1)] * 99, [], [b"jnull"])
    with pytest.raises(ValueError):
        open_segmented(frame, max_bytes=len(frame))


def test_capsule_rejects_an_unused_pool_fragment(open_segmented):
    frame = _frame(b'{"kind":"observation"}', [], [b"[]"], [])
    with pytest.raises(ValueError):
        open_segmented(frame, max_bytes=_LIMIT)


@pytest.mark.parametrize("length, tag", ((99, 0), (8, 22)))
def test_capsule_rejects_a_gap_or_reference_beyond_its_owner(open_segmented, length, tag):
    frame = _frame(b'{"kind":"observation","key":}', [(length, tag)], [b"[]"], [])
    with pytest.raises(ValueError):
        open_segmented(frame, max_bytes=_LIMIT)


def test_capsule_rejects_a_gap_inside_a_quoted_value(open_segmented):
    prefix = b'{"kind":"observation","session":"'
    frame = _frame(prefix + b'"}', [(len(prefix), 0)], [b"[]"], [])
    with pytest.raises(ValueError):
        open_segmented(frame, max_bytes=_LIMIT)


def test_capsule_rejects_a_literal_nonfinite_constant_outside_slots(open_segmented):
    prefix = b'{"kind":"observation","nodes":'
    frame = _frame(prefix + b',"session":NaN}', [(len(prefix), 0)], [b"[]"], [])
    with pytest.raises(ValueError):
        open_segmented(frame, max_bytes=_LIMIT)


def test_capsule_rejects_a_slot_overwritten_by_a_duplicate_json_key(open_segmented):
    payload = _payload()
    payload["nodes"][0]["properties"] = [{"key": "first", "value": _native("AXWindow")}]
    prefix, suffix = _json(payload).split(b'"first"')
    middle = b',"key":'
    frame = _frame(
        prefix + middle + suffix,
        [(len(prefix), 0), (len(middle), 0)],
        [b'["macos.ax","AXRole"]'],
        [],
    )
    with pytest.raises(ValueError):
        open_segmented(frame, max_bytes=_LIMIT)


def test_capsule_rejects_a_read_slot_in_a_key_position(open_segmented):
    payload = _payload()
    payload["nodes"][0]["properties"] = [{"key": "first", "value": _native("AXWindow")}]
    prefix, suffix = _json(payload).split(b'"first"')
    frame = _frame(prefix + suffix, [(len(prefix), 1)], [], [b"jnull"])
    with pytest.raises(ValueError):
        open_segmented(frame, max_bytes=_LIMIT)


@pytest.mark.parametrize("format_name", ["json", "cus2"])
def test_compiled_native_read_rejects_a_non_observation_result(native_read, format_name):
    body = b'{"kind":"not-observation"}'
    if format_name == "cus2":
        body = pack("<4sIIII", b"CUS2", len(body), 0, 0, 0) + body

    with pytest.raises(ValueError, match="^protocol: expected an observation frame result$"):
        native_read.open_read_frame(format_name, body, _LIMIT)


def test_compiled_native_jsonl_diagnostics_match_the_worker_serializer(native_read):
    session = "00000000-0000-0000-0000-000000000001"
    base = json.dumps(
        {key: value for key, value in dict(_payload(), session=session).items() if key != "metadataValues"},
        ensure_ascii=True,
    ).encode()[:-1]
    envelope = b'{"version":6,"session":"' + session.encode() + b'","id":1,"result":'

    def result(*values):
        return base + b' , "metadataValues": [' + b", ".join(values) + b"]}"

    def number(lexeme):
        return b'{"nativeType":"CFNumber","data":' + lexeme + b"}"

    valid = result(
        rb'{"data":"old","nativeType":"CFString","data":"\u96ea\ud83e\uddea\b\f\t\r\n\/"}',
        rb'{"nativeType":"CFString","data":"\u0085\u2028\u2029\u001f"}',
        *(number(lexeme) for lexeme in (b"1E15", b"5e-324", b"-0", b"-0.0", b"9223372036854775808", b"9" * 4300)),
        number(b'{"float":"inf"}'),
    )
    overflow = result(number(b"1e9999"))
    surrogate = result(rb'{"nativeType":"CFString","data":"\ud800"}')
    frame = native_read.open_read_envelope(envelope + valid + b"}", _LIMIT)["result"]
    expected = json.dumps(json.loads(valid), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
        "utf-8", "backslashreplace"
    )
    assert frame.jsonl_diagnostic_bytes() == expected
    assert frame.jsonl_diagnostic_bytes() == expected
    assert frame.diagnostic_bytes() == valid
    assert frame.raw_result() == json.loads(valid)
    assert frame.capture("window", []) is not None
    overflowing = native_read.open_read_envelope(envelope + overflow + b"}", _LIMIT)["result"]
    with pytest.raises(ValueError, match="special float needs a tag"):
        overflowing.capture("window", [])
    with pytest.raises(ValueError):
        native_read.open_read_envelope(envelope + surrogate + b"}", _LIMIT)
    assert rb'"\ud800"' in json.dumps(json.loads(surrogate), ensure_ascii=False).encode("utf-8", "backslashreplace")
    failure = {
        "version": 6,
        "session": session,
        "id": 1,
        "error": {
            "code": "deadline_exceeded",
            "target": "inspect",
            "effect": "none",
            "detail": "collection deadline",
            "next": "observe",
        },
    }
    assert native_read.open_read_envelope(json.dumps(failure).encode(), _LIMIT) == failure
