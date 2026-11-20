# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Keep captured detail facts, membership, and numbers independent of rendering."""

import hashlib
import json
from dataclasses import replace

import pytest
from computer_use.interaction.read.accessibility.preparation import prepare_snapshot
from computer_use.interaction.read.accessibility.snapshot import NativeSnapshot
from computer_use.interaction.read.acquisition.view_facts import captured_identity
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.element_details import CapturedElementDetails
from computer_use.interaction.read.presentation.rendering.document import prepare_document
from computer_use.session.observation import lazy_observation

from regression.test_migrated_read_projection import macos_node, number_capture
from regression.test_migrated_read_provider_facts import (
    PROVIDERS,
    observation,
    open_capture,
    property_row,
    provider_value,
    value,
)


def read_with_issue(engine):
    raw = observation("macos")
    raw["read"]["details"][0][1]["data"] = "original-capture"
    root = macos_node("root", "AXWindow", "Captured window")
    root["properties"].append(
        property_row(
            "macos.ax",
            "AXHelp",
            None,
            state="unsupported",
            read={"issues": [{"reason": "unsupported", "detail": "provider detail 中文"}]},
        )
    )
    raw["nodes"] = [root]
    return number_capture(engine, raw)


def test_normal_document_does_not_request_native_detail_rendering(native_read):
    prepared = prepare_snapshot(read_with_issue(native_read))
    body = DisplayDocument.tree(prepared)
    details = DisplayDocument.tree(prepared, details=True)
    rendered = prepare_document(body).snapshots[id(prepared)]
    assert not rendered.includes_details
    assert "Captured window" in body.text
    assert "provider detail 中文" not in body.text

    complete = prepare_document(details).snapshots[id(prepared)]
    assert complete.includes_details
    assert "provider detail 中文" in details.text
    assert "original-capture" in details.text
    assert prepare_document(details).snapshots[id(prepared)] is complete
    assert prepare_document(body).snapshots[id(prepared)] is complete
    assert "provider detail 中文" not in body.text


def test_recomposed_detail_tree_keeps_exact_native_source(native_read):
    snapshot = read_with_issue(native_read)
    doc = DisplayDocument.tree(prepare_snapshot(snapshot), details=True).scoped(("scope",))
    copied = replace(doc, parts=tuple(replace(part) for part in doc.parts))

    assert copied.text == doc.text
    assert "provider detail 中文" in copied.text
    assert copied.parts[0].prepared.snapshot.capture is snapshot.capture


def test_sdk_paging_retains_exact_native_issue_and_does_not_reformat_each_page(native_read):
    prepared = prepare_snapshot(read_with_issue(native_read))
    body = DisplayDocument.tree(prepared)
    details = DisplayDocument.tree(prepared, details=True)
    observed = lazy_observation(body, details_document=details)
    assert "Captured window" in observed.text
    assert "provider detail 中文" not in observed.text
    assert not prepare_document(body).snapshots[id(prepared)].includes_details

    first = observed.read_details(length=97)
    pages = [first.text]
    cursor = first.next
    while cursor is not None:
        page = observed.read_details(cursor=cursor, length=97)
        pages.append(page.text)
        cursor = page.next
    complete = "".join(pages)
    assert len(complete) == first.total
    assert "Captured window" in complete
    assert "provider detail 中文" in complete and "original-capture" in complete
    assert observed.read_details(length=97).text == first.text
    source_with_details = prepare_document(details).snapshots[id(prepared)]
    assert source_with_details.includes_details
    assert prepare_document(details).snapshots[id(prepared)] is source_with_details


def test_capture_exports_cannot_mutate_native_retained_facts(native_read):
    raw = observation("macos")
    root = macos_node("root", "AXRow", "Captured row")
    native_range = value(provider_value({"location": 3, "length": 2})["data"], "AXValue.CFRange")
    root["properties"].append(property_row("macos.ax", "AXRowIndexRange", native_range))
    raw["nodes"] = [root]

    first = number_capture(native_read, raw)
    exported = first.capture.node_view("root")
    exported["name"] = "Edited display"
    exported["structure"]["row_span"] = 9
    with pytest.raises(TypeError):
        first.refs["1"] = "injected"
    second = NativeSnapshot.number(first.capture, {"root": "1"})
    assert second.capture is first.capture
    assert second.refs == {"1": "root"}
    assert second.capture.node_view("root")["structure"] == {"row_index": 3, "row_span": 2}
    details = CapturedElementDetails(((second, ()),))(1).text
    assert '"name": "Captured row"' in details
    assert '"structure.row_span": 2' in details


@pytest.mark.parametrize(
    ("role", "name", "fields", "expected"),
    [
        (
            "AXTextField",
            "Element",
            [("AXValue", value({"ref": "root"}, "AXUIElement"))],
            '"control_value.value": {"element":"root"}',
        ),
        (
            "AXTextField",
            "Date",
            [("AXValue", value(42, "CFDate"))],
            '"control_value.value": {"datetime":"2001-01-01T00:00:42+00:00"}',
        ),
        (
            "AXSlider",
            "NaN",
            [("AXValue", value({"float": "nan"}, "CFNumber")), ("AXMinValue", value(0))],
            '"numeric_value.value": {"number":"NaN"}',
        ),
        (
            "AXGroup",
            "Invalid geometry",
            [
                ("AXSize", value(provider_value({"width": -1, "height": 20})["data"], "AXValue.CGSize")),
                ("AXPosition", value(provider_value({"x": 0, "y": 0})["data"], "AXValue.CGPoint")),
            ],
            '"width":-1,"height":20,"valid":false',
        ),
        (
            "AXGroup",
            "Opaque payload",
            [("OpaqueFixture", provider_value({"nested": [1, {"two": "2"}]}))],
            '"value":{"nested":[1,{"two":"2"}]}',
        ),
    ],
)
def test_native_tags_and_details_keep_identity_after_renumbering(native_read, role, name, fields, expected):
    raw = observation("macos")
    root = macos_node("root", role, name)
    root["properties"].extend(property_row("macos.ax", key, item) for key, item in fields)
    raw["nodes"] = [root]

    first = number_capture(native_read, raw)
    second = NativeSnapshot.number(first.capture, {"root": "1"})
    assert first.refs == {"0": "root"}
    assert second.refs == {"1": "root"}
    assert first.capture is second.capture
    for ref, snapshot in enumerate((first, second)):
        details = CapturedElementDetails.from_document(DisplayDocument.tree(prepare_snapshot(snapshot)))(ref).text
        assert f"Captured element {ref}:" in details
        assert f'"name": "{name}"' in details
        assert expected in details


@pytest.mark.parametrize("platform", ["linux", "windows"])
def test_prepared_document_keeps_exact_native_capture_identity(native_read, platform):
    capture = open_capture(native_read, observation(platform))
    numbered = NativeSnapshot.number(capture, {"root": "0"})
    prepared = prepare_snapshot(numbered)
    tree = DisplayDocument.tree(prepared)
    details = CapturedElementDetails.from_document(tree)(0).text

    assert captured_identity(prepared.snapshot) is capture
    assert prepared.snapshot.backend == platform
    assert prepared.snapshot.interval_ns == (10**16, 10**16 + 1_000_000)
    assert prepared.snapshot.refs == {"0": "root"}
    assert "0 window 原窗口" in tree.text
    assert f'"backend":"{platform}"' in details
    assert '"observation":"capture","node":"root"' in details


def test_native_detail_issue_lines_keep_order_and_exact_continuation(native_read):
    raw = observation("macos")
    root = macos_node("root", "AXTextArea", "Original")
    root["properties"].extend(
        (
            property_row("toolkit", "actions", provider_value([]), read={"complete": True}),
            property_row(
                "macos.ax",
                "AXHelp",
                None,
                state="unsupported",
                read={
                    "issues": [{"reason": "unsupported", "detail": "中文\u2028二行\x85🙂"}],
                },
            ),
            property_row(
                "macos.ax",
                "AXValue",
                value("Captured"),
                read={
                    "complete": False,
                    "issues": [
                        {"reason": "partial", "continuation": provider_value({"textRef": "tail", "totalUnits": 20})}
                    ],
                },
            ),
        )
    )
    raw["nodes"] = [root]
    rendered = DisplayDocument.tree(prepare_snapshot(number_capture(native_read, raw)), details=True).text
    lines = [line.strip() for line in rendered.splitlines() if line.startswith('\t{"index"')]
    assert len(lines) == 2
    assert r"\u2028" in lines[0] and r"\u0085" in lines[0]
    first, second = [json.loads(line) for line in lines]
    assert [row["index"] for row in (first, second)] == [0, 1]
    assert [row["ref"] for row in (first, second)] == ["0", "0"]
    assert first["issue"]["path"] == ["nodes", "root", "help"]
    assert first["issue"]["detail"] == "中文\u2028二行\x85🙂"
    assert second["issue"]["path"] == ["nodes", "root", "control_value", "value"]
    assert second["issue"]["continuation"] == {
        "token": "tail",
        "kind": "text",
        "consistency": "captured",
        "unit": "utf16_code_unit",
        "total": 20,
        "delivered": [0, 8],
    }


@pytest.mark.parametrize("platform", ["linux", "windows"])
def test_deep_native_document_retains_leaf_and_original_capture(native_read, platform):
    raw = observation(platform)
    source, native_type, role, root_role, name = PROVIDERS[platform]
    collection, group, label = (
        ("RawViewChildren", "ControlType.Pane", "ControlType.Text")
        if platform == "windows"
        else ("children", "panel", "label")
    )
    tokens = ["root", *(f"deep-{index}" for index in range(1, 451))]
    nodes = []
    for index, token in enumerate(tokens):
        children = tokens[index + 1 : index + 2]
        native_children = value(
            {"items": [value({"ref": child}, native_type) for child in children]}, f"{native_type}[]"
        )
        node_role = root_role if index == 0 else (group if children else label)
        properties = [
            property_row(source, role, value(node_role)),
            property_row(
                source,
                collection,
                native_children,
                read={
                    "complete": True,
                    "details": [
                        [key, value(item)]
                        for key, item in {
                            "relation": True,
                            "certainty": "confirmed",
                            "state": "complete",
                            "total": len(children),
                        }.items()
                    ],
                },
            ),
        ]
        if not children:
            properties.append(property_row(source, name, value("Deepest captured text")))
        nodes.append(
            {"id": token, "kind": "element", "source": source, "nativeType": native_type, "properties": properties}
        )
    raw["nodes"] = nodes

    body = json.dumps(raw).encode()
    capture = native_read.open_read_frame("json", body, len(body)).capture("window", [])
    snapshot = NativeSnapshot.number(capture, {token: str(index) for index, token in enumerate(capture.source_tokens)})
    prepared = prepare_snapshot(snapshot)
    details = DisplayDocument.tree(prepared, details=True).text
    assert "Deepest captured text" in details
    assert captured_identity(prepared.snapshot) is capture
    assert snapshot.refs["450"] == "deep-450"


@pytest.mark.parametrize("end", [3, 2], ids=["exact-emoji", "split-surrogate-pair"])
def test_windows_embedded_membership_survives_unconfirmed_preview(native_read, end):
    raw = observation("windows")
    source = "windows.uia"
    document = raw["nodes"][0]
    document["properties"][0]["value"]["data"] = "ControlType.Document"
    body = "A😀B"
    facts = {
        "Text.DocumentRange": body,
        "Text.ObjectRanges": {
            "source": "IUIAutomation.TextPattern.RangeFromChild",
            "anchor": {
                "source": "Text.DocumentRange",
                "unit": "utf16",
                "units": 4,
                "complete": True,
                "sha256": hashlib.sha256(body.encode()).hexdigest(),
            },
            "objects": [{"node": "child", "values": {"start": 1, "end": end, "degenerate": False}, "fieldStatus": {}}],
            "total": 1,
            "state": "complete",
            "order": "nativeEndpoints",
        },
    }
    document["properties"].extend(property_row(source, key, provider_value(item)) for key, item in facts.items())
    raw["nodes"].append(
        {
            **document,
            "id": "child",
            "properties": [
                property_row(source, "ControlType", value("ControlType.Button")),
                property_row(source, "Name", value("Embedded Button")),
            ],
        }
    )

    capture = open_capture(native_read, raw)
    snapshot = NativeSnapshot.number(capture, {"root": "0", "child": "2"})
    visible = DisplayDocument.tree(prepare_snapshot(snapshot)).text
    if end == 3:
        assert "Embedded objects of 0 (UTF-16): 2@1..3" in visible
        assert 'Reading sequence of 0: "A" → element 2 "😀" → "B"' in visible
    else:
        assert "Embedded objects of 0 (UTF-16): 2 (position unconfirmed)" in visible
        assert "Reading sequence" not in visible
        supplements = capture.node_view("root")["supplements"]
        unconfirmed = next(row["value"] for row in supplements if row["key"] == "text.unconfirmed_position")
        assert unconfirmed == {"start": 1, "end": 2}
    assert "2 button Embedded Button" in visible


@pytest.mark.parametrize("public_api", [False, True])
def test_each_preparation_uses_its_own_aliases_for_multiple_roots(native_read, public_api):
    raw = observation("macos")
    raw["roots"] = ["left", "right"]
    raw["nodes"] = [macos_node("left", "AXButton", "First"), macos_node("right", "AXButton", "Second")]
    capture = open_capture(native_read, raw)
    first = NativeSnapshot.number(capture, {"left": "0", "right": "1"})
    second = NativeSnapshot.number(capture, {"left": "1", "right": "2"})
    old = DisplayDocument.tree(prepare_snapshot(first, public_api=public_api))
    later = DisplayDocument.tree(prepare_snapshot(second, public_api=public_api))

    assert "0 button First" in old.text
    assert "1 button Second" in old.text
    assert "1 button First" in later.text
    assert "2 button Second" in later.text
    assert '"node":"right"' in CapturedElementDetails.from_document(old)(1).text
    assert '"node":"left"' in CapturedElementDetails.from_document(later)(1).text
