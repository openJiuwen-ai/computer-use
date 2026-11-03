# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Empty-value packing preserves facts and refs without parsing rendered prose."""

from copy import deepcopy

import pytest
from computer_use.interaction.read.presentation.compression.projection import projectTree
from computer_use.interaction.read.presentation.rendering.formatting import render_projection_lines, render_tree
from computer_use.interaction.read.presentation.rendering.layout.empty_values import (
    compact_empty_values,
    empty_value_cells,
)
from computer_use.interaction.read.presentation.rendering.layout.pipeline import LayoutPipeline

MISSING = object()


def node(ref, role, *children, **fields):
    return dict(ref=str(ref), role=role, children=list(children), **fields)


def table(selection=MISSING, count=5, **fields):
    selected = {} if selection is MISSING else {"selectedText": selection}
    return node(
        0,
        "table",
        *(
            node(i, "textField", name=f"A{i}", value="", selectable=True, **selected, **fields)
            for i in range(1, count + 1)
        ),
    )


@pytest.mark.parametrize("context", [{}, {"compact": True}, {"compact": True, "subtreeRef": "0"}])
def test_existing_empty_selection_output_refs_and_accounting_are_preserved(context):
    source = table("")
    original = deepcopy(source)
    result = render_tree(source, {**context, "recordFields": True})
    assert result["text"] == (
        '0 table\n\tEmpty values [text field (selectable); Value: "", Selected text: ""]: 1 A1, 2 A2, 3 A3, 4 A4, 5 A5'
    )
    assert result["refs"] == [str(i) for i in range(6)]
    assert [(r["ref"], r["value"], r["target"]) for r in result["fieldCoverage"] if r["field"] == "selectedText"] == [
        (str(i), "", str(i)) for i in range(1, 6)
    ]
    assert source == original
    section = result.document.parts[0]
    assert [item.ref for item in section.nodes] == result["refs"]
    assert all('Selected text: ""' in item.text for item in section.nodes[1:])


@pytest.mark.parametrize("selection", [MISSING, None])
def test_absent_or_unknown_selection_never_acquires_a_known_empty_claim(selection):
    result = render_tree(table(selection), {"recordFields": True})
    assert "Empty values" in result["text"]
    assert "Selected text:" not in result["text"]
    assert not any(row["field"] == "selectedText" for row in result["fieldCoverage"])


def test_empty_and_missing_selection_split_headers():
    source = table("", count=6)
    for cell in source["children"][3:]:
        del cell["selectedText"]
    assert render_tree(source)["text"].splitlines() == [
        "0 table",
        '\tEmpty values [text field (selectable); Value: "", Selected text: ""]: 1 A1, 2 A2, 3 A3',
        '\tEmpty values [text field (selectable); Value: ""]: 4 A4, 5 A5, 6 A6',
    ]


@pytest.mark.parametrize(
    "extra,wording",
    [
        ({"placeholder": "Type here"}, "Placeholder: Type here"),
        ({"description": "Shared cell metadata"}, "Description: Shared cell metadata"),
        ({"documentUrl": "https://example.invalid/sheet"}, "Document: https://example.invalid/sheet"),
    ],
)
def test_additional_generated_field_does_not_require_a_new_line_regex(extra, wording):
    source = table("", **extra)
    result = render_tree(source)
    assert "Empty values" in result["text"]
    assert result["text"].count(wording) == 1
    assert all(wording in item.text for item in result.document.parts[0].nodes[1:])
    assert result["refs"] == [str(i) for i in range(6)]


@pytest.mark.parametrize(
    "extra",
    [
        {"description": "Only this cell"},
        {"placeholder": "Only this cell"},
        {"identifier": "distinct-native-field"},
        {"rowRange": {"location": 9, "length": 1}},
        {"actionsUnavailable": True},
    ],
)
def test_different_metadata_stays_on_its_own_cell(extra):
    source = table("")
    source["children"][2].update(extra)
    result = render_tree(source)
    assert "Empty values" not in result["text"]
    assert result["text"].splitlines()[3].startswith("\t3 text field (selectable) A3,")
    assert result["refs"] == [str(i) for i in range(6)]


@pytest.mark.parametrize(
    "extra",
    [
        {"focused": True},
        {"selected": True},
        {"selectionRange": {"location": 0, "length": 0}},
        {"selectedText": "<unavailable>"},
        {"partialReason": "incomplete"},
        {"value": "unknown"},
        {"labelTargets": ["99"]},
    ],
)
def test_relevant_unknown_focus_selection_and_relationship_boundaries_stay_explicit(extra):
    source = table("")
    source["children"][2].update(extra)
    assert "3" not in empty_value_cells([source])
    assert "Empty values" not in render_tree(source)["text"]


def test_missing_value_is_not_promoted_to_empty_even_if_other_fields_match():
    source = table("")
    del source["children"][2]["value"]
    assert "3" not in empty_value_cells([source])
    assert "Empty values" not in render_tree(source)["text"]


def test_context_focus_remains_visible_even_without_a_true_element_focus_flag():
    result = render_tree(table("", focused=False), {"focusedRef": "3"})
    assert "Empty values" not in result["text"]
    assert '3 text field (selectable, focused) A3, Focus flag: false, Value: ""' in result["text"]


@pytest.mark.parametrize("addresses", [("A1", "B1", "C1"), ("A1", "A2", "A3")])
def test_both_directions_preserve_source_addresses_and_common_capabilities(addresses):
    source = node(
        0,
        "table",
        *(
            node(
                i,
                "textField",
                name=address,
                value="",
                selectedText="",
                capabilities={"click": "unknown", "typeText": "unknown", "replaceText": "unknown"},
            )
            for i, address in enumerate(addresses, 1)
        ),
    )
    result = render_tree(source)
    assert result["text"] == (
        '0 table\n\tEmpty values [text field; Click: unknown, Input: unknown, Value: "", Selected text: ""]: '
        + ", ".join(f"{i} {address}" for i, address in enumerate(addresses, 1))
    )


def test_untrusted_or_changed_lines_cannot_be_regrouped_from_their_appearance():
    source = table()
    context = {}
    projection = projectTree(source, context)
    frame = render_projection_lines(source, context, projection)
    changed = [line.replace('Value: ""', 'Value: "", Selected text: ""') for line in frame.lines]
    assert compact_empty_values(changed, frame.empty_value_lines) == changed
    assert compact_empty_values(frame.lines, {}) == frame.lines
    assert "Empty values" in "\n".join(LayoutPipeline().run(frame).lines)


def test_names_that_only_look_like_rendered_addresses_are_not_source_addresses():
    for name in ("A0", 'A1, Value: ""', "A999999999999999999", "AAAAAAAAAAAAAAAA1"):
        source = table()
        source["children"][2]["name"] = name
        assert "3" not in empty_value_cells([source])
        assert "Empty values" not in render_tree(source)["text"]


def test_existing_shared_address_caption_is_retained_in_the_group_header():
    source = table()
    for cell in source["children"]:
        cell["name"] = "Cell " + cell["name"]
    assert render_tree(source)["text"] == (
        '0 table\n\tEmpty values [text field (selectable) Cell; Value: ""]: 1 A1, 2 A2, 3 A3, 4 A4, 5 A5'
    )
