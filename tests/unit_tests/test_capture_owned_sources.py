"""Capture source reuse preserves exact evidence and detaches editable views."""

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from unittest.mock import patch

import pytest
from computer_use.interaction.read.facts.index import _ReadIssueValue, data, observation_index
from computer_use.interaction.read.facts.normalize import normalize_facts, renumber_snapshot
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.element_details import CapturedElementDetails
from computer_use.interaction.read.presentation.preparation import CapturedSnapshot, prepare_snapshot
from computer_use.protocol.observation import (
    ControlValue,
    CoordinateSpace,
    ElementValue,
    Geometry,
    NativeNode,
    NativeObservation,
    NumericValue,
    ReadIssue,
    ReadRef,
    Structure,
    Supplement,
)


def native(node=None, *, issues=()):
    return NativeObservation(
        "macos",
        "session",
        "capture",
        (1, 2),
        ("window",),
        {"window": node or NativeNode(role="window", name="Captured window")},
        {"desktop": CoordinateSpace("point", "desktop_top_left", "down")},
        issues=issues,
    )


def assert_immutable(value):
    seen, pending = set(), [value]
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        assert type(item) not in (dict, list, set)
        if isinstance(item, Mapping):
            pending.extend(item.values())
        elif isinstance(item, (tuple, frozenset)):
            pending.extend(item)
        elif is_dataclass(item):
            pending.extend(getattr(item, field.name) for field in fields(item))


def test_normalized_sources_reuse_only_immutable_capture_owned_graph():
    observation = native(
        NativeNode(role="text_field", name="Captured text", control_value=ControlValue("value")),
        issues=(ReadIssue(("nodes", "window", "help"), "unsupported", detail="native detail"),),
    )
    snapshot = normalize_facts(observation, DisplayReferences("session"))
    captured = CapturedSnapshot.capture(snapshot)
    for key in ("native", "facts", "refs", "fields", "text_refs", "annotations", "root_contexts", "value_fields"):
        assert getattr(captured, key) is getattr(snapshot, key)
    assert_immutable(captured)
    snapshot.roots[0]["name"] = "Edited display"
    assert captured.roots[0]["name"] == "Captured text"
    for mapping in (snapshot.facts, snapshot.refs, snapshot.fields, snapshot.text_refs):
        with pytest.raises(TypeError):
            mapping["injected"] = "changed"
    with pytest.raises(AttributeError):
        del snapshot.facts.native


def test_unproven_replaced_mapping_proxy_is_detached_instead_of_trusted():
    snapshot = normalize_facts(native(), DisplayReferences("session"))
    backing = {("0", "fixture"): {"nested": ["original"]}}
    snapshot.fields = MappingProxyType(backing)
    captured = CapturedSnapshot.capture(snapshot)
    backing[("0", "fixture")]["nested"].append("later")
    assert captured.fields[("0", "fixture")]["nested"] == ("original",)
    assert captured.fields is not snapshot.fields
    assert_immutable(captured)


def test_projection_range_source_detaches_from_editable_root():
    snapshot = normalize_facts(
        native(NativeNode(role="row", name="Row", structure=Structure(row_index=3, row_span=2))),
        DisplayReferences("session"),
    )
    provenance = snapshot.fields[("0", "structure.row_index")]["projections"][0]["normalized"]
    assert provenance == {"location": 3, "length": 2}
    snapshot.roots[0]["rowRange"]["length"] = 9
    assert provenance["length"] == 2
    assert (
        CapturedSnapshot.capture(snapshot).fields[("0", "structure.row_index")]["projections"][0]["normalized"]
        is provenance
    )


def test_issue_view_preserves_every_field_without_eager_detail_encoding():
    issue = ReadIssue(
        ("nodes", "window", "control_value", "value"),
        "conflict",
        detail="",
        continuation=ReadRef("captured-text", "text", "captured", "unicode_codepoint", 10, (0, 3)),
        alternatives=(
            {"element": ElementValue("window"), "time": datetime(2026, 11, 4, tzinfo=UTC)},
            float("nan"),
        ),
    )
    view = _ReadIssueValue(issue)
    with patch("computer_use.interaction.read.facts.index.data", side_effect=AssertionError("eager detail encoding")):
        assert view["path"] is issue.path
        assert view["state"] == "conflict"
        assert view["detail"] == ""
        assert tuple(view) == ("path", "state", "detail", "continuation", "alternatives")
        assert len(view) == 5
    assert dict(view) == dict(data(issue))
    assert_immutable(view)
    with pytest.raises(AttributeError):
        view._issue = ReadIssue((), "absent")
    with pytest.raises(AttributeError):
        del view._issue
    for key in ("unexpected",):
        with pytest.raises(KeyError):
            view[key]
    minimal = _ReadIssueValue(ReadIssue((), "absent"))
    assert dict(minimal) == {"path": (), "state": "absent", "alternatives": ()}
    for key in ("detail", "continuation"):
        with pytest.raises(KeyError):
            minimal[key]


def test_derived_issue_identity_remains_shared_across_status_and_global_sources():
    issue = ReadIssue(("nodes", "window"), "partial", alternatives=({"nested": (1, 2)},))
    index = observation_index(native(issues=(issue,)))
    original = index["issues"][0]
    assert all(status["issues"][0] is original for status in index["nodes"][0]["fieldStatus"].values())
    assert_immutable(index)


@pytest.mark.parametrize(
    "node",
    [
        NativeNode(role="text_field", name="Element", control_value=ControlValue(ElementValue("window"))),
        NativeNode(role="text_field", name="Date", control_value=ControlValue(datetime(2026, 1, 1, tzinfo=UTC))),
        NativeNode(role="slider", name="NaN", numeric_value=NumericValue(value=float("nan"))),
        NativeNode(role="group", name="Range", geometry={"bounds": Geometry("desktop", 0, 0, -1, 20, False)}),
        NativeNode(role="group", supplements=(Supplement("provider.unknown", {"nested": (1, {"two": "2"})}),)),
    ],
)
def test_semantic_tags_and_complete_element_details_survive_renumbering(node):
    snapshot = normalize_facts(native(node), DisplayReferences("session"))
    original = CapturedSnapshot.capture(snapshot)
    aliases = DisplayReferences("session")
    aliases.reference("earlier")
    with patch(
        "computer_use.interaction.read.facts.normalize.observation_index", side_effect=AssertionError("source rebuilt")
    ):
        renumbered = renumber_snapshot(snapshot, aliases)
    captured = CapturedSnapshot.capture(renumbered)
    assert captured.facts is original.facts
    assert captured.refs == {"1": "window"}
    assert_immutable(captured)
    prepared = prepare_snapshot(snapshot, compact=True, public_api=True)
    details = CapturedElementDetails(((prepared.snapshot, ()),))(0).text
    assert node.name is None or node.name in details
    if node.control_value and isinstance(node.control_value.value, ElementValue):
        assert snapshot.roots[0]["value"] == "element 0"
    if node.numeric_value:
        assert snapshot.roots[0]["value"] == "NaN"
    if node.geometry:
        assert snapshot.roots[0]["preserveGroup"] is True


def test_prepared_layout_uses_typed_immutable_signature_without_cloning_ancestors():
    from computer_use.interaction.read.presentation import preparation
    from computer_use.interaction.read.presentation.interface.signatures import interface_layout_signatures
    from computer_use.protocol.observation import Relation

    observation = NativeObservation(
        "macos",
        "session",
        "capture",
        (1, 2),
        ("window",),
        {
            "window": NativeNode(
                role="window", name="Window", relations={"children": Relation("children", ("button",))}
            ),
            "button": NativeNode(role="button", name="Button"),
        },
    )
    signatures = {}

    def collect(document, *, checkpoint=None):
        values = interface_layout_signatures(document, checkpoint=checkpoint)
        signatures.update(values)
        return values

    with patch.object(preparation, "interface_layout_signatures", side_effect=collect):
        prepared = prepare_snapshot(normalize_facts(observation, DisplayReferences("session")), compact=True)
    assert signatures
    for node in prepared.nodes:
        if node.ref in signatures:
            assert node.fields["_interfaceLayout"] is signatures[node.ref]
            assert_immutable(node.fields)
