"""First-read startup is one bounded call over separate native read/write stages."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import sdk.interaction.read.test_app_observation as fixture

from computer_use.errors import ComputerUseError
from computer_use.interaction.read.presentation.pipeline import PrepareSnapshotPipeline
from computer_use.interaction.write.engine import Engine
from computer_use.native.contracts import ApplicationBinding, Intent


class AppStartupTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.AppObservationTests.asyncSetUp
    asyncTearDown = fixture.AppObservationTests.asyncTearDown
    reader = fixture.AppObservationTests.reader
    requests = fixture.AppObservationTests.requests
    nodes = fixture.AppObservationTests.nodes
    images = fixture.AppObservationTests.images

    def launches(self):
        return [
            r
            for r in self.requests()
            if r["method"] == "execute" and r["params"]["command"]["kind"] == "launch_application"
        ]

    def surfaces(self):
        return [r for r in self.requests() if r["params"].get("query", {}).get("kind") == "surfaces"]

    async def test_first_read_starts_observed_installation_then_binds_receipt_instance(self):
        reader = self.reader("observation-launch")
        result = await reader.read("Native fixture")
        calls = self.requests()
        self.assertEqual(
            [r["params"]["mode"] for r in calls if r["method"] == "begin"],
            ["read", "write", "read", "read", "read", "read", "read"],
        )
        self.assertEqual(
            [r["method"] for r in calls],
            [
                "hello",
                "begin",
                "inspect",
                "finish",
                "begin",
                "inspect",
                "execute",
                "finish",
                "begin",
                "inspect",
                "finish",
                "begin",
                "inspect",
                "capture",
                "finish",
                "begin",
                "inspect",
                "finish",
                "begin",
                "inspect",
                "finish",
                "begin",
                "inspect",
                "finish",
            ],
        )
        self.assertEqual(len(self.nodes()), 2)
        self.assertEqual(len(self.images()), 1)
        self.assertEqual(result.launch["dispatch"], "dispatched")
        self.assertEqual(result.selection.application, "running:launched")
        self.assertTrue(all(r["params"]["query"]["application"] == "running:launched" for r in self.surfaces()))
        self.assertIn("Launch:", result.text)
        self.assertTrue(result.scope_confirmed)
        with self.assertRaises(TypeError):
            result.launch["application"]["id"] = "foreign"
        second = await reader.read(result.selection)
        self.assertIsNone(second.launch)
        self.assertEqual(second.selection, result.selection)
        self.assertEqual(len(self.launches()), 1)
        self.assertEqual(sum(r["params"].get("query", {}).get("kind") == "applications" for r in self.requests()), 1)

    async def test_existing_instance_receipt_has_no_launch_effect(self):
        result = await self.reader("observation-launch-reuse").read("Native fixture")
        self.assertEqual(result.launch["dispatch"], "notDispatched")
        self.assertIn("already running", result.text)

    async def test_delayed_first_window_then_content_refresh_never_relaunches(self):
        result = await self.reader("observation-launch-delayed").read("Native fixture")
        self.assertTrue(result.scope_confirmed)
        self.assertEqual(len(self.launches()), 1)
        self.assertEqual(len(self.surfaces()), 4)  # empty, first window, recheck after each tree
        self.assertEqual(len(self.nodes()), 2)
        self.assertEqual(len(self.images()), 1)

    async def test_empty_window_wait_is_bounded_and_preserves_launch_effect(self):
        with self.assertRaises(ComputerUseError) as caught:
            await self.reader("observation-launch-empty").read("Native fixture")
        self.assertEqual(caught.exception.effect, "possible")
        self.assertEqual(caught.exception.code, "observation_incomplete")
        self.assertEqual(caught.exception.launch["application"]["id"], "running:launched")
        self.assertEqual(len(self.surfaces()), 5)
        self.assertEqual(len(self.launches()), 1)
        self.assertFalse(self.nodes())
        self.assertFalse(self.images())

    async def test_partial_or_ambiguous_window_directory_is_not_retried_or_hidden(self):
        for mode, code in [
            ("observation-launch-partial", "observation_incomplete"),
            ("observation-launch-multiple", "ambiguous_target"),
        ]:
            before = len(self.surfaces()) if self.journal.exists() else 0
            with self.subTest(mode=mode), self.assertRaises(ComputerUseError) as caught:
                await self.reader(mode).read("Native fixture")
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(caught.exception.effect, "possible")
            # Multiple windows stay ambiguous; their overview is followed by
            # one catalogue refresh, not a startup retry or an implicit choice.
            expected_reads = 2 if mode == "observation-launch-multiple" else 1
            self.assertEqual(len(self.surfaces()) - before, expected_reads)
        self.assertFalse(self.nodes())

    async def test_failed_read_after_reusing_a_running_instance_does_not_claim_launch(self):
        with self.assertRaises(ComputerUseError) as caught:
            await self.reader("observation-launch-reuse-empty").read("Native fixture")
        self.assertEqual(caught.exception.effect, "none")
        self.assertEqual(caught.exception.launch["dispatch"], "notDispatched")
        self.assertEqual(len(self.surfaces()), 1)

    async def test_observation_failure_after_launch_retains_original_error_and_receipt(self):
        with self.assertRaises(ComputerUseError) as caught:
            await self.reader("observation-launch-directory-error").read("Native fixture")
        self.assertEqual(caught.exception.effect, "possible")
        self.assertEqual(caught.exception.code, "execution_failed")
        self.assertIn("Directory read failed", caught.exception.detail)
        self.assertIn("read the same App again", caught.exception.next)
        self.assertEqual(len(self.launches()), 1)

    async def test_fixed_window_closing_does_not_relaunch_or_select_another_document(self):
        with self.assertRaises(ComputerUseError) as caught:
            await self.reader("observation-launch-root-gone").read("Native fixture")
        self.assertEqual(caught.exception.code, "stale_target")
        self.assertEqual(caught.exception.effect, "possible")
        self.assertEqual(len(self.launches()), 1)
        self.assertEqual(len(self.nodes()), 1)

    async def test_unknown_launch_stops_before_reading_windows(self):
        with self.assertRaises(ComputerUseError) as caught:
            await self.reader("observation-launch-uncertain").read("Native fixture")
        self.assertEqual(caught.exception.effect, "possible")
        self.assertEqual(len(self.launches()), 1)
        self.assertFalse(self.surfaces())

    async def test_missing_launch_capability_never_opens_a_write_stage(self):
        with self.assertRaises(ComputerUseError) as caught:
            await self.reader("observation-launch-missing-capability").read("Native fixture")
        self.assertEqual(caught.exception.code, "mode_unsupported")
        self.assertEqual(caught.exception.effect, "none")
        self.assertFalse(self.launches())
        self.assertTrue(all(r["params"]["mode"] == "read" for r in self.requests() if r["method"] == "begin"))

    async def test_an_explicit_old_window_id_never_causes_a_new_launch(self):
        with self.assertRaises(ComputerUseError) as caught:
            await self.reader("observation-launch").read("Native fixture", window="old-window")
        self.assertEqual(caught.exception.code, "stale_target")
        self.assertEqual(caught.exception.effect, "none")
        self.assertFalse(self.launches())

    async def test_all_stages_including_launch_share_remaining_budget(self):
        reader = self.reader("observation-launch")
        now = [0.0]
        original = reader.port.finish

        async def finish(operation):
            value = await original(operation)
            now[0] += 0.2
            return value

        with (
            patch("computer_use.native.lifecycle.time", SimpleNamespace(monotonic=lambda: now[0])),
            patch.object(reader.port, "finish", finish),
        ):
            await reader.read("Native fixture", timeout_ms=2000)
        budgets = [r["timeoutMs"] for r in self.requests() if r["method"] == "begin"]
        self.assertEqual(len(budgets), 7)
        self.assertTrue(all(right <= left - 199 for left, right in zip(budgets, budgets[1:])), budgets)

    async def test_deadline_after_launch_does_not_erase_effect_or_start_more_stages(self):
        reader = self.reader("observation-launch")
        now = [0.0]
        original = reader.port.execute

        async def execute(*args, **kwargs):
            result = await original(*args, **kwargs)
            now[0] = 2.0
            return result

        with (
            patch("computer_use.native.lifecycle.time", SimpleNamespace(monotonic=lambda: now[0])),
            patch.object(reader.port, "execute", execute),
            self.assertRaises(ComputerUseError) as caught,
        ):
            await reader.read("Native fixture", timeout_ms=1000)
        self.assertEqual(caught.exception.effect, "possible")
        self.assertIn("budget exhausted", caught.exception.detail)
        self.assertFalse(self.surfaces())
        self.assertFalse(reader.port.calls.retired)

    async def test_cancellation_after_launch_keeps_cancel_semantics_and_effect_evidence(self):
        reader = self.reader("observation-launch")
        with (
            patch.object(PrepareSnapshotPipeline, "run", side_effect=asyncio.CancelledError()),
            self.assertRaises(asyncio.CancelledError) as caught,
        ):
            await reader.read("Native fixture")
        self.assertEqual(caught.exception.launch["dispatch"], "dispatched")
        self.assertIn("launch was not undone", caught.exception.__notes__[0])
        self.assertFalse(reader.port.calls.busy)
        self.assertFalse(reader.port.calls.retired)

    async def test_uncertain_write_cleanup_blocks_every_reader_and_new_engine(self):
        reader = self.reader("observation-launch-cleanup-unknown")
        with self.assertRaises(ComputerUseError) as caught:
            await reader.read("Native fixture")
        self.assertEqual(caught.exception.effect, "possible")
        count = len(self.requests())
        with self.assertRaisesRegex(RuntimeError, "retired"):
            await reader.directory.applications()
        with self.assertRaisesRegex(ComputerUseError, "retired"):
            await Engine(reader.port).execute(
                Intent("launch"), ApplicationBinding("native-v4-fixture", "installation-observed")
            )
        self.assertEqual(len(self.requests()), count)
        self.assertFalse(self.surfaces())


if __name__ == "__main__":
    unittest.main()
