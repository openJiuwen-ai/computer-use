"""Read orchestration exposes real subpipelines and keeps bounded effects."""

import unittest
from unittest.mock import patch

import sdk.interaction.read.test_app_observation as fixture
from observation_fixture import normalize_fixture as normalizeFacts
from sdk.interaction.read.facts.test_native_facts import batch, node

from computer_use.interaction.read.app_read.model import ReadRequest
from computer_use.interaction.read.numbering.pipeline import numberSnapshots
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.pipeline import ApplicationObservationPipeline as AppReadCoordinator
from computer_use.interaction.read.presentation.pipeline import PrepareSnapshotPipeline
from computer_use.pipeline import Control


class ReadPipelineContractTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.AppObservationTests.asyncSetUp
    asyncTearDown = fixture.AppObservationTests.asyncTearDown
    reader = fixture.AppObservationTests.reader
    requests = fixture.AppObservationTests.requests

    async def test_nested_query_and_numbering_paths_are_visible(self):
        reader = self.reader("observation-loading")
        events = []
        observed = await AppReadCoordinator(reader).arun(
            ReadRequest("Native fixture"), Control(diagnostic=events.append)
        )
        self.assertTrue(observed.scope_confirmed)
        entered = [event.path for event in events if event.phase == "enter"]
        facts = [path for path in entered if path[-1] == "collect_native_facts"]
        self.assertEqual(len(facts), 2)
        self.assertTrue(
            all(
                "ReadPipeline" in path and "AcquisitionPipeline" in path and "FactCollectionPipeline" in path
                for path in facts
            )
        )
        self.assertTrue(
            any(
                "NumberedPresentationPipeline" in path
                and "NumberSnapshotsPipeline" in path
                and "PrepareSnapshotRoots" in path
                and "CompressionPipeline" in path
                for path in entered
            )
        )
        self.assertFalse(any("RenderPipeline" in path or "AccountingPipeline" in path for path in entered))
        self.assertEqual(sum(path[-1] == "wait_content_change" for path in entered), 1)
        self.assertFalse(any(path[-1] in ("dispatch_startup", "prepare_native_context") for path in entered))
        self.assertFalse(any(request["method"] == "execute" for request in self.requests()))

    async def test_refresh_reasons_do_not_multiply_collection_allowance(self):
        reader = self.reader("observation-loading-scope-late")
        observed = await AppReadCoordinator(reader).arun(ReadRequest("Native fixture"))
        nodes = [request for request in self.requests() if request["params"].get("query", {}).get("kind") == "nodes"]
        self.assertEqual(len(nodes), 2)
        self.assertFalse(observed.scope_confirmed)
        self.assertFalse(reader.port.calls.busy)


class NumberingProgressContractTests(unittest.TestCase):
    def test_previous_part_aliases_survive_later_presentation_failure(self):
        first = normalizeFacts(batch([node("one", AXRole="AXButton", AXTitle="One")]), DisplayReferences("session"))
        second = normalizeFacts(batch([node("two", AXRole="AXButton", AXTitle="Two")]), DisplayReferences("session"))
        references = DisplayReferences("session")
        prepare = PrepareSnapshotPipeline.run

        def fail_second(pipeline, request, control=None):
            if request.snapshot is second:
                raise MemoryError("fixture")
            return prepare(pipeline, request, control)

        with patch.object(PrepareSnapshotPipeline, "run", fail_second), self.assertRaises(MemoryError):
            numberSnapshots((first, second), references)
        self.assertEqual(list(references._refs), [("surface", None, "one")])
        self.assertEqual(references._next, 1)


if __name__ == "__main__":
    unittest.main()
