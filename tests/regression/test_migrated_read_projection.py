# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Preserve native target and reference identity in rendered observations."""

import re
from types import SimpleNamespace

import pytest
from computer_use.interaction.read.accessibility.preparation import prepare_snapshot
from computer_use.interaction.read.accessibility.snapshot import NativeSnapshot
from computer_use.interaction.read.app import NativeAppReader
from computer_use.interaction.read.delivery.coverage import document_coverage
from computer_use.interaction.read.delivery.diff.baseline import ComparisonDocument
from computer_use.interaction.read.delivery.diff.formatting import plan_document_diff
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.rendering.document import render_document

from regression.test_migrated_read_provider_facts import observation, open_capture, property_row, provider_value, value


def macos_node(token, role, name, *, box=None, children=None, action=None):
    source = "macos.ax"
    properties = [property_row(source, "AXRole", value(role)), property_row(source, "AXTitle", value(name))]
    if children is not None:
        relation = {"relation": True, "state": "complete", "certainty": "confirmed", "total": len(children)}
        properties.append(
            property_row(
                source,
                "AXChildren",
                value({"items": [value({"ref": child}, "AXUIElement") for child in children]}, "CFArray"),
                read={"complete": True, "details": [[key, value(item)] for key, item in relation.items()]},
            )
        )
    if box is not None:
        x, y, width, height = box
        properties.extend(
            (
                property_row(source, "AXPosition", value(provider_value({"x": x, "y": y})["data"], "AXValue.CGPoint")),
                property_row(
                    source,
                    "AXSize",
                    value(provider_value({"width": width, "height": height})["data"], "AXValue.CGSize"),
                ),
            )
        )
    if action is not None:
        properties.append(
            property_row("toolkit", "actions", provider_value([{"name": action}]), read={"complete": True})
        )
    return {"id": token, "source": source, "kind": "element", "nativeType": "AXUIElement", "properties": properties}


def number_capture(engine, raw):
    capture = open_capture(engine, raw)
    return NativeSnapshot.number(capture, {token: str(index) for index, token in enumerate(capture.source_tokens)})


@pytest.mark.parametrize("rows", [1, 2, 3])
@pytest.mark.parametrize("heading_position", [False, True])
def test_heading_and_visual_columns_keep_each_original_target_once(native_read, rows, heading_position):
    raw = observation("macos")
    heading_box = (0, 0, 32, 24) if heading_position else None
    content = [macos_node("heading", "AXHeading", "Skills", box=heading_box)]
    for index in range(rows):
        y = 59 + index * 81
        content.extend(
            (
                macos_node(f"label{index}", "AXStaticText", f"Setting {index}", box=(17, y, 150, 20)),
                macos_node(f"description{index}", "AXStaticText", f"Description {index}", box=(17, y + 24, 193, 20)),
                macos_node(f"toggle{index}", "AXCheckBox", f"Toggle {index}", box=(713, y + 10, 42, 24)),
            )
        )
    raw["nodes"] = [
        macos_node("root", "AXWindow", "Settings", box=(0, 0, 800, 500), children=["group"]),
        macos_node("group", "AXGroup", "", box=(0, 0, 772, 450), children=[node["id"] for node in content]),
        *content,
    ]

    snapshot = number_capture(native_read, raw)
    text = DisplayDocument.tree(prepare_snapshot(snapshot)).text
    assert "Presentation fallback" not in text
    assert text.count("heading Skills") == 1
    assert len(text.encode()) < 4096
    displayed_refs = re.findall(r"^\s*(?:Region: )?(\d+) ", text, re.MULTILINE)
    assert len(displayed_refs) == len(set(displayed_refs))
    assert set(displayed_refs) == set(snapshot.refs)
    for index in range(rows):
        assert f"Setting {index}" in text
        assert f"Description {index}" in text
        toggle = f"toggle{index}"
        reference = snapshot.aliases[toggle]
        assert f"{reference} check box Toggle {index}" in text
        assert snapshot.refs[reference] == toggle
    assert snapshot.capture.node_view("group")["relations"]["children"]["targets"] == [node["id"] for node in content]


def test_compact_heading_keeps_composed_controls_addressable(native_read):
    raw = observation("macos")
    control_box = (0, 40, 100, 30)
    raw["nodes"] = [
        macos_node("root", "AXWindow", "", box=(0, 0, 300, 200), children=["heading", "wrapper"]),
        macos_node("heading", "AXHeading", "Downloads"),
        macos_node(
            "wrapper", "AXGroup", "Location /Downloads", box=control_box, children=["change"], action="AXShowMenu"
        ),
        macos_node("change", "AXButton", "Change", box=control_box, children=[], action="AXPress"),
    ]
    snapshot = number_capture(native_read, raw)
    text = DisplayDocument.tree(prepare_snapshot(snapshot)).text

    heading = next(line for line in text.splitlines() if "heading Downloads" in line)
    compound = next(line for line in text.splitlines() if "button Change" in line)
    assert text.index(heading) < text.index(compound)
    assert compound.startswith("\t\t")
    assert "Location /Downloads" in compound
    assert text.count("button Change") == 1
    assert "Presentation fallback" not in text
    assert snapshot.refs[snapshot.aliases["change"]] == "change"
    assert snapshot.capture.node_view("wrapper")["relations"]["children"]["targets"] == ["change"]


def test_reader_numbering_is_stable_until_the_driver_session_changes(native_read):
    raw = observation("macos")
    raw["nodes"] = [
        macos_node("hidden", "AXButton", "Hidden test source"),
        macos_node("root", "AXWindow", "Settings", children=["visible"]),
        macos_node("visible", "AXButton", "Save"),
    ]
    reader = NativeAppReader(SimpleNamespace())

    (first,) = reader.number_snapshots((number_capture(native_read, raw),))
    (second,) = reader.number_snapshots((number_capture(native_read, raw),))
    assert first.refs == second.refs == {"0": "root", "1": "visible", "2": "hidden"}
    assert first.capture.source_tokens == second.capture.source_tokens == ["hidden", "root", "visible"]

    previous_owner = reader._element_references
    assert previous_owner.reference(("another surface", "window", "visible")) == "3"
    raw["session"] = "new-driver"
    (restarted,) = reader.number_snapshots((number_capture(native_read, raw),))
    assert restarted.refs == first.refs
    assert restarted.session == "new-driver"
    assert reader._element_references is not previous_owner
    assert reader._element_references.reference(("another surface", "window", "visible")) == "3"


def test_native_projection_keeps_cycle_shared_and_unread_edges_across_renumbering(native_read):
    source = "linux.atspi"

    def node(token, role, children=()):
        properties = None if role is None else [property_row(source, "role", value(role))]
        if children:
            evidence = {"relation": True, "state": "complete", "certainty": "confirmed", "total": len(children)}
            properties.append(
                property_row(
                    source,
                    "children",
                    value(
                        {"items": [value({"ref": child}, "AtspiAccessible") for child in children]}, "AtspiAccessible[]"
                    ),
                    read={"complete": True, "details": [[key, value(item)] for key, item in evidence.items()]},
                )
            )
        return {
            "id": token,
            "source": source,
            "kind": "element",
            "nativeType": "AtspiAccessible",
            "properties": properties,
        }

    raw = observation("linux")
    raw["nodes"] = [
        node("unread", None),
        node("root", "frame", ["branch", "shared", "unread"]),
        node("branch", "panel", ["root", "shared"]),
        node("shared", "push button"),
    ]
    capture = open_capture(native_read, raw)
    assert capture.source_tokens == ["root", "branch", "shared", "unread"]
    initial = {"root": "7", "branch": "8", "shared": "9", "unread": "10"}
    before = NativeSnapshot.number(capture, initial)
    after = NativeSnapshot.number(capture, {token: str(21 + index) for index, token in enumerate(initial)})
    assert before.refs == {"7": "root", "8": "branch", "9": "shared", "10": "unread"}
    assert before.capture is after.capture
    assert not after.capture.contains_observed("unread")
    for snapshot, root, branch, shared, unread in ((before, "7", "8", "9", "10"), (after, "21", "22", "23", "24")):
        displayed = DisplayDocument.tree(prepare_snapshot(snapshot)).text
        assert re.findall(r"^\s*(\d+) button ", displayed, re.MULTILINE) == [shared]
        assert f"{branch} contains {root} (cycle)" in displayed
        assert f"{root} contains {shared} (shared)" in displayed
        assert f"{root} contains {unread} (unread)" in displayed
        assert "child facts have not been collected" in displayed
        assert snapshot.numbered.delivery_coverage() == ([(root, None), (branch, root), (shared, branch)], [branch])
    assert capture.node_view("root")["relations"]["children"]["targets"] == ["branch", "shared", "unread"]


@pytest.mark.parametrize("complete", [True, False])
def test_native_structural_diff_reports_removed_only_with_complete_parent_evidence(native_read, complete):
    raw = observation("macos")
    raw["nodes"] = [
        macos_node("root", "AXWindow", "Settings", children=["parent"]),
        macos_node("parent", "AXGroup", "Permissions", children=["child"]),
        macos_node("child", "AXButton", "Change", children=[], action="AXPress"),
    ]
    previous = NativeSnapshot.number(open_capture(native_read, raw), {"root": "0", "parent": "3", "child": "9"})
    root = macos_node("root", "AXWindow", "Settings", children=[])
    if not complete:
        root["properties"][-1]["read"] = {
            "complete": False,
            "details": [
                ["relation", value(True)],
                ["state", value("partial")],
                ["certainty", value("confirmed")],
                ["total", value(1)],
            ],
        }
    raw["nodes"] = [root]
    current = NativeSnapshot.number(open_capture(native_read, raw), {"root": "0"})
    scope = ("session", None, None, "surface", None)
    before = DisplayDocument.tree(prepare_snapshot(previous)).scoped(scope)
    after = DisplayDocument.tree(prepare_snapshot(current)).scoped(scope)

    plan = plan_document_diff(
        before, after, previous.refs, current.refs, coverage=(document_coverage(before), document_coverage(after))
    )
    assert (plan.mode, plan.reason) == ("diff", "compared-native-scopes")
    assert plan.retained == {"0"}
    assert plan.not_observed == (set() if complete else {"3", "9"})
    assert plan.tree_at(0).missing == ("3", "9")
    assert plan.tree_at(0).context == {"0"}
    delivered = render_document(after, plan, public_api=True)
    label = "Removed element IDs" if complete else "Not observed in this read"
    assert f"{label}: 3, 9" in delivered
    assert "Tree: diff" in delivered

    assert plan_document_diff(None, after, {}, current.refs).reason == "no-structured-baseline"
    assert plan_document_diff(ComparisonDocument(()), after, {}, current.refs).reason == "initial-observation"
    assert plan_document_diff(after, after, current.refs, current.refs).mode == "unchanged"
    replaced = plan_document_diff(after, after, current.refs, {"0": "another window"})
    assert (replaced.mode, replaced.reason) == ("full", "new-or-replaced-native-scope")


def empty_grid(addresses=("A1", "A2", "A3", "A4", "A5")):
    raw = observation("macos")
    tokens = [f"field{index}" for index in range(len(addresses))]
    raw["nodes"] = [macos_node("root", "AXTable", "", children=tokens)]
    for token, address in zip(tokens, addresses, strict=True):
        node = macos_node(token, "AXTextField", address, children=[])
        node["properties"].extend(
            (
                property_row("macos.ax", "AXValue", value("")),
                property_row("macos.ax", "AXEnabled", value(True)),
                property_row("macos.ax", "AXSelectedText", value("")),
            )
        )
        raw["nodes"].append(node)
    return raw


def render_grid(engine, raw):
    snapshot = number_capture(engine, raw)
    text = DisplayDocument.tree(prepare_snapshot(snapshot, compact=False)).text
    ordinary_lines = text.split("\n\nAdditional native facts", 1)[0]
    return snapshot, ordinary_lines


@pytest.mark.parametrize("addresses", [("A1", "B1", "C1"), ("A1", "A2", "A3")])
def test_full_empty_grid_preserves_real_addresses_refs_and_selection(native_read, addresses):
    snapshot, text = render_grid(native_read, empty_grid(addresses))

    line = next(line for line in text.splitlines() if line.startswith("\tEmpty values"))
    assert 'Value: ""' in line and 'Selected text: ""' in line
    assert line.endswith(", ".join(f"{index} {address}" for index, address in enumerate(addresses, start=1)))
    for index in range(len(addresses)):
        token = snapshot.refs[str(index + 1)]
        assert token == f"field{index}"
        assert snapshot.capture.node_view(token)["text"]["selected_text"] == ""


@pytest.mark.parametrize("state", ["absent", "error"])
def test_empty_value_group_does_not_invent_an_empty_selection(native_read, state):
    raw = empty_grid()
    for node in raw["nodes"][1:]:
        node["properties"][-1] = property_row("macos.ax", "AXSelectedText", None, state=state)
    snapshot, text = render_grid(native_read, raw)

    assert "Empty values" in text
    assert "Selected text:" not in text
    for token in snapshot.capture.source_tokens[1:]:
        assert "selected_text" not in snapshot.capture.node_view(token).get("text", {})


def test_empty_and_unavailable_selection_keep_separate_group_headers(native_read):
    raw = empty_grid(("A1", "A2", "A3", "A4", "A5", "A6"))
    for node in raw["nodes"][4:]:
        node["properties"].pop()
    _, text = render_grid(native_read, raw)

    selected, unknown = [line for line in text.splitlines() if "Empty values" in line]
    assert 'Selected text: ""' in selected
    assert selected.endswith("1 A1, 2 A2, 3 A3")
    assert "Selected text:" not in unknown
    assert unknown.endswith("4 A4, 5 A5, 6 A6")


@pytest.mark.parametrize(
    ("attribute", "data", "wording"),
    [
        ("AXPlaceholderValue", "Type here", "Placeholder: Type here"),
        ("AXDescription", "Shared cell metadata", "Description: Shared cell metadata"),
        ("AXDocument", "https://example.invalid/sheet", "Document: https://example.invalid/sheet"),
    ],
)
def test_empty_value_group_keeps_shared_field_for_each_source(native_read, attribute, data, wording):
    raw = empty_grid()
    for node in raw["nodes"][1:]:
        node["properties"].append(property_row("macos.ax", attribute, value(data)))
    snapshot, text = render_grid(native_read, raw)

    assert "Empty values" in text
    assert text.count(wording) == 1
    evidence = snapshot.capture.lookup_evidence([attribute])["nodes"]
    assert [row["values"].get(attribute) for row in evidence[1:]] == [data] * 5


@pytest.mark.parametrize(
    ("attribute", "data", "wording"),
    [
        ("AXDescription", "Only this cell", "Description: Only this cell"),
        ("AXPlaceholderValue", "Only this cell", "Placeholder: Only this cell"),
        ("AXIdentifier", "distinct-native-field", "ID: distinct-native-field"),
        ("AXFocused", True, "reports focus"),
        ("AXValue", "unknown", "Value: unknown"),
        ("AXSelected", True, "(selected)"),
    ],
)
def test_distinct_cell_facts_are_not_packed_as_empty(native_read, attribute, data, wording):
    raw = empty_grid()
    cell = raw["nodes"][3]
    cell["properties"] = [prop for prop in cell["properties"] if prop["key"][1] != attribute]
    cell["properties"].append(property_row("macos.ax", attribute, value(data)))
    snapshot, text = render_grid(native_read, raw)

    assert "Empty values" not in text
    detailed = next(line for line in text.splitlines() if line.startswith("\t3 text field"))
    assert wording in detailed
    assert "A3" in detailed and snapshot.refs["3"] == cell["id"]


def test_unread_value_does_not_acquire_empty_value_group(native_read):
    raw = empty_grid()
    cell = raw["nodes"][3]
    cell["properties"] = [prop for prop in cell["properties"] if prop["key"][1] != "AXValue"]
    snapshot, text = render_grid(native_read, raw)

    assert "Empty values" not in text
    assert "3 text field A3, Value: <unavailable>" in text
    assert "control_value" not in snapshot.capture.node_view(cell["id"])


@pytest.mark.parametrize(
    ("attribute", "start", "length", "wording"),
    [("AXSelectedTextRange", 0, 0, "Selection: 0:0"), ("AXRowIndexRange", 9, 1, "Row: 10")],
)
def test_native_range_on_one_cell_vetoes_empty_group(native_read, attribute, start, length, wording):
    raw = empty_grid()
    native_range = value(provider_value({"location": start, "length": length})["data"], "AXValue.CFRange")
    raw["nodes"][3]["properties"].append(property_row("macos.ax", attribute, native_range))

    _, text = render_grid(native_read, raw)
    detailed = next(line for line in text.splitlines() if line.startswith("\t3 text field"))
    assert wording in detailed
    assert "Empty values" not in text


def test_empty_value_group_does_not_fold_cell_with_unknown_actions(native_read):
    raw = empty_grid()
    for index, cell in enumerate(raw["nodes"][1:], start=1):
        if index != 3:
            cell["properties"].append(property_row("toolkit", "actions", provider_value([]), read={"complete": True}))

    _, text = render_grid(native_read, raw)
    assert "Empty values" not in text
    detailed = next(line for line in text.splitlines() if line.startswith("\t3 text field"))
    assert "Secondary Actions: <unavailable>" in detailed


def test_empty_value_group_keeps_partial_cell_subtree_open(native_read):
    raw = empty_grid()
    cell = raw["nodes"][3]
    relation = next(prop for prop in cell["properties"] if prop["key"][1] == "AXChildren")
    relation["read"] = {
        "complete": False,
        "details": [
            [key, value(item)]
            for key, item in {"relation": True, "certainty": "confirmed", "state": "partial", "total": 1}.items()
        ],
    }

    _, text = render_grid(native_read, raw)
    assert "Empty values" not in text
    assert "Subtree coverage: partial; element 3" in text


def test_empty_value_group_does_not_claim_unavailable_selection_for_a_single_cell(native_read):
    raw = empty_grid()
    raw["nodes"][3]["properties"][-1] = property_row("macos.ax", "AXSelectedText", None, state="error")

    _, text = render_grid(native_read, raw)
    assert "Empty values" not in text
    detailed = next(line for line in text.splitlines() if line.startswith("\t3 text field"))
    assert "Selected text:" not in detailed


def test_rendered_address_like_names_are_not_grouped(native_read):
    for name in ("A0", 'A1, Value: ""', "A999999999999999999", "AAAAAAAAAAAAAAAA1"):
        raw = empty_grid(("A1", "A2", name, "A4", "A5"))
        snapshot, text = render_grid(native_read, raw)
        assert "Empty values" not in text
        assert snapshot.capture.node_view(snapshot.refs["3"])["name"] == name


def test_common_cell_caption_stays_in_empty_group_header(native_read):
    raw = empty_grid(tuple(f"Cell A{index}" for index in range(1, 6)))
    _, text = render_grid(native_read, raw)
    line = next(line for line in text.splitlines() if line.startswith("\tEmpty values"))
    assert "[text field Cell;" in line
    assert line.endswith("1 A1, 2 A2, 3 A3, 4 A4, 5 A5")
