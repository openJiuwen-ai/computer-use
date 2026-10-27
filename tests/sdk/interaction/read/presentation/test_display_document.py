"""Display preparation retains immutable semantic data until the rendering boundary."""

import unittest
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

from observation_fixture import normalize_fixture as normalizeFacts
from sdk.interaction.read.facts.test_native_facts import batch, edge, node
from sdk.interaction.read.presentation.test_full_presentation import web

from computer_use.interaction.read.delivery.diff.formatting import plan_document_diff, snapshotDiff
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.preparation import prepareSnapshot
from computer_use.interaction.read.presentation.rendering.document import render_document


class DisplayDocumentTests(unittest.TestCase):
    def test_retained_unified_graph_is_included_in_the_structured_memory_budget(self):
        from observation_fixture import from_records

        from computer_use._json import dumps
        from computer_use.protocol.observation import Supplement

        original = from_records(batch([node("root", AXRole="AXWindow")]))
        payload = "provider-content-" * 1024
        graph = replace(
            original,
            nodes={
                **original.nodes,
                "root": replace(original.nodes["root"], supplements=(Supplement("provider.fixture", payload),)),
            },
        )
        snapshot = normalizeFacts(graph, DisplayReferences("session"))
        document = DisplayDocument.tree(prepareSnapshot(snapshot))
        data = document.to_data()
        self.assertIs(document.parts[0].prepared.snapshot.native, graph)
        self.assertEqual(data["parts"][0]["native"]["kind"], "native_observation")
        self.assertIn(payload, dumps(data))
        self.assertGreater(document.estimated_bytes, len(payload))

    def test_preparation_keeps_original_values_parents_and_bindings_before_rendering(self):
        for platform, role_field, window_role, text_role, name_field, value_field in (
            ("macos", "AXRole", "AXWindow", "AXTextField", "AXTitle", "AXValue"),
            ("windows", "ControlType", "ControlType.Window", "ControlType.Edit", "Name", "Value.Value"),
            ("linux", "role", "frame", "entry", "name", "Text.text"),
        ):
            with self.subTest(platform=platform):
                raw = batch(
                    [
                        node("window", **{role_field: window_role, name_field: "Fixture"}),
                        node("editor", **{role_field: text_role, name_field: "Name", value_field: "a\nb"}),
                    ],
                    [edge("window", "editor")],
                )
                raw["platform"] = platform
                snapshot = normalizeFacts(raw, DisplayReferences("session"))
                with patch(
                    "computer_use.interaction.read.presentation.rendering.document.render_document",
                    side_effect=AssertionError("premature rendering"),
                ):
                    prepared = prepareSnapshot(snapshot)
                    document = DisplayDocument.tree(prepared)
                    self.assertEqual(prepared.snapshot.refs, snapshot.refs)
                    self.assertEqual(document.summary_data()["trees"][0]["refs"], list(prepared.refs))
                    self.assertGreater(document.estimated_bytes, 0)
                    visited = {}
                    for item in prepared.nodes:
                        self.assertFalse(hasattr(item, "text"))
                        if item.parent is not None:
                            self.assertEqual(item.depth, visited[item.parent].depth + 1)
                        visited[item.ref] = item
                    self.assertTrue(any(item.fields.get("value") == "a\nb" for item in prepared.nodes))
                self.assertEqual(document.text, render_document(document))

    def test_compact_and_detail_snapshots_keep_related_root_context(self):
        raw = batch(
            [
                node("window", AXRole="AXWindow", AXTitle="Fixture"),
                node("body", AXRole="AXStaticText", AXValue="Body"),
                node("dialog", AXRole="AXDialog", AXTitle="Related"),
                node("hint", AXRole="AXStaticText", AXValue="Hint"),
            ],
            [edge("window", "body")],
            roots=["window", "dialog", "hint"],
        )
        snapshot = normalizeFacts(raw, DisplayReferences("session"))
        snapshot.root_contexts = {
            snapshot.roots[1]["ref"]: (("0", "owned"),),
            snapshot.roots[2]["ref"]: (("0", "owned"),),
        }
        original = deepcopy(snapshot.facts)
        for compact in (False, True):
            prepared = prepareSnapshot(snapshot, compact=compact)
            document = DisplayDocument.tree(prepared)
            self.assertEqual(snapshot.facts, original)
            self.assertEqual(prepared.snapshot.refs, snapshot.refs)
            self.assertEqual(prepared.snapshot.root_contexts[snapshot.roots[1]["ref"]], (("0", "owned"),))
            self.assertIn("Related elements (owned of 0):", document.text)
            self.assertEqual(document.text, render_document(document))

    def test_folded_caption_references_are_retained_without_extra_display_nodes(self):
        raw = batch(
            [
                web("button"),
                web("caption", "AXStaticText", AXValue="Run", **{"settable.AXFocused": False}),
                node("body", AXRole="AXStaticText", AXValue="Unchanged body " * 200),
            ],
            [edge("button", "caption")],
            roots=["button", "body"],
        )
        raw["nodes"][0]["actions"]["items"] = [dict(name="AXPress", description="Press")]
        snapshot = normalizeFacts(raw, DisplayReferences("session"), driver_features=("nativeActionIntents",))
        prepared = prepareSnapshot(snapshot)
        self.assertLess(len(prepared.nodes), len(prepared.snapshot.refs))
        document = DisplayDocument.join(("App: Test.", "Tree: full", DisplayDocument.tree(prepared)))
        with patch(
            "computer_use.interaction.read.presentation.rendering.document.render_document",
            side_effect=AssertionError("diff planning must stay structured"),
        ):
            plan = plan_document_diff(document, document, snapshot.refs, snapshot.refs)
        self.assertEqual(plan.mode, "unchanged")
        record = dict(document=document, refs=snapshot.refs, publicApi=True)
        self.assertIn("Tree: unchanged", snapshotDiff(record, record))

    def test_lossless_fallback_stays_structured_until_the_final_renderer(self):
        raw = batch([node("a", AXRole="AXWindow"), node("b", AXRole="AXButton")], roots=["a", "b"])
        snapshot = normalizeFacts(raw, DisplayReferences("session"))
        with patch(
            "computer_use.interaction.read.presentation.semantics.passes.compose_display_semantics.composeControls",
            side_effect=ValueError("fixture projection failure"),
        ):
            prepared = prepareSnapshot(snapshot)
        self.assertTrue(any(root.failure for root in prepared.roots))
        document = DisplayDocument.tree(prepared)
        plan = plan_document_diff(document, document, snapshot.refs, snapshot.refs)
        self.assertEqual(plan.mode, "full")
        self.assertIn("Presentation fallback", render_document(document, plan))

    def test_prepared_capture_is_unchanged_when_the_source_snapshot_is_modified(self):
        snapshot = normalizeFacts(
            batch(
                [
                    node("window", AXRole="AXWindow", AXTitle="Original"),
                    node("editor", AXRole="AXTextField", AXTitle="Name", AXValue="original value"),
                ],
                [edge("window", "editor")],
            ),
            DisplayReferences("session"),
        )
        prepared = prepareSnapshot(snapshot)
        document = DisplayDocument.tree(prepared)
        original = document.to_data()
        snapshot.facts["nodes"][1]["values"]["value"] = "new observed value"
        snapshot.roots[0]["name"] = "Changed title"
        snapshot.roots[0]["children"][0]["value"] = "changed semantic value"
        snapshot.refs["1"] = "replacement"
        snapshot.annotations.append("new annotation")
        self.assertEqual(document.to_data(), original)
        self.assertIn("original value", document.text)
        self.assertNotIn("changed semantic value", document.text)
        self.assertEqual(prepared.snapshot.refs["1"], "editor")
        with self.assertRaises(TypeError):
            prepared.nodes[0].fields["name"] = "edited"


if __name__ == "__main__":
    unittest.main()
