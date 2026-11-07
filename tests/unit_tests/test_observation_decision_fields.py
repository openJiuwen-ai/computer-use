# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Ordinary observation exposes proven operation constraints on their original owners."""

from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.preparation import prepare_snapshot
from computer_use.protocol.observation import NativeNode, NativeObservation, NumericValue, ReadIssue, Relation


def test_slider_range_is_displayed_but_unproved_and_incidental_bounds_are_not():
    native = NativeObservation(
        "macos",
        "session",
        "surface",
        (1, 2),
        ("window",),
        {
            "window": NativeNode(
                role="window", actions=(), relations={"children": Relation("children", ("slider", "label"), total=2)}
            ),
            "slider": NativeNode(
                role="slider",
                name="Zoom",
                actions=(),
                numeric_value=NumericValue(value=3, minimum=1, maximum=7, small_step=0.5),
            ),
            "label": NativeNode(
                role="static_text", name="Caption", actions=(), numeric_value=NumericValue(minimum=-80)
            ),
        },
        issues=(ReadIssue(("nodes", "slider", "numeric_value", "maximum"), "partial"),),
    )
    references = DisplayReferences(native.session)
    before = normalize_facts(native, references)
    document = DisplayDocument.tree(prepare_snapshot(before))
    text = document.text
    assert "Minimum: 1" in text
    assert "Small step: 0.5" in text
    assert "Maximum: 7" not in text
    assert "Minimum: -80" not in text

    from dataclasses import replace

    from computer_use.interaction.read.delivery.diff.formatting import snapshot_diff

    changed = replace(native.nodes["slider"], numeric_value=NumericValue(value=3, minimum=2, maximum=7, small_step=0.5))
    after = normalize_facts(replace(native, nodes={**native.nodes, "slider": changed}), references)
    diff = snapshot_diff(
        {"document": document, "refs": before.refs},
        {"document": DisplayDocument.tree(prepare_snapshot(after)), "refs": after.refs},
    )
    assert "Minimum: 2" in diff
    assert "Minimum: 1" not in diff
    assert "Caption" not in diff


def test_native_focus_seed_is_requested_only_for_ordinary_macos_window_observation():
    from computer_use.interaction.read.acquisition.facts.helpers import plan_fact_profile
    from computer_use.interaction.read.acquisition.facts.model import FactReadRequest, FactScope, NativeReadCapabilities
    from computer_use.native.contracts import Binding

    target = Binding("session", "instance", "window", "surface", "root")
    capabilities = NativeReadCapabilities("macos", ("nativeFocusedReadV1", "nativeAdvertisedFields"))

    def options(**request):
        scope = FactScope(FactReadRequest(None, max_nodes=16, **request), target)
        return plan_fact_profile(scope, capabilities).options

    assert options(scope_kind="window")["focused_element"] is True
    assert "focused_element" not in options(scope_kind="window", fields=["AXRole"])
    assert "focused_element" not in options(scope_kind="window", children_page={"cursor": None, "limit": 5})
    assert "focused_element" not in options(scope_kind="applicationMenu")
    assert "AXHasPopup" in options(scope_kind="window")["advertised_fields"]
    assert "advertised_fields" not in options(scope_kind="window", fields=["AXRole"])


def test_child_coverage_does_not_count_unavailable_slots_as_read():
    from types import SimpleNamespace

    from computer_use.interaction.read.presentation.interface.atoms.model import AtomCoverage
    from computer_use.interaction.read.presentation.interface.text.formatting import InterfaceWriter

    writer = InterfaceWriter.__new__(InterfaceWriter)
    atom = SimpleNamespace(coverage=AtomCoverage(read=2, total=5, start=1, end=5))
    lines = writer.coverage_lines(atom)
    assert "2/5 read" in lines[0]
    assert "unavailable native slots" in lines[0]


def test_partial_canvas_displays_each_shown_windows_sampled_state():
    from computer_use.interaction.read.presentation.content import DisplayText
    from computer_use.interaction.read.presentation.headers import ImageHeader

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
