# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider evidence through the native frame, capture, and detail boundaries."""

import base64
import hashlib
import json

import pytest
from computer_use import diagnostics
from computer_use.interaction.read.accessibility.frame import NativeReadFrame
from computer_use.interaction.read.accessibility.snapshot import NativeSnapshot


PROVIDERS = {
    "macos": ("macos.ax", "AXUIElement", "AXRole", "AXWindow", "AXTitle"),
    "windows": ("windows.uia", "IUIAutomationElement", "ControlType", "ControlType.Window", "Name"),
    "linux": ("linux.atspi", "AtspiAccessible", "role", "frame", "name"),
}


def value(data, native_type="nativeScalar"):
    return {"nativeType": native_type, "data": data}


def property_row(source, name, data, **evidence):
    return {"key": [source, name], "value": data, **evidence}


def provider_value(data):
    if isinstance(data, dict):
        return value({"fields": [[key, provider_value(item)] for key, item in data.items()]}, "record")
    if isinstance(data, list):
        return value({"items": [provider_value(item) for item in data]}, "list")
    return value(data)


def observation(platform):
    source, native_type, role, role_value, name = PROVIDERS[platform]
    return {
        "kind": "observation",
        "platform": platform,
        "session": "session",
        "roots": ["root"],
        "nodes": [
            {
                "id": "root",
                "source": source,
                "nativeType": native_type,
                "kind": "element",
                "properties": [
                    property_row(source, role, value(role_value)),
                    property_row(source, name, value("原窗口")),
                ],
            }
        ],
        "read": {
            "intervalNs": [10**16, 10**16 + 1_000_000],
            "details": [["observation_id", value("capture")], ["surface", value("surface")]],
        },
    }


def open_capture(engine, raw):
    frame = engine.open_read_frame("json", json.dumps(raw).encode("utf-8"), 64 * 1024)
    return frame.capture("window", [])


@pytest.mark.parametrize("platform", PROVIDERS)
def test_ordered_provider_facts_keep_wide_status_null_values_and_actions(native_read, platform):
    raw = observation(platform)
    source, native_type, role, role_value, name = PROVIDERS[platform]
    raw["metadataValues"] = [provider_value({"nativeDomain": "provider", "nativeCode": 2**70})]
    raw["nodes"][0]["properties"].extend(
        (
            property_row(source, name, value("第二个名字😀")),
            property_row(source, 32001, value(None), arguments=[value(42)]),
            property_row(
                source,
                "Missing",
                None,
                state="error",
                read={
                    "issues": [
                        {"reason": "failure", "nativeStatus": {"metadataValue": 0}, "detail": "explicit failure"}
                    ]
                },
            ),
            property_row("toolkit", "actions", provider_value([{"name": "provider-action"}]), read={"complete": True}),
        )
    )
    raw["nodes"].append(
        {
            "id": "outside",
            "source": source,
            "nativeType": native_type,
            "kind": "element",
            "properties": None,
            "read": {"issues": [{"reason": "outside_window_scope"}]},
        }
    )

    capture = open_capture(native_read, raw)
    alternate_name = f"{source}::{name}#2"
    null_name = f"{source}::32001#3"
    fields = [role, name, alternate_name, null_name, "Missing"]
    evidence = capture.lookup_evidence(fields)
    assert evidence["observed"]["startedAt"] == raw["read"]["intervalNs"][0] // 1_000_000
    assert evidence["observed"]["completedAt"] == evidence["observed"]["startedAt"] + 1
    assert capture.contains_observed("root")
    assert not capture.contains_observed("outside")
    root = next(node for node in evidence["nodes"] if node["node"] == "root")
    assert root["requestedFields"] == fields
    assert list(root["values"].items()) == [
        (role, role_value),
        (name, "原窗口"),
        (alternate_name, "第二个名字😀"),
        (null_name, None),
    ]
    assert root["fieldStatus"]["Missing"] == {
        "state": "error",
        "nativeDomain": "provider",
        "nativeCode": 2**70,
        "detail": "explicit failure",
    }
    assert root["actions"] == {"state": "complete", "items": [{"name": "provider-action"}]}


@pytest.mark.parametrize("platform", PROVIDERS)
def test_raw_diagnostic_view_keeps_admitted_provider_values(native_read, platform):
    raw = observation(platform)
    native = native_read.open_read_frame("json", json.dumps(raw).encode("utf-8"), 64 * 1024)
    frame = NativeReadFrame(native)

    def edit_callback_data(record):
        record["result"]["nodes"][0]["properties"][1]["value"]["data"] = "edited diagnostic"

    diagnostics.set_diagnostic_handler(edit_callback_data)
    try:
        diagnostics.diagnostic({"kind": "command_end"}, _native_observation=frame)
    finally:
        diagnostics.set_diagnostic_handler(None)

    assert frame.raw_result()["nodes"][0]["properties"][1]["value"]["data"] == "edited diagnostic"
    capture = frame.capture("window", [])
    name = PROVIDERS[platform][4]
    assert capture.lookup_evidence([name])["nodes"][0]["values"][name] == "原窗口"
    assert capture.node_view("root", ["name"]) == {"name": "原窗口"}


@pytest.mark.parametrize("platform", PROVIDERS)
def test_invalid_provider_binary_value_reports_wire_validation_error(native_read, platform):
    raw = observation(platform)
    source = PROVIDERS[platform][0]
    raw["nodes"][0]["properties"].append(
        property_row(source, "Payload", value({"bytes": "not base64!"}, "providerBlob"))
    )
    with pytest.raises(ValueError, match=r"^driver observation: invalid base64 data$"):
        open_capture(native_read, raw)


@pytest.mark.parametrize("extra", ["valid", "duplicate", "multiple_roots", "wrong_source", "unconfirmed"])
def test_macos_focus_requires_one_confirmed_application_seed(native_read, extra):
    raw = observation("macos")
    root = raw["nodes"][0]
    metadata = {
        "source": "target" if extra == "wrong_source" else "application",
        "attribute": "AXFocusedUIElement",
        "state": "unknown" if extra == "unconfirmed" else "confirmed",
    }
    root["properties"].append(
        property_row(
            "toolkit",
            "focused_read_seed",
            value({"ref": "field"}, "AXUIElement"),
            read={
                "complete": True,
                "details": [[key, value(item)] for key, item in metadata.items()],
            },
        )
    )
    raw["nodes"].append({**root, "id": "field", "properties": []})
    if extra == "multiple_roots":
        raw["roots"].append("field")
    if extra == "duplicate":
        root["properties"].append(property_row("toolkit", "focused_read_seed", None, state="absent"))

    capture = open_capture(native_read, raw)
    assert capture.focused_element == ("field" if extra == "valid" else None)
    evidence = capture.lookup_evidence(["toolkit::focused_read_seed#2"])
    assert evidence["nodes"][0]["values"]["toolkit::focused_read_seed#2"]["node"] == "field"


@pytest.mark.parametrize(
    ("position", "units", "expected_start", "expected_issue"),
    [
        (2, 4, 2, None),
        (3, 4, None, "Text endpoints split a UTF-16 character."),
        (2, 3, None, "anchor splits a UTF-16 character"),
        (True, 4, None, "Malformed text-range evidence cannot establish confirmed offsets."),
    ],
)
def test_windows_text_retains_confirmed_utf16_ranges_and_rejected_provider_evidence(
    native_read, position, units, expected_start, expected_issue
):
    raw = observation("windows")
    text = "中文😀"
    anchor = {
        "source": "Text.DocumentRange",
        "unit": "utf16",
        "units": units,
        "complete": False,
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
    }
    facts = {
        "Text.DocumentRange": text,
        "Text.CaretRange": {
            "source": "IUIAutomationTextPattern2.GetCaretRange",
            "values": {
                "isActive": True,
                "anchor": anchor,
                "range": {"start": position, "end": position, "degenerate": True},
            },
            "fieldStatus": {},
            "state": "complete",
        },
        "Text.LineRanges": {
            "source": "IUIAutomation.TextPattern.TextUnit.Line",
            "requestedUnit": "Line",
            "unitSupport": "unconfirmed",
            "anchor": anchor,
            "ranges": [{"values": {"start": 0, "end": 4, "degenerate": False, "text": text}, "fieldStatus": {}}],
            "state": "partial",
            "documentEndReached": True,
            "detail": "Fixture",
        },
        "UIA.ReferenceScopeEvidence": {"ownership": "confirmed", "input": "observationOnly", "detail": "Fixture"},
    }
    raw["nodes"][0]["properties"].extend(
        property_row("windows.uia", key, provider_value(data)) for key, data in facts.items()
    )
    capture = open_capture(native_read, raw)
    node = capture.node_view("root", ["text", "supplements"])
    text_info = node["text"]
    assert (text_info["content"], text_info["unit"]) == (text, "utf16_code_unit")
    assert text_info.get("caret", {}).get("start") == expected_start
    spans = [(span["start"], span["end"], span["text"]) for span in text_info.get("line_ranges", ())]
    assert spans == ([(0, 4, text)] if units == 4 else [])
    supplements = {entry["key"]: entry["value"] for entry in node["supplements"]}
    assert supplements["observation_scope"]["input"] == "observationOnly"
    if type(position) is bool:
        assert supplements["provider.windows.Text.CaretRange"]["values"]["range"]["start"] is True
    elif expected_issue is not None:
        assert supplements["text.unconfirmed_position"] == {"start": position, "end": position}
    numbered = NativeSnapshot.number(capture, {"root": "0"})
    details = numbered.numbered.element_details("0", ("session", None, None, "surface", None))
    if expected_issue is not None:
        assert expected_issue in details


@pytest.mark.parametrize(
    ("subrole", "count_after", "accepted"),
    [("AXStandardWindow", 3, True), ("AXStandardWindow", 2, False), ("AXSecureTextField", 3, False)],
)
def test_macos_parameterized_text_requires_confirmed_native_range(native_read, subrole, count_after, accepted):
    raw = observation("macos")
    counts = {
        "character_count_before": 3,
        "character_count_after": count_after,
        "character_count_before_status": 0,
        "character_count_after_status": 0,
        "native_status": 0,
    }
    raw["nodes"][0]["properties"].extend(
        (
            property_row("macos.ax", "AXValue", value("")),
            property_row("macos.ax", "AXSubrole", value(subrole)),
            property_row(
                "macos.ax",
                "AXStringForRange",
                value("中😀"),
                arguments=[provider_value({"location": 0, "length": 3})],
                read={
                    "complete": True,
                    "api": "AXUIElementCopyParameterizedAttributeValue",
                    "details": [[key, value(item)] for key, item in counts.items()],
                },
            ),
        )
    )
    capture = open_capture(native_read, raw)
    fields = capture.node_view("root", ["text"])
    if accepted:
        text = fields["text"]
        assert (text["content"], text["character_count"], text["unit"]) == ("中😀", 3, "utf16_code_unit")
    else:
        assert not fields.get("text", {}).get("content")


def test_malformed_provider_utf16_does_not_discard_the_observation(native_read):
    raw = observation("windows")
    # NativeObservationGraph.Value uses this encoding for a UIA BSTR with an isolated surrogate.
    units = base64.b64encode(b"\x00\xd8").decode("ascii")
    raw["nodes"][0]["properties"].append(
        property_row(
            "windows.uia",
            "HelpText",
            {
                "nativeType": "BSTR",
                "data": {"bytes": units},
                "metadata": [["string_encoding", value("utf-16le")]],
            },
        )
    )
    frame = native_read.open_read_frame("json", json.dumps(raw).encode("utf-8"), 64 * 1024)
    capture = frame.capture("window", [])
    assert capture.node_view("root", ["role", "name", "help"]) == {
        "role": "window",
        "name": "原窗口",
        "help": "\ufffd",
    }
    assert frame.raw_result() == raw
