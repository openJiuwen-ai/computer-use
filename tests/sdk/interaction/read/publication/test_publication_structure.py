"""Publication retains capture structure until callers request output text."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from observation_fixture import normalize_fixture as normalizeFacts
from sdk.interaction.read.facts.test_native_facts import batch, edge, node

from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.content import text
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.preparation import prepareSnapshot
from computer_use.interaction.read.publication.model import PublicationRequest
from computer_use.interaction.read.publication.pipeline import ObservationPublicationPipeline
from computer_use.native.contracts import Binding
from computer_use.session.observation import peekObservationText


class PublicationStructureTests(unittest.TestCase):
    @staticmethod
    def fixture():
        snapshot = normalizeFacts(
            batch(
                [
                    node("root", AXRole="AXWindow", AXTitle="Captured"),
                    node(
                        "field",
                        AXRole="AXTextField",
                        AXValue="Original value",
                        VendorData={"empty": "", "false": False},
                    ),
                ],
                [edge("root", "field")],
            ),
            DisplayReferences("session"),
        )
        document = DisplayDocument.join(
            (
                "App: Fixture.",
                "Tree: full",
                DisplayDocument.tree(prepareSnapshot(snapshot, compact=True, public_api=False)),
            )
        )
        events, hints = [], []

        def page_hints(binding, source, **options):
            hints.append((binding, source, options))
            return text("")

        controller = SimpleNamespace(
            transport=SimpleNamespace(capabilities=frozenset({"nativeRelationPages"})),
            pages=SimpleNamespace(plan_hints=page_hints),
            _overview_image=lambda overview: None,
            _window_ref=lambda state, target: None,
            publishObservation=events.append,
        )
        request = PublicationRequest(
            object(),
            SimpleNamespace(windows={}, selection=None, selector="Fixture"),
            document,
            snapshot,
            Binding("session", "instance", "root-window", "surface", "root"),
        )
        return controller, request, events, hints

    def test_publication_and_retained_details_do_not_materialize_any_text(self):
        from computer_use.interaction.read.presentation.rendering import document

        controller, request, events, hints = self.fixture()
        with patch.object(document, "render_document", wraps=document.render_document) as renderer:
            result = ObservationPublicationPipeline(controller).run(request)
            self.assertEqual(renderer.call_count, 0)
            self.assertIsNone(peekObservationText(result))
            self.assertEqual(len(events), 1)
            self.assertIs(events[0]["state"], result)
            self.assertIsInstance(events[0]["document"], DisplayDocument)
            self.assertEqual(len(hints), 1)
            self.assertEqual(set(result), {"text", "details", "screenshot", "overview"})
            self.assertIn("Original value", result.text)
            self.assertEqual(renderer.call_count, 1)
            self.assertIn("Details: observation.details", result.text)
            self.assertEqual(renderer.call_count, 1)
            self.assertIn("Captured native details", result.details)
            self.assertIn("VendorData", result.details)
            self.assertEqual(renderer.call_count, 2)
            self.assertEqual(result.details, result["details"])
            self.assertEqual(renderer.call_count, 2)

    def test_publication_detaches_detail_facts_before_first_text_access(self):
        controller, request, _, _ = self.fixture()
        result = ObservationPublicationPipeline(controller).run(request)
        request.tree.roots[1:] = []
        request.tree.facts["nodes"][1]["values"]["AXValue"] = "Unrelated later update"
        request.tree.refs.clear()
        self.assertIn("Original value", result.text)
        self.assertNotIn("Unrelated later update", result.details)
        self.assertIn("VendorData", result.details)


if __name__ == "__main__":
    unittest.main()
