# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The window tree distinguishes a reported off-frame field from a hidden one."""

from dataclasses import replace

import pytest
from computer_use.identity import Record
from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.preparation import prepare_snapshot
from computer_use.protocol.observation import CoordinateSpace
from computer_use.protocol.observation.adapters import decode_driver_reply
from computer_use.session.observation import lazy_observation
from computer_use_mcp.output import ObservationPresenter
from unit_tests.test_observation_reuse import wire
from unit_tests.test_sparse_relation_pages import native_value

CLUE = "bounds outside observed window"


def property_value(name, value):
    return {"key": ["macos.ax", name], "value": value}


def frame(x, y, width, height):
    return [
        property_value(
            name,
            {
                "nativeType": native_type,
                "data": {"fields": [[key, {"nativeType": "CGFloat", "data": value}] for key, value in fields]},
            },
        )
        for name, native_type, fields in (
            ("AXPosition", "AXValue.CGPoint", (("x", x), ("y", y))),
            ("AXSize", "AXValue.CGSize", (("width", width), ("height", height))),
        )
    ]


def relation(name, *targets, total=None):
    value = [{"nativeType": "AXUIElement", "data": {"ref": token}} for token in targets]
    count = len(targets) if total is None else total
    complete = count == len(targets)
    result = property_value(
        name, {"nativeType": "CFArray", "data": {"items": value}} if name == "AXChildren" else value[0]
    )
    details = {
        "relation": True,
        "state": "complete" if complete else "partial",
        "certainty": "confirmed",
        "total": count,
    }
    if not complete:
        details.update(offset=0, detail="Collection limit")
    result["read"] = {"complete": complete, "details": [[key, native_value(item)] for key, item in details.items()]}
    return result


def captured_field(x, y, *, owner=None, in_child=False, cross_space=False, field_width=240):
    # The real Shotcut AXWindow/AXTextField geometry and direct AXParent/AXChildren
    # evidence from D12-060 req66 and req1388, without unrelated provider data.
    raw = wire()
    raw["nodes"][0]["properties"].extend(
        [
            *frame(85, 68, 1270, 716),
            relation("AXChildren", "child" if in_child else "field", total=206),
        ]
    )
    field_properties = [
        property_value("AXRole", native_value("AXTextField")),
        property_value("AXTitle", native_value("Presets Search")),
        property_value("AXValue", native_value("Compressor")),
        property_value("AXEnabled", native_value(True)),
        property_value("AXFocused", native_value(True)),
        *frame(x, y, field_width, 24),
        relation("AXParent", "child" if in_child else "root"),
        relation("AXChildren", "clear"),
    ]
    if owner is not None:
        field_properties.append(relation("AXWindow", owner))
    root_shape = {key: value for key, value in raw["nodes"][0].items() if key not in ("id", "properties")}
    raw["nodes"].append(dict(root_shape, id="field", properties=field_properties))
    raw["nodes"].append(
        dict(
            root_shape,
            id="clear",
            properties=[
                property_value("AXRole", native_value("AXButton")),
                property_value("AXTitle", native_value("Clear search")),
            ],
        )
    )
    if in_child or owner == "child":
        raw["nodes"].append(
            dict(
                root_shape,
                id="child",
                properties=[
                    property_value("AXRole", native_value("AXWindow")),
                    *frame(1400, 100, 300, 300),
                    *([relation("AXChildren", "field")] if in_child else []),
                ],
            )
        )
    observation = decode_driver_reply(raw).observation
    if cross_space:
        field = observation.nodes["field"]
        bounds = replace(field.geometry["bounds"], space="independent")
        observation = replace(
            observation,
            coordinate_spaces={
                **observation.coordinate_spaces,
                "independent": CoordinateSpace("point", "desktop_top_left", "down"),
            },
            nodes={**observation.nodes, "field": replace(field, geometry={"bounds": bounds})},
        )
    return observation


def delivery(observation, source, aliases):
    snapshot = normalize_facts(
        observation,
        aliases,
        scope_kind="window",
        operation_capabilities={"field": {"typeText": "available", "replaceText": "available"}},
    )
    document = DisplayDocument.tree(prepare_snapshot(snapshot)).scoped(("scope",))
    return {
        "source": source,
        "scope": "scope",
        "view": "window",
        "state": lazy_observation(document, None),
        "document": document,
        "refs": dict(snapshot.refs),
        "full": False,
        "publicApi": True,
    }


def test_outside_bounds_clue_retains_the_element_and_clears_in_diff():
    source, aliases = Record(), DisplayReferences("session")
    outside = delivery(captured_field(-974, -696), source, aliases)
    presenter = ObservationPresenter()
    try:
        ref = next(ref for ref, token in outside["refs"].items() if token == "field")
        text = presenter.text(outside)
        assert any(
            line.strip().startswith(f"{ref} text field")
            and all(fact in line for fact in ("text-input", "reports focus", CLUE, "Presets Search", "Compressor"))
            for line in text.splitlines()
        )
        assert "hidden" not in text
        presenter.delivered(outside)
        moved_outside = delivery(captured_field(-980, -696), source, aliases)
        assert "Tree: unchanged" in presenter.text(moved_outside)
        presenter.delivered(moved_outside)

        inside = delivery(captured_field(85, 185), source, aliases)
        diff = presenter.text(inside)
        assert inside["refs"][ref] == "field"
        assert "Tree: diff" in diff and f"~\t{ref} text field" in diff
        assert "reports focus" in diff and "text-input" in diff and CLUE not in diff
        assert "Removed element" not in diff and "Clear search" not in diff
    finally:
        presenter.close()


@pytest.mark.parametrize(
    ("x", "y", "changes"),
    [
        (-974, -696, {"owner": "child"}),
        (1405, 120, {"owner": "child", "in_child": True}),
        (-974, -696, {"cross_space": True}),
        (-155, 185, {}),  # Touches the observed window's left boundary.
        (-150, 185, {}),  # Partially intersects the observed window.
        (-974, -696, {"field_width": 0}),
    ],
)
def test_no_outside_bounds_clue_without_exact_disjoint_same_window_geometry(x, y, changes):
    aliases = DisplayReferences("session")
    result = delivery(captured_field(x, y, **changes), Record(), aliases)
    assert CLUE not in result["document"].text
    assert "Presets Search" in result["document"].text
