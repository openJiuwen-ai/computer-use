# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Test the scope, coverage, and constraints actually included in native output."""

import pytest
from compat_read_fixtures import children, element, scalar, snapshot

from computer_use.interaction.read.accessibility.preparation import prepare_snapshot
from computer_use.interaction.read.acquisition.facts.helpers import plan_fact_profile
from computer_use.interaction.read.acquisition.facts.model import FactReadRequest, FactScope, NativeReadCapabilities
from computer_use.interaction.read.delivery.coverage import document_coverage
from computer_use.interaction.read.delivery.diff.baseline import ComparisonDocument
from computer_use.interaction.read.delivery.diff.formatting import plan_document_diff
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.content import DisplayText
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.headers import ImageHeader
from computer_use.interaction.read.presentation.rendering.document import render_document
from computer_use.native.contracts import Binding


TARGET = Binding("session", "instance", "window", "surface", "root")


def property_row(name, value):
    return {"key": ["macos.ax", name], "value": scalar(value)}


def display(nodes, references=None):
    tree = snapshot(TARGET, nodes=nodes, references=references)
    return tree, DisplayDocument.tree(prepare_snapshot(tree))


def relation_status(state, **fields):
    relation = children([])
    relation["state"] = state
    relation["value"] = None
    relation["read"]["complete"] = False
    relation["read"]["details"] = [
        [key, scalar(value)]
        for key, value in {"relation": True, "state": state, "certainty": "unknown", **fields}.items()
    ]
    return relation


def content_nodes(role, relation):
    root = element("root", "AXWindow", "Open")
    root["properties"].append(children(["region", "cancel"]))
    region = element("region", role, "Results")
    region["properties"].append(relation)
    return [root, region, element("cancel", "AXButton", "Cancel")]


@pytest.mark.usefixtures("native_read")
def test_unknown_collection_does_not_look_like_a_known_empty_scroll_area():
    tree, document = display(content_nodes("AXScrollArea", relation_status("unsupported")))
    text = document.text
    assert "scroll area Results" in text
    assert "child enumeration unsupported; contents unknown" in text
    assert "Inspect subtree" not in text
    assert "Presentation fallback" not in text
    assert "button Cancel" in text
    reference = next(ref for ref, token in tree.refs.items() if token == "region")
    assert reference not in document_coverage(document)["complete"]


@pytest.mark.parametrize("state", [None, "absent"])
@pytest.mark.usefixtures("native_read")
def test_known_empty_scroll_area_has_no_unknown_children_notice(state):
    relation = children([]) if state is None else relation_status(state, certainty="confirmed", total=0)
    _, document = display(content_nodes("AXScrollArea", relation))
    assert "contents unknown" not in document.text
    assert "partial:" not in document.text


@pytest.mark.parametrize("role", ["AXButton", "AXStaticText", "AXImage", "AXTextField"])
@pytest.mark.usefixtures("native_read")
def test_unsupported_leaf_children_do_not_create_region_warnings(role):
    _, document = display(content_nodes(role, relation_status("unsupported")))
    assert "contents unknown" not in document.text
    assert "partial:" not in document.text


@pytest.mark.usefixtures("native_read")
def test_unavailable_native_relation_slots_are_not_counted_as_read():
    relation = children(["first", "last"])
    relation["read"] = {
        "complete": False,
        "details": [
            ["relation", scalar(True)],
            ["certainty", scalar("confirmed")],
            ["state", scalar("partial")],
            ["total", scalar(5)],
            ["offset", scalar(0)],
            ["inspectedCount", scalar(5)],
            ["detail", scalar("Some reported AX child slots could not be read")],
            ["unavailablePositions", {"nativeType": "CFArray", "data": {"items": [scalar(i) for i in (1, 2, 3)]}}],
        ],
    }
    tree, document = display(
        [
            *content_nodes("AXScrollArea", relation),
            element("first", "AXButton", "First"),
            element("last", "AXButton", "Last"),
        ]
    )
    text = document.text
    assert "2/5 read" in text
    assert "unavailable native slots" in text
    assert "button First" in text and "button Last" in text
    coverage = document_coverage(document)
    refs = {token: ref for ref, token in tree.refs.items()}
    assert refs["root"] in coverage["complete"]
    assert refs["region"] not in coverage["complete"]


@pytest.mark.usefixtures("native_read")
def test_slider_range_discloses_confirmed_bounds_and_diff_only():
    root = element("root", "AXWindow", "Settings")
    root["properties"].append(children(["slider", "label"]))
    slider = element("slider", "AXSlider", "Zoom")
    slider["properties"].extend(
        [
            property_row("AXValue", 3),
            property_row("AXMinValue", 1),
            property_row("AXValueIncrement", 0.5),
            {"key": ["macos.ax", "AXMaxValue"], "state": "timeout", "value": None},
        ]
    )
    label = element("label", "AXStaticText", "Caption")
    label["properties"].append(property_row("AXMinValue", -80))
    refs = DisplayReferences(TARGET.session)
    tree, before = display([root, slider, label], refs)
    assert "Minimum: 1" in before.text
    assert "Small step: 0.5" in before.text
    assert "Maximum:" not in before.text
    assert "Minimum: -80" not in before.text

    slider["properties"][-3] = property_row("AXMinValue", 2)
    later, after = display([root, slider, label], refs)
    plan = plan_document_diff(ComparisonDocument.capture(before), after, tree.refs, later.refs)
    diff = render_document(after, plan)
    assert "Minimum: 2" in diff
    assert "Minimum: 1" not in diff
    assert "Caption" not in diff


def test_focus_seed_is_only_requested_for_an_ordinary_macos_window_read():
    capabilities = NativeReadCapabilities("macos", ("nativeFocusedReadV1", "nativeAdvertisedFields"))

    def options(**request):
        scope = FactScope(FactReadRequest(None, max_nodes=16, **request), TARGET)
        return plan_fact_profile(scope, capabilities).options

    assert options(scope_kind="window")["focused_element"] is True
    assert "focused_element" not in options(scope_kind="window", fields=["AXRole"])
    assert "focused_element" not in options(scope_kind="window", children_page={"cursor": None, "limit": 5})
    assert "focused_element" not in options(scope_kind="applicationMenu")
    assert "AXHasPopup" in options(scope_kind="window")["advertised_fields"]
    assert "advertised_fields" not in options(scope_kind="window", fields=["AXRole"])


def test_partial_canvas_reports_only_the_shown_windows_sampled_state():
    header = ImageHeader(
        "partial",
        (2,),
        False,
        (
            {"displayed": True, "ref": 2, "capture": {"windowState": {"onScreen": False, "ownerForeground": None}}},
            {"displayed": False, "ref": 3, "capture": {"windowState": {"onScreen": True, "ownerForeground": True}}},
        ),
        None,
    )
    text = DisplayDocument((DisplayText("image_header", (header,)),)).text
    assert "Image note (Window 2):" in text
    assert "onScreen=false; ownerForeground=unknown" in text
    assert "Window 3" not in text
