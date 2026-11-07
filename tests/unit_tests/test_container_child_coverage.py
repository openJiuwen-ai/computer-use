# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""An inaccessible content collection is not presented as a known empty region."""

from dataclasses import replace
from unittest import TestCase

from computer_use.interaction.read.delivery.coverage import document_coverage
from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.preparation import prepare_snapshot
from computer_use.protocol.observation import (
    CoordinateSpace,
    Geometry,
    NativeNode,
    NativeObservation,
    ReadIssue,
    Relation,
    encode_observation,
)


def content_observation(*, role="scroll_area", state="unsupported", alternate_children=False):
    """Model the browser/scroll-area nesting returned by the system open dialog."""
    region_relations = {"children": Relation("children", (), evidence="candidate" if state else "reported")}
    if alternate_children:
        region_relations["content_children"] = Relation("children", ("entry",), view="content", total=1)
    nodes = {
        "window": NativeNode(
            role="window",
            name="Open",
            actions=(),
            relations={"children": Relation("children", ("browser", "cancel"), total=2)},
        ),
        "browser": NativeNode(
            role="browser", actions=(), relations={"children": Relation("children", ("outer",), total=1)}
        ),
        "outer": NativeNode(
            role="scroll_area", actions=(), relations={"children": Relation("children", ("results",), total=1)}
        ),
        "results": NativeNode(
            role=role,
            name="Results",
            actions=(),
            geometry={"frame": Geometry("screen", x=80, y=100, width=400, height=300)},
            relations=region_relations,
        ),
        "cancel": NativeNode(role="button", name="Cancel", actions=()),
    }
    if alternate_children:
        nodes["entry"] = NativeNode(role="static_text", name="Visible file", actions=())
    return NativeObservation(
        backend="macos",
        session="session",
        id="capture",
        interval_ns=(1, 2),
        roots=("window",),
        nodes=nodes,
        coordinate_spaces={"screen": CoordinateSpace("point", "primary_screen_top_left", "down")},
        issues=(ReadIssue(("nodes", "results", "relations", "children"), state),) if state else (),
    )


def display(observation):
    snapshot = normalize_facts(observation, DisplayReferences(observation.session))
    prepared = prepare_snapshot(snapshot, compact=True, public_api=True)
    return snapshot, prepared, DisplayDocument.tree(prepared).text


class ContainerChildCoverageTests(TestCase):
    def test_relation_component_issue_prevents_proven_children_coverage(self):
        for property_path, state, edge_state in (
            (("targets", 0), "conflict", "conflict"),
            (("total",), "absent", "partial"),
            (("targets", 0), "unsupported", "partial"),
        ):
            with self.subTest(property_path=property_path, state=state):
                native = replace(content_observation(state=None), issues=(
                    ReadIssue(("nodes",), "partial"),
                    ReadIssue(("nodes", "window", "relations", "children", *property_path), state),
                ))
                snapshot, prepared, _ = display(native)
                edge = next(edge for edge in snapshot.facts["relations"] if edge["source"] == "window")
                self.assertEqual(edge["state"], edge_state)
                self.assertEqual([issue["state"] for issue in edge["issues"]], [state])
                self.assertEqual(edge["targets"], ("browser", "cancel"))
                refs = {token: ref for ref, token in snapshot.refs.items()}
                coverage = document_coverage(DisplayDocument.tree(prepared))
                self.assertNotIn(refs["window"], coverage["complete"])
                self.assertIn(refs["browser"], coverage["complete"])

    def test_unsupported_collection_has_a_local_unknown_contents_notice(self):
        native = content_observation()
        original = encode_observation(native)
        snapshot, prepared, text = display(native)
        result_lines = text.split("scroll area Results", 1)[1].split("button Cancel", 1)[0]
        self.assertIn("Children: total unknown; partial:", result_lines)
        self.assertIn("child enumeration unsupported; contents unknown", result_lines)
        self.assertNotIn("Inspect subtree", result_lines)
        self.assertNotIn("Presentation fallback", text)
        self.assertIn("Cancel", text)
        result_ref = next(ref for ref, token in snapshot.refs.items() if token == "results")
        self.assertIn(result_ref, {node.ref for node in prepared.nodes})
        self.assertEqual(encode_observation(native), original)
        self.assertIs(prepared.snapshot.native, native)
        self.assertEqual(native.nodes["results"].geometry["frame"].width, 400)

    def test_known_empty_children_remain_empty_without_an_unknown_notice(self):
        for state in (None, "absent"):
            with self.subTest(state=state):
                _, _, text = display(content_observation(state=state))
                self.assertNotIn("contents unknown", text)
                self.assertNotIn("partial:", text)

    def test_leaf_unsupported_children_do_not_create_region_warnings(self):
        for role in ("button", "static_text", "image", "text_field"):
            with self.subTest(role=role):
                _, _, text = display(content_observation(role=role))
                self.assertNotIn("contents unknown", text)
                self.assertNotIn("partial:", text)

    def test_available_alternate_children_view_supplies_the_contents(self):
        _, _, text = display(content_observation(alternate_children=True))
        self.assertIn("Visible file", text)
        self.assertNotIn("contents unknown", text)
        self.assertNotIn("partial:", text)

    def test_partial_alternate_view_keeps_its_own_coverage_evidence(self):
        native = content_observation(alternate_children=True)
        native = replace(
            native,
            issues=(*native.issues, ReadIssue(("nodes", "results", "relations", "content_children"), "partial")),
        )
        _, _, text = display(native)
        self.assertIn("Visible file", text)
        self.assertIn("observed children partly collected", text)
        self.assertNotIn("child enumeration unsupported", text)
