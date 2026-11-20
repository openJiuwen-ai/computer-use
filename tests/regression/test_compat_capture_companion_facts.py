# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exercise the companion reader with native AX evidence through the Rust engine."""

import json
from types import SimpleNamespace

import pytest
from compat_read_fixtures import children, element, scalar

from computer_use.interaction.read.accessibility.preparation import prepare_snapshot
from computer_use.interaction.read.accessibility.snapshot import NativeSnapshot
from computer_use.interaction.read.facts.capture_companion import SHARING_CONTROL_FIELDS
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.rendering.document.capture_context import _signature


ABSENCE_FIELDS = ("AXDescription", "AXHelp", "AXPlaceholderValue", "AXURL", "AXValue", "AXSelectedText")
ROOT, BUTTON = "sharing-window", "sharing-button"


def property_row(name, value, *, toolkit=False):
    return {"key": ["toolkit" if toolkit else "macos.ax", name], "value": value}


def actions(names):
    return {
        **property_row(
            "actions",
            {
                "nativeType": "CFArray",
                "data": {
                    "items": [{"nativeType": "record", "data": {"fields": [["name", scalar(name)]]}} for name in names]
                },
            },
            toolkit=True,
        ),
        "read": {"complete": True, "details": [["state", scalar("complete")]]},
    }


@pytest.fixture
def raw_capture():
    dialog = element(ROOT, "AXWindow", "Window")
    dialog["properties"].extend(
        [
            property_row("AXSubrole", scalar("AXDialog")),
            property_row("AXModal", scalar(False)),
            property_row("AXFocused", scalar(False)),
            children([BUTTON]),
            actions(["AXRaise"]),
        ]
    )
    button = element(BUTTON, "AXButton", "WindowSharingSessionButton")
    button["properties"].extend(
        [
            property_row("AXEnabled", scalar(True)),
            property_row("AXFocused", scalar(False)),
            children([]),
            actions(["AXPress"]),
        ]
    )
    for node in (dialog, button):
        node["properties"].extend({**property_row(name, None), "state": "unsupported"} for name in ABSENCE_FIELDS)
    return {
        "kind": "observation",
        "session": "session",
        "platform": "macos",
        "roots": [ROOT],
        "nodes": [dialog, button],
        "read": {
            "complete": True,
            "intervalNs": [1, 2],
            "details": [
                ["observation_id", scalar("sharing-capture")],
                ["surface", scalar("sharing-surface")],
            ],
        },
    }


def property_of(raw, owner, name):
    node = next(node for node in raw["nodes"] if node["id"] == owner)
    return next(row for row in node["properties"] if row["key"][1] == name)


def open_capture(engine, raw):
    return engine.open_read_frame("json", json.dumps(raw).encode(), 8192).capture("window", [])


def test_narrow_sharing_control_facts_establish_identity_without_geometry(native_read, raw_capture):
    capture = open_capture(native_read, raw_capture)
    assert capture.sharing_control_signature(ROOT) == (ROOT, BUTTON)
    assert set(SHARING_CONTROL_FIELDS) == set(ABSENCE_FIELDS) | {
        "AXRole",
        "AXSubrole",
        "AXTitle",
        "AXEnabled",
        "AXFocused",
        "AXModal",
    }
    assert not capture.node_view(ROOT, ["geometry"]).get("geometry")
    assert capture.sharing_control_signature(BUTTON) is None
    assert capture.sharing_control_signature("missing") is None

    property_of(raw_capture, ROOT, "AXTitle")["value"] = scalar("Business window")
    renamed = open_capture(native_read, raw_capture)
    assert renamed.sharing_control_signature(ROOT) == (ROOT, BUTTON)
    assert renamed.capture_companion_evidence(ROOT) is None


def test_multiple_roots_do_not_establish_a_sharing_control(native_read, raw_capture):
    raw_capture["roots"].append(BUTTON)
    assert open_capture(native_read, raw_capture).sharing_control_signature(ROOT) is None


def test_partial_capture_keeps_the_sharing_child_unclassified(native_read, raw_capture):
    raw_capture["read"].update(complete=False, issues=[{"reason": "timeout", "detail": "node collection timed out"}])
    assert open_capture(native_read, raw_capture).sharing_control_signature(ROOT) is None


def test_unread_button_cannot_establish_a_sharing_control(native_read, raw_capture):
    raw_capture["nodes"][1]["properties"] = None
    assert open_capture(native_read, raw_capture).sharing_control_signature(ROOT) is None


@pytest.mark.parametrize(
    ("owner", "name", "value"),
    [
        (ROOT, "AXSubrole", "AXStandardWindow"),
        (ROOT, "AXSubrole", None),
        (ROOT, "AXModal", True),
        (ROOT, "AXModal", None),
        (ROOT, "AXFocused", True),
        (ROOT, "AXFocused", None),
        (BUTTON, "AXRole", "AXGroup"),
        (BUTTON, "AXTitle", "Business action"),
        (BUTTON, "AXEnabled", False),
        (BUTTON, "AXEnabled", None),
        (BUTTON, "AXFocused", True),
        (BUTTON, "AXFocused", None),
    ],
)
def test_required_control_role_and_state_are_known_and_exact(native_read, raw_capture, owner, name, value):
    row = property_of(raw_capture, owner, name)
    row["value"] = None if value is None else scalar(value)
    if value is None:
        row["state"] = "unsupported"
    assert open_capture(native_read, raw_capture).sharing_control_signature(ROOT) is None


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AXDescription", "Business description"),
        ("AXHelp", "Business help"),
        ("AXPlaceholderValue", "Business placeholder"),
        ("AXURL", "https://example.invalid/document"),
        ("AXValue", "Business value"),
        ("AXValue", 3),
        ("AXSelectedText", "Business document"),
    ],
)
@pytest.mark.parametrize("owner", [ROOT, BUTTON])
def test_native_business_payload_prevents_companion_classification(native_read, raw_capture, owner, name, value):
    row = property_of(raw_capture, owner, name)
    row.pop("state")
    row["value"] = scalar(value)
    assert open_capture(native_read, raw_capture).sharing_control_signature(ROOT) is None


@pytest.mark.parametrize("name", ABSENCE_FIELDS)
def test_an_unread_property_cannot_prove_absence(native_read, raw_capture, name):
    raw_capture["nodes"][0]["properties"].remove(property_of(raw_capture, ROOT, name))
    assert open_capture(native_read, raw_capture).sharing_control_signature(ROOT) is None


@pytest.mark.parametrize(
    ("owner", "name"),
    [
        (ROOT, "AXRole"),
        (ROOT, "AXModal"),
        (ROOT, "AXFocused"),
        (ROOT, "AXChildren"),
        (ROOT, "AXDescription"),
        (ROOT, "AXHelp"),
        (ROOT, "AXValue"),
        (ROOT, "AXSelectedText"),
        (BUTTON, "AXRole"),
        (BUTTON, "AXTitle"),
        (BUTTON, "actions"),
        (BUTTON, "AXEnabled"),
        (BUTTON, "AXChildren"),
    ],
)
def test_path_read_gaps_prevent_companion_classification(native_read, raw_capture, owner, name):
    row = property_of(raw_capture, owner, name)
    row.update(state="timeout", value=None)
    if name == "AXChildren":
        row["read"] = {
            "complete": False,
            "details": [
                [key, scalar(value)]
                for key, value in {"relation": True, "state": "timeout", "certainty": "unknown"}.items()
            ],
        }
    assert open_capture(native_read, raw_capture).sharing_control_signature(ROOT) is None


@pytest.mark.parametrize(
    "change",
    [
        {"certainty": "inferred"},
        {"state": "partial"},
        {"state": "partial", "total": 2},
        {"state": "partial", "total": 2, "offset": 1},
    ],
)
def test_window_children_must_be_resolved_as_a_complete_relation(native_read, raw_capture, change):
    relation = property_of(raw_capture, ROOT, "AXChildren")
    if change.get("state") == "partial":
        relation["read"]["complete"] = False
    fields = dict(relation["read"]["details"])
    fields.update({name: scalar(value) for name, value in change.items()})
    relation["read"]["details"] = list(fields.items())
    assert open_capture(native_read, raw_capture).sharing_control_signature(ROOT) is None


@pytest.mark.parametrize("owner", [ROOT, BUTTON])
def test_missing_or_additional_children_are_not_a_sharing_control(native_read, raw_capture, owner):
    row = property_of(raw_capture, owner, "AXChildren")
    row.update(children([] if owner == ROOT else [ROOT]))
    assert open_capture(native_read, raw_capture).sharing_control_signature(ROOT) is None


@pytest.mark.parametrize("names", [[], ["OtherAction"]])
def test_button_requires_axpress(native_read, raw_capture, names):
    property_of(raw_capture, BUTTON, "actions").update(actions(names))
    assert open_capture(native_read, raw_capture).sharing_control_signature(ROOT) is None


@pytest.mark.parametrize(
    ("width", "include_bounds", "session", "target", "expected"),
    [
        (100, True, "session", ROOT, ("0", "1")),
        (100, True, "another-session", ROOT, None),
        (100, True, "session", BUTTON, None),
        (500, True, "session", ROOT, None),
        (100, False, "session", ROOT, None),
    ],
)
def test_capture_presentation_checks_geometry_and_binding(
    native_read,
    raw_capture,
    width,
    include_bounds,
    session,
    target,
    expected,
):
    if include_bounds:

        def geometry(kind, fields):
            return {
                "nativeType": "AXValue." + kind,
                "data": {"fields": [[key, scalar(value)] for key, value in fields.items()]},
            }

        for node in raw_capture["nodes"]:
            node["properties"].extend(
                [
                    property_row("AXPosition", geometry("CGPoint", {"x": 10, "y": 20})),
                    property_row("AXSize", geometry("CGSize", {"width": width, "height": 32})),
                ]
            )
    # The narrow policy intentionally ignores geometry. Only presentation may fold the body.
    capture = open_capture(native_read, raw_capture)
    assert capture.sharing_control_signature(ROOT) == (ROOT, BUTTON)
    snapshot = NativeSnapshot.number(capture, {ROOT: "0", BUTTON: "1"})
    window = SimpleNamespace(target={"session": session, "node": target})
    document = DisplayDocument.tree(prepare_snapshot(snapshot))
    assert _signature(document.parts[0], window) == expected
