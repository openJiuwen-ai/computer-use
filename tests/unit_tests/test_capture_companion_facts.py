# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Sharing-control classification uses native evidence before display projection."""

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from computer_use.interaction.read.facts.capture_companion import SHARING_CONTROL_FIELDS, sharing_control_signature
from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.rendering.document.capture_context import _signature
from computer_use.protocol.observation import Action, ControlValue, NumericValue, ReadIssue, TextInfo
from computer_use.protocol.observation.adapters import decode_driver_reply
from recorded_sharing_control import load_sharing_control_recording


@pytest.fixture(scope="module")
def recorded():
    fixture = load_sharing_control_recording()
    return {
        "fixture": fixture,
        "full": decode_driver_reply(fixture["raw_reply"]).observation,
        "narrow": decode_driver_reply(fixture["narrow_raw_reply"]).observation,
    }


def ids(native):
    root = native.roots[0]
    return root, native.nodes[root].relations["children"].targets[0]


def change_node(native, token, **values):
    return replace(native, nodes={**native.nodes, token: replace(native.nodes[token], **values)})


def render_signature(native, *, refs=None, session=None, target_root=None):
    snapshot = normalize_facts(native, DisplayReferences(native.session), scope_kind="window")
    if refs is not None:
        snapshot = replace(snapshot, refs=refs)
    tree = SimpleNamespace(prepared=SimpleNamespace(snapshot=snapshot))
    window = SimpleNamespace(target={"session": session or native.session, "node": target_root or native.roots[0]})
    return _signature(tree, window)


def test_narrow_projection_reproduces_the_original_wire_fixture(recorded):
    # Ordered JSON retains the original property sequence, key order and scalar types.
    encoded = json.dumps(recorded["fixture"]["narrow_raw_reply"], separators=(",", ":"), allow_nan=False)
    assert hashlib.sha256(encoded.encode()).hexdigest() == (
        "1b20bc0b31d31cb216cf28ec01bf985e6e7d12b62a380962f31e459e8fba196c"
    )


def test_recorded_full_and_narrow_reply_supply_the_same_positive_native_signature(recorded):
    full, narrow = recorded["full"], recorded["narrow"]
    assert len(full.nodes) == len(narrow.nodes) == 3
    assert sharing_control_signature(full, full.roots[0]) == ids(full)
    assert sharing_control_signature(narrow, narrow.roots[0]) == ids(narrow)
    assert set(SHARING_CONTROL_FIELDS) == set(recorded["fixture"]["provenance"]["narrow_fields"])
    assert len(SHARING_CONTROL_FIELDS) == 12
    assert not any(node.geometry for node in narrow.nodes.values())
    provenance = recorded["fixture"]["provenance"]
    assert provenance["request_id"] == 56 and provenance["original_full_rpc_duration_ms"] == pytest.approx(6.783333)
    assert "not a new native query" in provenance["narrow_reply_origin"]


def test_fact_signature_does_not_require_generic_title_geometry_or_display_references(recorded):
    native = recorded["narrow"]
    root, button = ids(native)
    native = change_node(native, root, name="A different capture-control window title")
    assert sharing_control_signature(native, root) == (root, button)
    assert render_signature(native) is None  # The renderer retains its separate geometry/name contract.


@pytest.mark.parametrize("backend", ["windows", "linux"])
def test_other_backends_and_nonroot_tokens_are_not_classified(recorded, backend):
    native = recorded["full"]
    root, button = ids(native)
    assert sharing_control_signature(replace(native, backend=backend), root) is None
    assert sharing_control_signature(native, button) is None
    assert sharing_control_signature(native, "missing") is None
    assert sharing_control_signature(replace(native, roots=(root, button)), root) is None


@pytest.mark.parametrize(
    ("owner", "field", "value"),
    [
        ("root", "role", "window"),
        ("root", "role", None),
        ("root", "modal", True),
        ("root", "modal", None),
        ("root", "focused", True),
        ("root", "focused", None),
        ("button", "role", "group"),
        ("button", "name", "Business action"),
        ("button", "enabled", False),
        ("button", "enabled", None),
        ("button", "focused", True),
        ("button", "focused", None),
    ],
)
def test_required_native_role_and_state_facts_must_be_known_and_exact(recorded, owner, field, value):
    native = recorded["full"]
    root, button = ids(native)
    token = root if owner == "root" else button
    if field in ("enabled", "focused", "modal"):
        changed = change_node(native, token, states=replace(native.nodes[token].states, **{field: value}))
    else:
        changed = change_node(native, token, **{field: value})
    assert sharing_control_signature(changed, root) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("description", "Business description"),
        ("help", "Business help"),
        ("placeholder", "Business placeholder"),
        ("url", "https://example.invalid/document"),
        ("control_value", ControlValue(value="Business value")),
        ("numeric_value", NumericValue(value=3)),
        ("text", TextInfo("captured-text", content="Business document")),
    ],
)
@pytest.mark.parametrize("owner", ["root", "button"])
def test_independent_business_payload_stays_in_normal_scope(recorded, owner, field, value):
    native = recorded["full"]
    root, button = ids(native)
    changed = change_node(native, root if owner == "root" else button, **{field: value})
    assert sharing_control_signature(changed, root) is None


@pytest.mark.parametrize(
    "path",
    [
        ("description",),
        ("help",),
        ("placeholder",),
        ("url",),
        ("control_value", "value"),
        ("text", "selected_text"),
    ],
)
def test_silent_missing_payload_is_unknown_instead_of_proved_absent(recorded, path):
    native = recorded["narrow"]
    root, _ = ids(native)
    full_path = ("nodes", root, *path)
    changed = replace(native, issues=tuple(issue for issue in native.issues if issue.path != full_path))
    assert sharing_control_signature(changed, root) is None


@pytest.mark.parametrize(
    "path",
    [
        ("role",),
        ("states", "modal"),
        ("states", "focused"),
        ("relations", "children"),
        ("description",),
        ("help",),
        ("control_value", "value"),
        ("text", "selected_text"),
    ],
)
def test_required_path_read_gaps_are_not_overridden_by_retained_values(recorded, path):
    native = recorded["full"]
    root, _ = ids(native)
    changed = replace(native, issues=(*native.issues, ReadIssue(("nodes", root, *path), "timeout")))
    assert sharing_control_signature(changed, root) is None


@pytest.mark.parametrize("path", [("role",), ("name",), ("actions",), ("states", "enabled"), ("relations", "children")])
def test_button_required_paths_reject_unknown_or_conflicting_evidence(recorded, path):
    native = recorded["full"]
    root, button = ids(native)
    changed = replace(native, issues=(*native.issues, ReadIssue(("nodes", button, *path), "conflict")))
    assert sharing_control_signature(changed, root) is None


@pytest.mark.parametrize("path", [(), ("nodes",)])
def test_unknown_collection_coverage_cannot_prove_the_small_tree_signature(recorded, path):
    native = recorded["full"]
    changed = replace(native, issues=(*native.issues, ReadIssue(path, "partial")))
    assert sharing_control_signature(changed, native.roots[0]) is None


@pytest.mark.parametrize(
    "change",
    [
        {"evidence": "candidate"},
        {"enumeration": "sampled"},
        {"total": None},
        {"total": 2},
        {"positions": (2,)},
        {"offset": 1},
        {"targets": ()},
    ],
)
def test_parent_requires_one_fully_enumerated_child(recorded, change):
    native = recorded["full"]
    root, _ = ids(native)
    relations = native.nodes[root].relations
    changed = change_node(native, root, relations={**relations, "children": replace(relations["children"], **change)})
    assert sharing_control_signature(changed, root) is None


def test_extra_button_children_or_missing_child_node_are_not_the_signature(recorded):
    native = recorded["full"]
    root, button = ids(native)
    relations = native.nodes[button].relations
    changed = change_node(
        native,
        button,
        relations={
            **relations,
            "children": replace(
                relations["children"],
                targets=(root,),
                total=1,
                positions=(0,),
            ),
        },
    )
    assert sharing_control_signature(changed, root) is None
    assert (
        sharing_control_signature(
            replace(native, nodes={key: node for key, node in native.nodes.items() if key != button}), root
        )
        is None
    )


@pytest.mark.parametrize(
    "actions",
    [None, (), (Action(kind="invoke", key="OtherAction"),), (Action(kind="invoke", key="AXPress", available=False),)],
)
def test_button_requires_a_native_axpress_declaration(recorded, actions):
    native = recorded["full"]
    root, button = ids(native)
    assert sharing_control_signature(change_node(native, button, actions=actions), root) is None


def test_renderer_reuses_native_signature_and_keeps_display_and_geometry_boundaries(recorded):
    native = recorded["full"]
    root, button = ids(native)
    assert render_signature(native) == ("0", "1")
    assert render_signature(native, refs={"0": root}) is None
    assert render_signature(native, session="another-session") is None
    assert render_signature(native, target_root=button) is None
    changed = change_node(native, root, name="A different capture-control window title")
    assert sharing_control_signature(changed, root) == (root, button)
    assert render_signature(changed) is None
    frame = native.nodes[root].geometry["bounds"]
    changed = change_node(native, root, geometry={"bounds": replace(frame, width=500)})
    assert sharing_control_signature(changed, root) == (root, button)
    assert render_signature(changed) is None
    changed = change_node(native, root, geometry={})
    assert sharing_control_signature(changed, root) == (root, button)
    assert render_signature(changed) is None
