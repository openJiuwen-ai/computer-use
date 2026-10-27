"""Plan structured changes before the shared terminal renderer produces text."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from observation_fixture import normalize_fixture as normalizeFacts
from sdk.interaction.read.facts.test_native_facts import batch, edge, node

from computer_use._identity import Record
from computer_use._json import dumps, utf8
from computer_use.interaction.read.delivery.diff.formatting import plan_document_diff, snapshotDiff
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.document import DisplayDocument, DisplayTree
from computer_use.interaction.read.presentation.ir import freeze
from computer_use.interaction.read.presentation.preparation import prepareSnapshot
from computer_use.interaction.read.presentation.rendering.document import render_document
from computer_use.session.observation import lazyObservation, peekObservationText
from computer_use_mcp.presentation import ObservationPresenter


def observation(
    value="before",
    *,
    menu="File",
    tail="Reported focus: 2 text field Name",
    context=(),
    omit_field=False,
    partial=False,
):
    references = DisplayReferences("session")
    for native in ("window", "form", "field", "body", "menu", "file"):
        references.reference(native)
    sources = [node("window", AXRole="AXWindow", AXTitle="Document"), node("form", AXRole="AXGroup", AXTitle="Form")]
    if not omit_field:
        sources.append(node("field", AXRole="AXTextArea" if partial else "AXTextField", AXTitle="Name", AXValue=value))
    sources.append(node("body", AXRole="AXStaticText", AXValue="unrelated content " * 300))
    relations = [edge("window", "form", "body"), edge("form", *(() if omit_field else ("field",)))]
    if partial and not omit_field:
        relations.append(edge("field", state="partial", total=3, offset=0, cursor="next-children"))
    window = normalizeFacts(batch(sources, relations), references)
    menus = normalizeFacts(
        batch(
            [node("menu", AXRole="AXMenuBar"), node("file", AXRole="AXMenuBarItem", AXTitle=menu)],
            [edge("menu", "file")],
        ),
        references,
    )
    window_document = DisplayDocument.tree(prepareSnapshot(window)).scoped(("session", "instance", "root", "window"))
    menu_document = DisplayDocument.tree(prepareSnapshot(menus)).scoped(("session", "instance", "root", "menu"))
    document = DisplayDocument.join(
        (
            'App: "Fixture".',
            "Tree: full",
            'Window 0: "Document"',
            window_document,
            tail,
            "Additional native facts/actions/relations: (await app.element(0)).read()",
            'Continue: (await app.element(1)).read(cursor="p1")',
            "Application menu (context of this window):",
            menu_document,
            "Observation note: current capture metadata is retained.",
        )
    )
    return dict(document=replace(document, context=context), refs={**window.refs, **menus.refs}, publicApi=True)


def replace_tree(value, index, function):
    parts = list(value["document"].parts)
    positions = [i for i, part in enumerate(parts) if isinstance(part, DisplayTree)]
    parts[positions[index]] = function(parts[positions[index]])
    return dict(value, document=replace(value["document"], parts=tuple(parts)))


def map_nodes(tree, function):
    return replace(tree, prepared=replace(tree.prepared, nodes=tuple(function(n) for n in tree.nodes)))


def event(value, source, coverage=None):
    return dict(
        value,
        source=source,
        scope="window",
        view="window",
        full=False,
        state=lazyObservation(value["document"]),
        coverage=coverage,
    )


class StructuredDiffTests(unittest.TestCase):
    def test_sectioned_tree_with_menu_and_continuations_can_be_unchanged(self):
        current = observation()
        result = snapshotDiff(current, current)
        for expected in (
            "Tree: unchanged",
            "read(full=True)",
            "Application menu (context of this window):",
            "Additional native facts/actions/relations:",
            'cursor="p1"',
            "Reported focus: 2",
        ):
            self.assertIn(expected, result)
        self.assertNotIn("unrelated content", result)

    def test_change_keeps_ancestors_and_current_summaries_without_unrelated_body(self):
        previous, current = observation(), observation("after", tail="Selected: 2 text field Name\nReported focus: 2")
        plan = plan_document_diff(previous["document"], current["document"], previous["refs"], current["refs"])
        self.assertEqual(plan.mode, "diff")
        self.assertEqual(plan.trees[0].changes, {"2": "~"})
        self.assertTrue({"0", "1"} <= plan.trees[0].context)
        result = render_document(current["document"], plan, public_api=True)
        self.assertIn("Tree: diff", result)
        self.assertRegex(result, r"~\t\t2 .*Name.*Value: after")
        self.assertIn("Selected: 2", result)
        self.assertNotIn("unrelated content", result)

    def test_menu_is_compared_as_its_own_tree(self):
        previous, current = observation(), observation(menu="Changed")
        plan = plan_document_diff(previous["document"], current["document"], previous["refs"], current["refs"])
        self.assertEqual(plan.trees[0].changes, {})
        self.assertEqual(plan.trees[1].changes, {"5": "~"})
        self.assertIn("Changed", snapshotDiff(previous, current))

    def test_section_notes_are_delivered_when_only_metadata_changes(self):
        before = observation()
        document = DisplayDocument.join((before["document"], "New provider limitation: content is partial."))
        after = dict(before, document=replace(document, context=before["document"].context))
        result = snapshotDiff(before, after)
        self.assertIn("New provider limitation:", result)
        self.assertIn("Tree: unchanged", result)

    def test_rebound_reference_requires_full_even_when_semantic_fields_match(self):
        previous = observation()
        current = dict(previous, refs={**previous["refs"], "2": "replacement-native-object"})
        self.assertEqual(snapshotDiff(previous, current), current["document"].text)

    def test_context_and_native_scope_changes_require_full(self):
        previous = observation(context=("session", "root", "document-a", "main"))
        for context in [
            ("session-2", "root", "document-a", "main"),
            ("session", "root", "document-b", "main"),
            ("session", "root", "document-a", "modal"),
        ]:
            current = observation(context=context)
            self.assertEqual(snapshotDiff(previous, current), current["document"].text)
        current = replace_tree(previous, 1, lambda tree: replace(tree, scope=("other-menu",)))
        self.assertEqual(snapshotDiff(previous, current), current["document"].text)

    def test_hidden_native_references_do_not_prevent_diff_or_become_removed(self):
        previous = observation()
        previous = dict(previous, refs={**previous["refs"], "6": "folded-caption"})
        self.assertIn("Tree: unchanged", snapshotDiff(previous, previous))
        current = observation(omit_field=True)
        current["refs"] = previous["refs"]
        result = snapshotDiff(previous, current)
        self.assertIn("Not observed in this read (not proof of removal): 2", result)
        self.assertNotIn("Removed element IDs:", result)

    def test_missing_reference_obeys_the_callers_coverage_classification(self):
        previous, current = observation(), observation(omit_field=True)
        self.assertIn("Removed element IDs: 2", snapshotDiff(previous, current))
        self.assertIn("not proof of removal", snapshotDiff(previous, current, {"2"}))

    def test_delivery_requires_collection_coverage_before_reporting_removal(self):
        previous, current = observation(), observation(omit_field=True)
        source, presenter = Record(), ObservationPresenter()
        presenter.delivered(event(previous, source))
        self.assertIn("not proof of removal", presenter.text(event(current, source)))
        old_coverage = dict(parents={"0": None, "1": "0", "2": "1", "3": "0", "4": None, "5": "4"}, complete=["0", "1"])
        new_coverage = dict(
            parents={key: parent for key, parent in old_coverage["parents"].items() if key != "2"}, complete=["1"]
        )
        presenter.delivered(event(previous, source, old_coverage))
        self.assertIn("Removed element IDs: 2", presenter.text(event(current, source, new_coverage)))

    def test_reordered_or_reparented_survivors_require_full(self):
        previous = observation()
        for change in (
            lambda tree: replace(tree, prepared=replace(tree.prepared, nodes=tuple(reversed(tree.nodes)))),
            lambda tree: map_nodes(tree, lambda n: replace(n, parent="0", depth=1) if n.ref == "2" else n),
        ):
            current = replace_tree(previous, 0, change)
            self.assertEqual(
                plan_document_diff(previous["document"], current["document"], previous["refs"], current["refs"]).mode,
                "full",
            )
            self.assertEqual(snapshotDiff(previous, current), current["document"].text)

    def test_partial_and_multiline_hints_remain_visible_in_unchanged_output(self):
        current = observation("line\n" * 400, partial=True)
        result = snapshotDiff(current, current)
        self.assertIn("Tree: unchanged", result)
        self.assertIn("Omitted:", result)
        self.assertIn("Children:", result)
        self.assertIn("partial", result)
        plan = plan_document_diff(current["document"], current["document"], current["refs"], current["refs"])
        self.assertTrue({"0", "1", "2"} <= plan.trees[0].context)

    def test_cross_root_labels_are_kept_with_their_ancestors(self):
        def relate(tree):
            return map_nodes(
                tree, lambda n: replace(n, fields=freeze({**n.fields, "_contextRefs": ("5",)})) if n.ref == "2" else n
            )

        previous = replace_tree(observation(), 0, relate)
        current = replace_tree(observation("after"), 0, relate)
        plan = plan_document_diff(previous["document"], current["document"], previous["refs"], current["refs"])
        self.assertTrue({"4", "5"} <= plan.trees[1].context)
        result = render_document(current["document"], plan, public_api=True)
        self.assertIn("File", result)
        self.assertNotIn("unrelated content", result)

    def test_text_only_data_is_full_and_cannot_override_a_structured_document(self):
        previous = observation()
        self.assertEqual(
            snapshotDiff(previous, dict(document=None, refs={}, text="unstructured input")), "unstructured input"
        )
        current = dict(previous, text="misleading caller text")
        self.assertIn("Tree: unchanged", snapshotDiff(previous, current))
        self.assertNotIn("misleading caller text", snapshotDiff(previous, current))

    def test_identical_tree_numbers_in_distinct_sections_keep_their_own_context(self):
        previous = observation()
        menu = normalizeFacts(
            batch(
                [node("window", AXRole="AXMenuBar"), node("form", AXRole="AXMenuBarItem", AXTitle="File")],
                [edge("window", "form")],
            ),
            DisplayReferences("session"),
        )
        current = replace_tree(previous, 1, lambda tree: replace(tree, prepared=prepareSnapshot(menu)))
        self.assertIn("Tree: unchanged", snapshotDiff(current, current))

    def test_baseline_budget_counts_structured_data_without_rendering(self):
        sample, source = observation(), Record()
        observed = event(sample, source)
        presenter = ObservationPresenter()
        with patch(
            "computer_use.interaction.read.presentation.rendering.document.render_document",
            side_effect=AssertionError("baseline must retain structure"),
        ):
            presenter.delivered(observed)
            baseline = next(iter(presenter._baselines.values()))
            self.assertIs(baseline["document"], sample["document"])
            self.assertNotIn("text", baseline)
            self.assertIsNone(peekObservationText(observed["state"]))
            self.assertGreaterEqual(baseline["bytes"], len(utf8(dumps(sample["document"].to_data()))))
            tiny = ObservationPresenter(maxBytes=100)
            tiny.delivered(observed)
        self.assertFalse(tiny._baselines)

    def test_diff_planning_never_invokes_a_text_formatter(self):
        with patch(
            "computer_use.interaction.read.presentation.rendering.document.render_document",
            side_effect=AssertionError("planning invoked renderer"),
        ):
            previous, current = observation(), observation("after")
            plan = plan_document_diff(previous["document"], current["document"], previous["refs"], current["refs"])
        self.assertEqual(plan.mode, "diff")
        self.assertEqual(plan.trees[0].changes, {"2": "~"})

    def test_full_and_diff_use_the_same_terminal_renderer(self):
        previous, current = observation(), observation("after")
        with patch(
            "computer_use.interaction.read.presentation.rendering.document.render_document", wraps=render_document
        ) as renderer:
            full = current["document"].text
            diff = snapshotDiff(previous, current)
        self.assertEqual(renderer.call_count, 2)
        self.assertIn("Tree: full", full)
        self.assertIn("Tree: diff", diff)
        self.assertIn("after", full)
        self.assertIn("after", diff)


if __name__ == "__main__":
    unittest.main()
