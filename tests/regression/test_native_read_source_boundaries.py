# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native read regressions for retained references, disclosure, and geometry-backed facts."""

import json
from copy import deepcopy

import pytest
from computer_use.interaction.read.accessibility.preparation import (
    PrepareSnapshotPipeline,
    SnapshotRenderRequest,
    prepare_snapshot,
)
from computer_use.interaction.read.accessibility.snapshot import NativeSnapshot
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.element_details import CapturedElementDetails
from computer_use.interaction.read.presentation.rendering.document import prepare_document
from computer_use.pipeline.core import Control

from regression.test_migrated_read_projection import empty_grid, macos_node, number_capture, render_grid
from regression.test_migrated_read_provider_facts import PROVIDERS, observation, property_row, provider_value, value


@pytest.mark.parametrize("length", [1, 120], ids=["short", "long"])
def test_native_text_excerpt_retains_the_exact_full_source_in_details(native_read, length):
    text = "Native start " + "Exact detail 中文😀 " * length + "end"
    raw = observation("macos")
    root = macos_node("root", "AXStaticText", text)
    root["properties"].append(property_row("toolkit", "actions", provider_value([]), read={"complete": True}))
    raw["nodes"] = [root]
    original = deepcopy(raw)

    snapshot = number_capture(native_read, raw)
    prepared = prepare_snapshot(snapshot)
    body = DisplayDocument.tree(prepared).text
    if length == 1:
        assert text in body
        assert "[excerpt:" not in body
    else:
        assert text not in body
        assert "Native start" in body and "end [excerpt:" in body
        assert f"480/{len(text)} chars; captured details: element 0" in body
    assert text in CapturedElementDetails.from_document(DisplayDocument.tree(prepared))(0).text
    assert snapshot.refs == {"0": "root"}
    assert snapshot.capture.node_view("root")["name"] == text
    assert raw == original


@pytest.mark.parametrize(
    ("compact", "depth", "warm"),
    [(True, 64, False), (False, 118, False), (False, 118, True), (True, 85, True)],
    ids=["compact", "full", "warm-full", "deeper-compact"],
)
def test_deep_native_tree_retains_original_body_and_detail_evidence(native_read, compact, depth, warm):
    raw = observation("macos")
    raw["nodes"] = [
        macos_node("root", "AXWindow", "Deep form", children=["layer-1"]),
        *(macos_node(f"layer-{index}", "AXGroup", "", children=[f"layer-{index + 1}"]) for index in range(1, depth)),
        macos_node(f"layer-{depth}", "AXStaticText", "Deepest captured text", children=[]),
    ]
    if warm:
        warm_snapshot = number_capture(native_read, observation("macos"))
        assert DisplayDocument.tree(prepare_snapshot(warm_snapshot, compact=compact)).text
    wire = json.dumps(raw).encode("utf-8")
    capture = native_read.open_read_frame("json", wire, len(wire)).capture("window", [])
    snapshot = NativeSnapshot.number(capture, {token: str(index) for index, token in enumerate(capture.source_tokens)})
    prepared = prepare_snapshot(snapshot, compact=compact)
    body = DisplayDocument.tree(prepared).text
    details = DisplayDocument.tree(prepared, details=True).text
    deepest = f"layer-{depth}"
    assert "Deepest captured text" in body and "Deepest captured text" in details
    assert prepared.snapshot.capture is snapshot.capture
    assert prepared.snapshot.refs[str(depth)] == deepest
    assert prepared.snapshot.capture.node_view(deepest)["name"] == "Deepest captured text"


def test_terminal_budget_error_keeps_original_exception_and_retries_from_cached_capture(native_read):
    snapshot = number_capture(native_read, observation("macos"))
    prepared = prepare_snapshot(snapshot)
    document = DisplayDocument.tree(prepared)
    failure = StopIteration("terminal checkpoint stopped")

    def remaining():
        raise failure

    with pytest.raises(StopIteration) as caught:
        prepare_document(document, control=Control(remaining=remaining))

    assert caught.value is failure
    assert prepared.snapshot.capture is snapshot.capture
    assert "原窗口" in document.text


@pytest.mark.parametrize("mode", ["full", "compact", "explicit-subtree"])
def test_empty_value_display_preserves_refs_and_captured_empty_selection(native_read, mode):
    raw = empty_grid()
    original = deepcopy(raw)
    snapshot = number_capture(native_read, raw)
    prepared = PrepareSnapshotPipeline().run(
        SnapshotRenderRequest(
            snapshot, compact=mode != "full", public_api=True, share_descriptions=mode != "explicit-subtree"
        )
    )
    details = CapturedElementDetails.from_document(DisplayDocument.tree(prepared))
    body = DisplayDocument.tree(prepared).text

    assert prepared.refs == tuple(str(index) for index in range(6))
    for number in range(1, 6):
        assert f"A{number}" in body
        assert '"text.selected_text": ""' in details(number).text
        assert snapshot.capture.node_view(snapshot.refs[str(number)])["text"]["selected_text"] == ""
    assert raw == original


def test_label_relationship_leaves_its_empty_field_addressable(native_read):
    raw = empty_grid()
    relationship = macos_node("unused", "AXTextField", "", children=["field3"])["properties"][-1]
    relationship["key"][1] = "AXServesAsTitleForUIElements"
    raw["nodes"][3]["properties"].append(relationship)

    snapshot, body = render_grid(native_read, raw)
    assert "Empty values" not in body
    assert "3 text field A3" in body
    assert "Labels for: 4" in body
    assert snapshot.capture.node_view(snapshot.refs["3"])["relations"]["labels"]["targets"] == ["field3"]


def provider_relation(source, native_type, name, targets, **details):
    return property_row(
        source,
        name,
        value({"items": [value({"ref": target}, native_type) for target in targets]}, "list"),
        read={"complete": True, "details": [[key, provider_value(item)] for key, item in details.items()]},
    )


@pytest.mark.parametrize(
    ("platform", "selector", "second_selector", "kind", "offset"),
    [
        ("windows", "Table.RowHeaders", "TableItem.RowHeaderItems", "row_headers", 4),
        ("linux", "children", "node-parent-of", "children", 4),
        ("windows", "Table.RowHeaders", "TableItem.RowHeaderItems", "row_headers", 2**35),
    ],
    ids=["windows", "linux", "windows-wide-slot"],
)
def test_provider_aliases_keep_native_slot_progress_and_typed_fields(
    native_read, platform, selector, second_selector, kind, offset
):
    raw = observation(platform)
    source, native_type, role, _, _ = PROVIDERS[platform]
    root = raw["nodes"][0]
    if platform == "windows":
        additions = {
            "HeadingLevel": 80059,
            "PositionInSet": 1,
            "GridItem.RowSpan": 0,
            "IsKeyboardFocusable": False,
            "SupportedPatterns": ["ValuePattern", "SelectionItemPattern", "ScrollPattern", "NewPattern"],
            "Value.IsReadOnly": True,
        }
        action = "Invoke.Invoke"
    else:
        root["properties"][0] = property_row(source, role, value("password text"))
        additions = {
            "attributes": {"level": "3", "posinset": "2", "placeholder": "Search"},
            "Value.currentValue": 2**67,
            "focusable": True,
            "interfaces": ["EditableText", "Value", "UnknownInterface"],
        }
        action = "click"
    root["properties"].extend(property_row(source, key, provider_value(item)) for key, item in additions.items())
    root["properties"].append(
        property_row("toolkit", "actions", provider_value([{"name": action}]), read={"complete": True})
    )
    raw["nodes"].extend(
        {"id": token, "source": source, "nativeType": native_type, "kind": "element", "properties": None}
        for token in ("first", "second")
    )
    raw["read"]["details"].append(["requested_relations", provider_value([selector, second_selector])])
    root["properties"].extend(
        (
            provider_relation(
                source,
                native_type,
                selector,
                ["second", "first"],
                relation=True,
                offset=offset,
                inspectedCount=3,
                unavailablePositions=[offset + 1],
                total=offset + 5,
                certainty="confirmed",
                state="partial",
                cursor="native-next",
                detail="native gap",
                membershipObservedAt={"id": "membership", "startedAt": 1, "completedAt": 2},
            ),
            provider_relation(
                source,
                native_type,
                second_selector,
                ["first"],
                relation=True,
                certainty="conflicting",
                state="complete",
                detail="another relation view",
            ),
        )
    )

    snapshot = number_capture(native_read, raw)
    capture = snapshot.capture
    assert snapshot.refs == {"0": "root", "1": "first", "2": "second"}
    assert not capture.contains_observed("first") and not capture.contains_observed("second")
    node = capture.node_view("root")
    assert list(node["relations"]) == [kind, f"{kind}:2"]
    assert node["relations"][kind] == {
        "kind": kind,
        "targets": ["second", "first"],
        "positions": [offset, offset + 2],
        "offset": offset,
        "total": offset + 5,
        "enumeration": "sampled",
        "evidence": "reported",
        "inspected_count": 3,
        "unavailable_positions": [offset + 1],
    }
    assert node["relations"][f"{kind}:2"]["targets"] == ["first"]
    evidence = capture.lookup_evidence([selector, second_selector])["relations"]
    assert [(edge["property"], edge["state"]) for edge in evidence] == [
        (selector, "partial"),
        (second_selector, "complete"),
    ]
    assert evidence[0]["cursor"] == "native-next"
    assert evidence[1]["certainty"] == "conflicting"
    assert [item["key"] for item in node["supplements"]][-2:] == ["relation_membership", "relation_detail"]

    details = DisplayDocument.tree(prepare_snapshot(snapshot), details=True).text
    assert f'"path":["nodes","root","relations","{kind}"],"state":"partial","detail":"native gap"' in details
    assert f'"path":["nodes","root","relations","{kind}:2"],"state":"conflict"' in details
    if platform == "windows":
        assert node["structure"] == {"heading_level": 9, "index": 0}
        assert node["states"]["selectable"] is True
        assert [(item["kind"], item.get("available")) for item in node["actions"]] == [
            ("invoke", None),
            ("set_value", False),
            ("scroll", None),
            ("focus", False),
        ]
    else:
        assert (node["role"], node["role_detail"], node["placeholder"]) == ("text_field", "password", "Search")
        assert node["states"]["protected"] is True
        assert node["structure"] == {"level": 3, "index": 1}
        assert node["numeric_value"]["value"] == 2**67
        assert [(item["kind"], item.get("target")) for item in node["actions"]] == [
            ("invoke", None),
            ("insert_text", ["text", "content"]),
            ("set_value", ["text", "content"]),
            ("set_value", ["numeric_value", "value"]),
            ("focus", None),
        ]
