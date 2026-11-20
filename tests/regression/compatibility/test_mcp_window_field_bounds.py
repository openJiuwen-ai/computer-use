# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The window tree distinguishes a reported off-frame field from a hidden one."""

import json

import pytest
from computer_use.identity import Record
from computer_use.interaction.read.accessibility.preparation import prepare_snapshot
from computer_use.interaction.read.accessibility.snapshot import NativeSnapshot
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.session.observation import lazy_observation
from computer_use_mcp.output import ObservationPresenter

CLUE = "bounds outside observed window"


def native_value(value):
    return {"nativeType": "CFString" if isinstance(value, str) else "nativeScalar", "data": value}


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


def captured_field(x, y, *, owner=None, in_child=False, field_width=240, enabled=True):
    # The real Shotcut AXWindow/AXTextField geometry and direct AXParent/AXChildren
    # evidence from D12-060 req66 and req1388, without unrelated provider data.
    raw = {
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
                "properties": [property_value("AXRole", native_value("AXWindow"))],
            }
        ],
        "read": {
            "complete": True,
            "intervalNs": [1, 2],
            "details": [
                ["surface", native_value("surface")],
                ["observation_id", native_value("capture")],
            ],
        },
    }
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
        property_value("AXEnabled", native_value(enabled)),
        property_value("AXFocused", native_value(True)),
        property_value("settable.AXValue", native_value(True)),
        property_value("settable.AXFocused", native_value(True)),
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
    return raw


def delivery(engine, observation, source, aliases):
    frame = engine.open_read_frame("json", json.dumps(observation).encode(), 8192)
    capture = frame.capture("window", ("nativeTextInput", "nativeActuators"))
    tokens = capture.source_tokens
    snapshot = NativeSnapshot.number(capture, dict(zip(tokens, aliases.reference_batch(tokens), strict=True)))
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


def test_off_frame_field_retains_reference_and_value_when_returning_inside(native_read):
    source, aliases = Record(), DisplayReferences("session")
    outside = delivery(native_read, captured_field(-974, -696), source, aliases)
    presenter = ObservationPresenter()
    try:
        ref = next(ref for ref, token in outside["refs"].items() if token == "field")
        text = presenter.text(outside)
        assert any(
            line.strip().startswith(f"{ref} text field")
            and all(fact in line for fact in ("text-input", "reports focus", "Presets Search", "Compressor"))
            for line in text.splitlines()
        )
        assert "hidden" not in text
        presenter.delivered(outside)
        moved_outside = delivery(native_read, captured_field(-980, -696), source, aliases)
        assert "Tree: unchanged" in presenter.text(moved_outside)
        presenter.delivered(moved_outside)

        inside = delivery(native_read, captured_field(85, 185), source, aliases)
        diff = presenter.text(inside)
        assert inside["refs"][ref] == "field"
        assert "Tree: diff" in diff and f"~\t{ref} text field" in diff
        assert "reports focus" in diff and "text-input" in diff and CLUE not in diff
        assert "Removed element" not in diff and "Clear search" not in diff
        presenter.delivered(inside)

        diff = presenter.text(outside)
        assert "Tree: diff" in diff
        row = next(line for line in diff.splitlines() if line.startswith(f"~\t{ref} text field"))
        assert CLUE in row, row
    finally:
        presenter.close()


def test_outside_bounds_clue_is_shown_on_the_exact_field(native_read):
    result = delivery(native_read, captured_field(-974, -696), Record(), DisplayReferences("session"))
    row = next(line for line in result["document"].text.splitlines() if "Presets Search" in line)
    assert CLUE in row, row


@pytest.mark.parametrize(
    ("x", "y", "changes"),
    [
        (-974, -696, {"owner": "child"}),
        (1405, 120, {"owner": "child", "in_child": True}),
        (-155, 185, {}),  # Touches the observed window's left boundary.
        (-150, 185, {}),  # Partially intersects the observed window.
        (-974, -696, {"field_width": 0}),
        (-974, -696, {"enabled": False}),
    ],
)
def test_no_outside_bounds_clue_without_exact_disjoint_same_window_geometry(native_read, x, y, changes):
    aliases = DisplayReferences("session")
    result = delivery(native_read, captured_field(x, y, **changes), Record(), aliases)
    assert CLUE not in result["document"].text
    assert "Presets Search" in result["document"].text
