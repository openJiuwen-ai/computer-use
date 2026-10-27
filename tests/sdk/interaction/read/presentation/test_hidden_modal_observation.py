"""Hidden modal templates must not disable visible controls in the display."""

import unittest

from observation_fixture import normalize_fixture as normalizeFacts

from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.rendering.formatting import renderTree
from computer_use.interaction.read.presentation.tree import withInteractions


def facts(visible=False, showing=False):
    nodes = [
        dict(node="window", values=dict(role="frame", name="Document", modal=False)),
        dict(node="button", values=dict(role="push button", name="Apply", enabled=True)),
        dict(
            node="template",
            values=dict(
                role="filler",
                name="",
                modal=True,
                **({} if visible is None else dict(visible=visible)),
                **({} if showing is None else dict(showing=showing)),
            ),
        ),
    ]
    for node in nodes:
        node.update(
            requestedFields=list(node["values"]),
            fieldStatus={},
            actions=dict(
                state="complete", items=[dict(name="click", description="Click")] if node["node"] == "button" else []
            ),
        )
    return dict(
        kind="nodes",
        session="s",
        platform="linux",
        surface="surface",
        observed=dict(id="stamp", startedAt=1, completedAt=2),
        roots=["window"],
        nodes=nodes,
        requestedRelations=["children"],
        relations=[
            dict(
                source=n["node"],
                property="children",
                targets=["button", "template"] if n["node"] == "window" else [],
                certainty="confirmed",
                state="complete",
            )
            for n in nodes
        ],
        unread=[],
        stopReasons=[],
    )


class HiddenModalObservationTests(unittest.TestCase):
    def test_hidden_template_keeps_native_modality_without_blocking_the_window(self):
        snapshot = normalizeFacts(facts(), DisplayReferences("s"))
        root = withInteractions(snapshot.roots[0])
        modal = root["children"][1]
        self.assertEqual(root["blockedBy"], [])
        self.assertEqual(root["children"][0]["blockedBy"], [])
        self.assertEqual((modal["modal"], modal["visible"], modal["showing"]), (True, False, False))
        rendered = snapshot.render()
        self.assertNotIn("Blocked by:", rendered["text"])
        self.assertIn("modal, hidden", rendered["text"])
        self.assertEqual(snapshot.facts["nodes"][2]["values"]["states.modal"], True)
        self.assertEqual(set(rendered["refs"].values()), {"window", "button", "template"})

    def test_missing_offscreen_or_conflicting_visibility_does_not_waive_modal_blocking(self):
        for visible, showing in [
            (None, None),
            (False, None),
            (None, False),
            (True, False),
            (False, True),
            (True, True),
        ]:
            with self.subTest(visible=visible, showing=showing):
                snapshot = normalizeFacts(facts(visible, showing), DisplayReferences("s"))
                root = withInteractions(snapshot.roots[0])
                self.assertEqual(root["blockedBy"], ["2"])
                self.assertEqual(root["children"][0]["blockedBy"], ["2"])
                self.assertIn("Blocked by: 2", snapshot.render()["text"])

    def test_visible_modal_still_blocks_with_a_hidden_sibling(self):
        snapshot = normalizeFacts(facts(), DisplayReferences("s"))
        root = snapshot.roots[0]
        root["children"].append(dict(ref="active", role="sheet", modal=True, visible=True, showing=True, children=[]))
        result = withInteractions(root)
        self.assertEqual(result["blockedBy"], ["active"])
        self.assertEqual(result["children"][0]["blockedBy"], ["active"])
        self.assertEqual(result["children"][-1]["blockedBy"], [])

    def test_header_does_not_call_hidden_template_an_active_interaction(self):
        snapshot = normalizeFacts(facts(), DisplayReferences("s"))
        text = renderTree(withInteractions(snapshot.roots[0]), dict(app="fixture", includeBackground=True))["text"]
        self.assertNotIn("Interaction:", text)
        self.assertIn("hidden", text)
