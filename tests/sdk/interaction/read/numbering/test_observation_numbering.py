"""Presentation order cannot reassign a session number to another target."""

import unittest
from copy import deepcopy
from unittest.mock import patch

from display_documents import fixture_renderer, with_document
from observation_fixture import normalize_fixture as normalizeFacts
from sdk.interaction.read.facts.test_native_facts import batch, edge, node

from computer_use.interaction.read.acquisition.reader import capturedChildren, capturedField
from computer_use.interaction.read.delivery.diff.formatting import snapshotDiff
from computer_use.interaction.read.numbering.pipeline import numberSnapshots
from computer_use.interaction.read.numbering.references import DisplayReferences


def visible(snapshot):
    return [ref for part in snapshot.render(compact=True)["projections"] for ref in part["refs"]]


class NumberingTests(unittest.TestCase):
    @staticmethod
    def raw_snapshot():
        label = node("caption", AXRole="AXStaticText", AXValue="Wi-Fi 503")
        label["actions"]["items"] = [dict(name="AXShowMenu", description="Show menu")]
        raw = batch(
            [
                node("window", AXRole="AXWindow"),
                node("wrapper", AXRole="AXGroup"),
                node("button", AXRole="AXButton", AXTitle="Button 123"),
                node("row", AXRole="AXRow", AXSelected=True),
                node("cell", AXRole="AXCell"),
                label,
            ],
            [edge("window", "wrapper", "button"), edge("wrapper", "row"), edge("row", "cell"), edge("cell", "caption")],
        )
        return raw

    def snapshot(self):
        raw = self.raw_snapshot()
        references = DisplayReferences("session")
        for index in range(60):
            references.reference("earlier-" + str(index))
        return normalizeFacts(raw, references, driver_features=("nativeActionIntents",), scope_kind="window")

    def test_final_rows_are_contiguous_after_compression_and_raw_sources_survive(self):
        original = self.snapshot()
        before = deepcopy(original.facts)
        old_refs = dict(original.refs)
        (numbered,) = numberSnapshots((original,))
        self.assertEqual(visible(numbered), ["0", "1", "2"])
        self.assertEqual([numbered.refs[ref] for ref in visible(numbered)], ["window", "row", "button"])
        self.assertEqual(numbered.refs["5"], "caption")
        self.assertIn("Show menu [element 5]", numbered.render()["details"])
        self.assertIn("Wi-Fi 503", numbered.render(compact=True)["text"])
        self.assertIn("Button 123", numbered.render(compact=True)["text"])
        self.assertEqual(capturedField(numbered, "5", "control_value.value").value, "Wi-Fi 503")
        self.assertEqual(numbered.facts, before)
        self.assertEqual(original.facts, before)
        self.assertEqual(original.refs, old_refs)
        self.assertEqual(set(numbered.refs.values()), set(old_refs.values()))
        self.assertEqual(numbered.driver_features, original.driver_features)
        self.assertEqual(numbered.scope_kind, original.scope_kind)
        self.assertIs(numberSnapshots((numbered,))[0], numbered)

    def test_window_and_menus_share_visible_sequence_before_hidden_aliases(self):
        menu = normalizeFacts(
            batch(
                [node("menu", AXRole="AXMenuBar"), node("file", AXRole="AXMenuBarItem", AXTitle="File")],
                [edge("menu", "file")],
            ),
            DisplayReferences("session"),
            scope_kind="applicationMenu",
        )
        first, second = numberSnapshots((self.snapshot(), menu))
        self.assertEqual(visible(first) + visible(second), ["0", "1", "2", "3", "4"])
        self.assertTrue(set(first.refs).isdisjoint(second.refs))
        self.assertEqual(first.refs["7"], "caption")
        self.assertEqual(second.scope_kind, "applicationMenu")
        self.assertEqual(visible(numberSnapshots((menu,))[0]), ["0", "1"])

    def test_same_native_object_and_scope_keep_their_aliases(self):
        first, second = numberSnapshots((self.snapshot(), self.snapshot()))
        self.assertEqual(first.refs, second.refs)

    def test_reordering_menu_and_subtree_reads_never_recycle_a_number(self):
        refs = DisplayReferences("session")
        (before,) = numberSnapshots((self.snapshot(),), refs)
        menu = normalizeFacts(
            batch([node("attach", AXRole="AXMenuItem", AXTitle="Attach")]),
            DisplayReferences("session"),
            scope_kind="applicationMenu",
        )
        (opened,) = numberSnapshots((menu,), refs)
        (after,) = numberSnapshots((self.snapshot(),), refs)
        self.assertEqual(before.refs, after.refs)
        self.assertTrue(set(before.refs).isdisjoint(opened.refs))
        self.assertNotIn("Attach", after.render(compact=True)["text"])
        changed = batch([node("send", AXRole="AXMenuItem", AXTitle="Send")])
        (sent,) = numberSnapshots(
            (normalizeFacts(changed, DisplayReferences("session"), scope_kind="applicationMenu"),), refs
        )
        self.assertTrue(set(sent.refs).isdisjoint(opened.refs))

    def test_subtree_reuses_only_scoped_numbers_and_new_nodes_follow_reserved_refs(self):
        original = self.snapshot()
        before = deepcopy(original.facts)
        references = DisplayReferences("session")
        (numbered,) = numberSnapshots((original,), references)
        raw = self.raw_snapshot()
        raw["nodes"].append(node("new", AXRole="AXButton", AXTitle="New"))
        for relation in raw["relations"]:
            if relation["source"] == "button" and relation["property"] == "AXChildren":
                relation.update(targets=["new"], state="complete")
        raw["relations"].append(edge("new", state="absent"))
        subtree = normalizeFacts(raw, DisplayReferences("session"), scope_kind=original.scope_kind)
        (extended,) = numberSnapshots((subtree,), references)
        for ref, token in numbered.refs.items():
            self.assertEqual(extended.refs[ref], token)
        self.assertGreaterEqual(
            int(next(ref for ref, token in extended.refs.items() if token == "new")), len(numbered.refs)
        )
        self.assertEqual(original.facts, before)
        # Equal opaque tokens in another native window remain distinct targets.
        raw["surface"] = "another-surface"
        (other,) = numberSnapshots(
            (normalizeFacts(raw, DisplayReferences("session"), scope_kind=original.scope_kind),), references
        )
        self.assertTrue(set(numbered.refs).isdisjoint(other.refs))

    def test_partial_relations_and_text_continuations_keep_native_carriers(self):
        text = node("text", AXRole="AXTextField", AXValue="preview")
        text["fieldStatus"]["AXValue"] = dict(state="truncated", textRef="captured:original", totalUnits=100)
        raw = batch(
            [text, node("root", AXRole="AXGroup")],
            [edge("root", "text", state="partial", total=10, offset=2, cursor="cursor:original")],
            roots=["root"],
        )
        snapshot = normalizeFacts(raw, DisplayReferences("session"), driver_features=("nativeNodePages",))
        (numbered,) = numberSnapshots((snapshot,))
        self.assertEqual(visible(numbered), ["0", "1"])
        self.assertEqual(numbered.refs, {"1": "text", "0": "root"})
        self.assertEqual(capturedField(numbered, "1", "control_value.value").text_ref, "captured:original")
        self.assertEqual(capturedChildren(numbered, "0").cursor, "cursor:original")
        self.assertIn('app.element("0").read({"offset": 3})', numbered.render(compact=True)["text"])
        self.assertEqual(numbered.facts, snapshot.facts)

    def test_unread_and_cyclic_relations_use_new_aliases_without_losing_targets(self):
        raw = batch(
            [node("child", AXRole="AXGroup"), node("root", AXRole="AXWindow")],
            [edge("root", "child", "unread"), edge("child", "root")],
            roots=["root"],
        )
        raw["unread"] = ["unread"]
        original = normalizeFacts(raw, DisplayReferences("session"))
        (numbered,) = numberSnapshots((original,))
        self.assertEqual(visible(numbered), ["0", "1"])
        self.assertEqual(numbered.refs, {"1": "child", "0": "root", "2": "unread"})
        self.assertIn("1 contains 0 (cycle)", numbered.render()["details"])
        self.assertIn("0 contains 2 (unread)", numbered.render()["details"])
        self.assertEqual(numbered.facts, original.facts)

    def test_projection_failure_numbers_the_lossless_fallback_in_its_display_order(self):
        original = self.snapshot()
        with patch(
            "computer_use.interaction.read.presentation.semantics.passes.compose_display_semantics.composeControls",
            side_effect=ValueError("fixture"),
        ):
            (numbered,) = numberSnapshots((original,))
            self.assertEqual(visible(numbered), [str(i) for i in range(6)])
            self.assertIn("Presentation fallback for 0", numbered.render(compact=True)["text"])
        self.assertEqual(numbered.facts, original.facts)
        self.assertEqual(numbered.refs["5"], "button")

    def test_numbers_are_platform_independent(self):
        for platform, role, title in (("windows", "ControlType", "Name"), ("linux", "role", "name")):
            with self.subTest(platform=platform):
                values = {role: "ControlType.Button" if platform == "windows" else "push button", title: "Test"}
                raw = batch([node("button", **values)])
                raw["platform"] = platform
                references = DisplayReferences("session")
                references.reference("past")
                original = normalizeFacts(raw, references)
                (numbered,) = numberSnapshots((original,))
                self.assertEqual(visible(numbered), ["0"])
                self.assertEqual(numbered.facts, original.facts)

    @fixture_renderer()
    def test_diff_requires_the_same_native_identity_for_shared_numbers(self):
        text = "App: Test.\nTree: full\n0 window Test\n\t1 text Before\n\t2 text " + "Other content " * 100
        previous = with_document(dict(text=text, refs={"0": "window", "1": "old", "2": "other"}))
        for text_change in (False, True):
            current = with_document(
                dict(
                    text=text.replace("Before", "After") if text_change else text,
                    refs={"0": "window", "1": "new", "2": "other"},
                )
            )
            self.assertEqual(snapshotDiff(previous, current), current["text"])
        current = with_document(dict(text=text.replace("Before", "After"), refs=previous["refs"]))
        self.assertIn("Tree: diff", snapshotDiff(previous, current))
        self.assertIn("Tree: unchanged", snapshotDiff(previous, previous))


if __name__ == "__main__":
    unittest.main()
