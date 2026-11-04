# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read metadata sharing preserves complete native evidence without repeat decoding."""

import base64
import copy
from collections.abc import Mapping
from dataclasses import fields

import pytest
from computer_use.protocol.observation import encode_observation
from computer_use.protocol.observation.adapters import decode_driver_reply
from computer_use.protocol.observation.adapters.wire import DecodedDriverObservation, DriverWire, decode_query_facts


def scalar(value):
    return {"nativeType": "provider", "data": value}


def payload(*, platform="macos"):
    return {
        "kind": "observation",
        "session": "session",
        "platform": platform,
        "roots": ["root"],
        "nodes": [
            {
                "id": "root",
                "source": "macos.ax",
                "kind": "element",
                "nativeType": "AXUIElement",
                "properties": [{"key": ["macos.ax", "AXRole"], "value": scalar("AXWindow")}],
            }
        ],
        "read": {
            "intervalNs": [1, 2],
            "details": [["surface", scalar("surface")], ["observation_id", scalar("capture")]],
        },
    }


def plain(value):
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    return value


def evidence(value):
    decoded = DecodedDriverObservation(value)
    reply = decode_driver_reply(decoded)
    return {
        "observation": encode_observation(reply.observation),
        "metadata": {field.name: plain(getattr(reply.metadata, field.name)) for field in fields(reply.metadata)},
        "facts": plain(decode_query_facts(decoded)),
    }


def test_pool_value_is_validated_once_and_shared_immutably_across_all_read_slots(monkeypatch):
    raw = payload()
    shared = {
        "nativeType": "record",
        "data": {
            "fields": [
                [
                    "items",
                    {
                        "nativeType": "CFArray",
                        "data": {"items": [scalar("field"), scalar("another")]},
                    },
                ]
            ]
        },
    }
    raw["metadataValues"] = [shared]
    marker = {"metadataValue": 0}
    raw["read"]["details"].append(["first", marker])
    raw["nodes"][0]["read"] = {"details": [["second", marker]]}
    raw["nodes"][0]["properties"][0]["read"] = {"details": [["third", marker]]}
    calls = []
    original = DriverWire.value

    def count(self, value, depth=0):
        if value is shared:
            calls.append(value)
        return original(self, value, depth)

    monkeypatch.setattr(DriverWire, "value", count)
    decoded = DecodedDriverObservation(raw)
    wire = decoded.wire
    first = wire.details["first"]
    second = wire.read_info(raw["nodes"][0]["read"])["details"]["second"]
    third = wire.property(raw["nodes"][0]["properties"][0]).read["details"]["third"]
    decode_driver_reply(decoded)
    assert len(calls) == 1
    assert first is second is third
    assert first["items"] == ("field", "another")
    with pytest.raises(TypeError):
        first["items"] = ()
    with pytest.raises(TypeError):
        first["items"][0] = "changed"
    shared["data"]["fields"][0][1]["data"]["items"][0]["data"] = "external mutation"
    assert first["items"][0] == "field"


def test_pooled_status_continuation_and_detail_keep_complete_normalized_evidence():
    original = payload()
    failure = {
        "nativeType": "record",
        "data": {
            "fields": [
                ["state", scalar("timeout")],
                ["detail", scalar("provider timeout")],
                ["wrappedNativeValue", {"nativeType": "record", "data": {"fields": [["unusual", scalar(42)]]}}],
            ]
        },
    }
    continuation = {"nativeType": "record", "data": {"fields": [["textRef", scalar("token")]]}}
    original["nodes"][0]["properties"].append(
        {
            "key": ["macos.ax", "AXHelp"],
            "state": "timeout",
            "value": None,
            "read": {
                "complete": False,
                "details": [["extra", scalar("kept")]],
                "issues": [
                    {
                        "reason": "timeout",
                        "detail": "provider timeout",
                        "nativeStatus": failure,
                        "continuation": continuation,
                    }
                ],
            },
        }
    )
    compact = copy.deepcopy(original)
    compact["metadataValues"] = [failure, continuation, scalar("kept")]
    read = compact["nodes"][0]["properties"][-1]["read"]
    read["details"][0][1] = {"metadataValue": 2}
    read["issues"][0]["nativeStatus"] = {"metadataValue": 0}
    read["issues"][0]["continuation"] = {"metadataValue": 1}
    assert evidence(compact) == evidence(original)


@pytest.mark.parametrize(
    "failed_value",
    [
        {"nativeType": "CFDate", "data": 42},
        {"nativeType": "CFURL", "data": "doc.txt", "metadata": [["base", scalar("file:///tmp/parent/")]]},
        {
            "nativeType": "CFString",
            "data": "preview",
            "raw": base64.b64encode("a\ud800".encode("utf-16le", "surrogatepass")).decode(),
            "metadata": [["raw_encoding", scalar("utf-16le")]],
        },
        {"nativeType": "provider", "data": 1, "metadata": [["enum_name", scalar("one")]]},
        {"nativeType": "CFNumber", "data": {"float": "nan"}},
        {"nativeType": "CFArray", "data": {"items": [{"issue": {"reason": "unavailable", "nativeStatus": scalar(9)}}]}},
    ],
)
def test_pooled_special_native_values_keep_identical_semantic_facts(failed_value):
    original = payload()
    status = {
        "nativeType": "record",
        "data": {
            "fields": [
                ["state", scalar("error")],
                ["wrappedNativeValue", failed_value],
            ]
        },
    }
    original["nodes"][0]["properties"].append(
        {
            "key": ["macos.ax", "AXHelp"],
            "state": "error",
            "value": None,
            "read": {"complete": False, "issues": [{"reason": "error", "nativeStatus": status}]},
        }
    )
    compact = copy.deepcopy(original)
    compact["metadataValues"] = [status]
    compact["nodes"][0]["properties"][-1]["read"]["issues"][0]["nativeStatus"] = {"metadataValue": 0}
    left, right = evidence(compact), evidence(original)
    # Encoded observations use tagged NaN values, not host float equality.
    assert left == right


@pytest.mark.parametrize("index", [-1, 1, True, False, 0.0, "0", None])
def test_metadata_reference_requires_a_declared_exact_integer_index(index):
    raw = payload()
    raw["metadataValues"] = [scalar("entry")]
    raw["read"]["details"].append(["invalid", {"metadataValue": index}])
    with pytest.raises(ValueError, match="metadata value reference"):
        DecodedDriverObservation(raw)


@pytest.mark.parametrize("where", ["property", "identity", "pool", "native_metadata", "native_collection"])
def test_reference_marker_is_confined_to_read_metadata_slots(where):
    raw = payload()
    marker = {"metadataValue": 0}
    raw["metadataValues"] = [scalar("entry")]
    if where == "property":
        raw["nodes"][0]["properties"][0]["value"] = marker
    elif where == "identity":
        raw["nodes"][0]["identity"] = marker
    elif where == "pool":
        raw["metadataValues"][0] = marker
    elif where == "native_metadata":
        raw["metadataValues"][0] = {"nativeType": "provider", "data": "value", "metadata": [["invalid", marker]]}
    else:
        raw["metadataValues"][0] = {"nativeType": "CFArray", "data": {"items": [marker]}}
    with pytest.raises(ValueError):
        DecodedDriverObservation(raw)


@pytest.mark.parametrize(
    "table",
    [
        None,
        {},
        [scalar(float("inf"))],
        [{"nativeType": "AXUIElement", "data": {"ref": "missing"}}],
    ],
)
def test_even_unused_pool_entries_are_fully_validated(table):
    raw = payload()
    raw["metadataValues"] = table
    with pytest.raises(ValueError):
        DecodedDriverObservation(raw)


def test_reference_record_rejects_extra_fields_and_native_refs_remain_distinct():
    raw = payload()
    raw["metadataValues"] = [{"nativeType": "AXUIElement", "data": {"ref": "root"}}]
    raw["read"]["details"].append(["owner", {"metadataValue": 0}])
    wire = DriverWire(raw)
    assert wire.details["owner"] == {"nativeType": "AXUIElement", "node": "root"}
    raw["read"]["details"][-1][1]["unexpected"] = True
    with pytest.raises(ValueError):
        DriverWire(raw)


@pytest.mark.parametrize("platform", ["macos", "windows", "linux"])
def test_inline_values_and_optional_pool_use_one_platform_neutral_contract(platform):
    raw = payload(platform=platform)
    expected = DriverWire(raw).details
    raw["metadataValues"] = [scalar("surface")]
    raw["read"]["details"][0][1] = {"metadataValue": 0}
    assert DriverWire(raw).details == expected


def test_pool_reference_does_not_bypass_the_inline_value_nesting_budget():
    nested = scalar("leaf")
    for _ in range(48):
        nested = {"nativeType": "provider", "data": "value", "metadata": [["nested", nested]]}
    raw = payload()
    raw["metadataValues"] = [nested]
    DriverWire(raw)  # The unused entry is itself within the value-depth budget.
    raw["read"]["details"].append(["deep", {"metadataValue": 0}])
    with pytest.raises(ValueError, match="value budget"):
        DriverWire(raw)
    raw.pop("metadataValues")
    raw["read"]["details"][-1][1] = nested
    with pytest.raises(ValueError, match="value budget"):
        DriverWire(raw)
