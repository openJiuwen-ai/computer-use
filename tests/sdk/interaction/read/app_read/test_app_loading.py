"""Loading evidence over real JSONL; no fake readiness inferred from images."""

import asyncio
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import sdk.interaction.read.test_app_observation as fixture
from observation_fixture import normalize_fixture as normalizeFacts
from sdk.interaction.read.facts.test_native_facts import batch, edge, node

from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.loading import busy_nodes
from computer_use.interaction.read.facts.normalize import normalizeFacts as normalize_observation
from computer_use.interaction.read.numbering.references import DisplayReferences


class AppLoadingTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.AppObservationTests.asyncSetUp
    asyncTearDown = fixture.AppObservationTests.asyncTearDown
    reader = fixture.AppObservationTests.reader
    requests = fixture.AppObservationTests.requests
    nodes = fixture.AppObservationTests.nodes
    images = fixture.AppObservationTests.images

    async def test_busy_region_refreshes_tree_only_and_preserves_component_times(self):
        result = await self.reader("observation-loading").read("Native fixture")
        self.assertEqual(len(self.nodes()), 2)
        self.assertEqual(len(self.images()), 1)
        self.assertIn("Loaded content", result.text)
        self.assertEqual(result.tree.facts["observed"]["id"], "facts-2")
        self.assertEqual(result.image.target.capture, "image-1")
        self.assertIn("screenshot was captured before the refreshed tree", result.text)
        self.assertTrue(result.scope_confirmed)
        self.assertFalse(result.gaps)
        self.assertTrue(all(r["params"]["mode"] == "read" for r in self.requests() if r["method"] == "begin"))
        self.assertFalse(any(r["method"] == "execute" for r in self.requests()))

    async def test_persistent_busy_is_local_visible_and_bounded(self):
        result = await self.reader("observation-loading-persistent").read("Native fixture")
        self.assertEqual(len(self.nodes()), 2)
        self.assertEqual(len(self.images()), 1)
        self.assertTrue(result.scope_confirmed, "Loading does not invalidate the working-window identity")
        self.assertIn("1 container (busy)", result.text)
        self.assertNotIn("0 window (busy)", result.text)
        self.assertIn("Loaded content", result.text)
        self.assertIn("elements 1 are busy", result.text)
        self.assertEqual({gap.component for gap in result.gaps}, {"content"})

    async def test_first_initialization_receipt_refreshes_without_reinitializing(self):
        reader = self.reader("observation-loading-initializing")
        first = await reader.read("Native fixture")
        self.assertIn("Loaded content", first.text)
        self.assertEqual(first.source_directory["provider"]["status"], "requestReturned")
        self.assertEqual(first.directory["provider"]["status"], "alreadyAttempted")
        self.assertEqual(len(self.nodes()), 2)
        await reader.read(first.selection)
        self.assertEqual(len(self.nodes()), 3)
        self.assertEqual(len(self.images()), 2)

    async def test_shell_can_refresh_without_claiming_busy_or_reinitializing(self):
        for mode in ("empty", "unknown", "already-attempted", "malformed", "no-feature"):
            before = len(self.nodes()) if self.journal.exists() else 0
            with self.subTest(mode=mode):
                result = await self.reader("observation-loading-" + mode).read("Native fixture")
                self.assertEqual(len(self.nodes()) - before, 2)
                self.assertEqual(busy_nodes(result.tree), ())
                self.assertNotIn("are busy", result.text)
                if mode == "empty":
                    self.assertIn("No identifiable content controls or text", result.text)
                    self.assertIn("does not establish that the window is empty or ready", result.text)
                else:
                    self.assertIn("Loaded content", result.text)
                    self.assertFalse(any(gap.component == "content" for gap in result.gaps))
                if mode == "unknown":
                    self.assertIn("busy unavailable", result.text)
        self.assertFalse(any(r["method"] == "execute" for r in self.requests()))

    async def test_visible_content_with_unknown_busy_does_not_cause_a_refresh(self):
        reader = self.reader("observation-loading-unknown")
        collect = reader.reader.collect

        async def with_label(*args, **kwargs):
            result = await collect(*args, **kwargs)
            nodes = dict(result.native.nodes)
            body = next(key for key in nodes if key not in result.native.roots)
            nodes[body] = replace(nodes[body], name="Independent content")
            native = replace(result.native, nodes=nodes)
            snapshot = normalize_observation(
                native,
                DisplayReferences(native.session),
                scope_kind=result.scope_kind,
                operation_capabilities=result.collection.operation_capabilities,
            )
            snapshot.collection = result.collection
            return snapshot

        with patch.object(reader.reader, "collect", with_label):
            result = await reader.read("Native fixture")
        self.assertEqual(len(self.nodes()), 1)
        self.assertIn("Independent content", result.text)
        self.assertNotIn("No identifiable content", result.text)
        self.assertEqual(busy_nodes(result.tree), ())

    async def test_explicit_field_or_depth_queries_do_not_diagnose_window_coverage(self):
        for limits in (dict(fields=["AXRole", "AXTitle"]), dict(max_depth=0)):
            with self.subTest(limits=limits):
                before = len(self.nodes()) if self.journal.exists() else 0
                result = await self.reader("observation-loading-empty").read("Native fixture", **limits)
                self.assertEqual(len(self.nodes()) - before, 1)
                self.assertNotIn("No identifiable content", result.text)

    async def test_shell_with_short_budget_reports_uncertainty_without_extra_read(self):
        reader = self.reader("observation-loading-empty")
        now = [0.0]
        discover = reader._discover

        async def late_recheck(*args, **kwargs):
            result = await discover(*args, **kwargs)
            now[0] = 0.85
            return result

        with (
            patch("computer_use.native.lifecycle.time", SimpleNamespace(monotonic=lambda: now[0])),
            patch.object(reader, "_discover", late_recheck),
        ):
            result = await reader.read("Native fixture", timeout_ms=1000)
        self.assertEqual(len(self.nodes()), 1)
        self.assertIn("No identifiable content", result.text)
        self.assertNotIn("after a bounded refresh", result.text)
        self.assertTrue(result.scope_confirmed)

    async def test_view_change_has_priority_and_shares_the_refresh_allowance(self):
        result = await self.reader("observation-loading-scope-first").read("Native fixture")
        self.assertEqual([r["params"]["target"]["node"] for r in self.nodes()], ["node", "dialog-node-2"])
        self.assertEqual(result.target.surface, "dialog-surface-2")
        self.assertEqual(result.image.target.surface, result.target.surface)
        self.assertTrue(result.scope_confirmed)

    async def test_view_changing_after_content_refresh_does_not_start_a_third_attempt(self):
        result = await self.reader("observation-loading-scope-late").read("Native fixture")
        self.assertEqual(len(self.nodes()), 2)
        self.assertEqual(len(self.images()), 1)
        self.assertEqual(result.target.surface, "surface")
        self.assertEqual(result.image.target.surface, "surface")
        self.assertFalse(result.scope_confirmed)
        self.assertIn("Dialog 2", result.text)
        self.assertIn("Current view is not confirmed", result.text)

    async def test_partial_directory_does_not_block_same_bound_view_refresh(self):
        result = await self.reader("observation-loading-partial-directory").read("Native fixture")
        self.assertEqual(len(self.nodes()), 2)
        self.assertFalse(result.scope_confirmed)
        self.assertIn("Loaded content", result.text)
        self.assertIn("provider directory timeout", result.text)
        self.assertEqual(result.target.surface, result.image.target.surface)

    async def test_local_refresh_failure_retains_earlier_tree_with_explicit_gap(self):
        result = await self.reader("observation-loading-refresh-error").read("Native fixture")
        self.assertEqual(len(self.nodes()), 2)
        self.assertEqual(len(self.images()), 1)
        self.assertEqual(result.tree.facts["observed"]["id"], "facts-1")
        self.assertIn("refresh provider timeout", result.text)
        self.assertIn("earlier tree", result.text)
        self.assertIn("are busy", result.text)

    async def test_disconnected_refresh_is_not_hidden_by_previous_components(self):
        with self.assertRaises(ComputerUseError):
            await self.reader("observation-loading-refresh-disconnect").read("Native fixture")
        self.assertEqual(len(self.nodes()), 2)
        self.assertEqual(len(self.images()), 1)

    async def test_short_remaining_budget_publishes_loading_facts_without_refresh(self):
        reader = self.reader("observation-loading")
        now = [0.0]
        original = reader._discover

        async def late_recheck(*args, **kwargs):
            result = await original(*args, **kwargs)
            now[0] = 0.85
            return result

        with (
            patch("computer_use.native.lifecycle.time", SimpleNamespace(monotonic=lambda: now[0])),
            patch.object(reader, "_discover", late_recheck),
        ):
            result = await reader.read("Native fixture", timeout_ms=1000)
        self.assertEqual(len(self.nodes()), 1)
        self.assertIn("are busy", result.text)
        self.assertTrue(result.scope_confirmed)

    async def test_cancelled_refresh_never_publishes_earlier_components(self):
        reader = self.reader("observation-loading")
        from computer_use.interaction.read.acquisition.components.pipeline import ComponentPipeline

        original = ComponentPipeline.arun

        async def cancel_refresh(pipeline, request, control=None):
            if request.include_image is False:
                raise asyncio.CancelledError()
            return await original(pipeline, request, control)

        with patch.object(ComponentPipeline, "arun", cancel_refresh), self.assertRaises(asyncio.CancelledError):
            await reader.read("Native fixture")
        self.assertFalse(reader.port.calls.busy)
        self.assertFalse(reader.port.calls.retired)


class BusyPresentationTests(unittest.TestCase):
    def test_loading_container_and_false_or_unknown_declarations_are_preserved(self):
        raw = batch(
            [
                node("window", AXRole="AXWindow"),
                node("body", AXRole="AXGroup", AXElementBusy=True),
                node("label", AXRole="AXStaticText", AXValue="Visible content", AXElementBusy=False),
            ],
            [edge("window", "body"), edge("body", "label")],
        )
        snapshot = normalizeFacts(raw, DisplayReferences("session"))
        rendered = snapshot.render()
        self.assertEqual(busy_nodes(snapshot), ("body",))
        self.assertIn("1 container (busy)\n\t\t2 static text", rendered["text"])
        self.assertIn("2 states.busy: false", rendered["text"])
        self.assertIs(snapshot.native.nodes["label"].states.busy, False)
        del raw["nodes"][1]["values"]["AXElementBusy"]
        raw["nodes"][1]["fieldStatus"]["AXElementBusy"] = dict(state="timeout")
        uncertain = normalizeFacts(raw, DisplayReferences("session"))
        self.assertFalse(busy_nodes(uncertain))
        self.assertNotIn("(busy)", uncertain.render()["text"])
        self.assertIn("states.busy: timeout", uncertain.render()["text"])


if __name__ == "__main__":
    unittest.main()
