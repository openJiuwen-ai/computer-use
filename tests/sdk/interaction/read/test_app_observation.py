import asyncio
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from repository import ROOT as REPO_ROOT

from computer_use.errors import ComputerUseError
from computer_use.interaction.read.app import NativeAppReader
from computer_use.interaction.read.model import WindowSelectionRequired
from computer_use.interaction.read.presentation.pipeline import PrepareSnapshotPipeline
from computer_use.native.connection import Connection
from computer_use.native.contracts import HostPolicy, Intent
from computer_use.native.scopes import SelectedApplicationWindow
from computer_use.native.transport import ConnectionPort

"Whole-call observation contracts over the production JSONL connection."


ROOT = REPO_ROOT


class AppObservationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="native-observation-")
        self.journal = Path(self.directory.name) / "wire.jsonl"
        self.connections = []

    async def asyncTearDown(self):
        for connection in self.connections:
            connection.close()
            await connection.waitClosed()
        self.directory.cleanup()

    def reader(self, mode="observation"):
        connection = Connection(
            lambda: None,
            dict(
                command=sys.executable,
                args=[str(ROOT / "tests/fixtures/v5-driver.py"), mode, str(self.journal)],
                timeoutMs=3000,
            ),
            protocol_version=6,
        )
        self.connections.append(connection)
        return NativeAppReader(ConnectionPort(connection, HostPolicy()))

    def requests(self):
        return [value for line in self.journal.read_text().splitlines() if "method" in (value := json.loads(line))]

    def nodes(self):
        return [r for r in self.requests() if r["params"].get("query", {}).get("kind") == "nodes"]

    def images(self):
        return [r for r in self.requests() if r["method"] == "capture"]

    async def test_native_read_prepares_structures_without_rendering_tree_or_header_text(self):
        from computer_use.interaction.read.presentation import content
        from computer_use.interaction.read.presentation.rendering import document
        from computer_use.interaction.read.presentation.rendering.passes import render_semantic_lines

        reader = self.reader("observation-sdk-menu")
        with (
            patch.object(document, "render_document", wraps=document.render_document) as final_renderer,
            patch.object(
                render_semantic_lines, "renderProjectionLines", wraps=render_semantic_lines.renderProjectionLines
            ) as tree_renderer,
            patch.object(content, "render_text", wraps=content.render_text) as field_renderer,
        ):
            observed = await reader.read("Native fixture")
            self.assertIsNotNone(observed.tree)
            self.assertTrue(observed.refs)
            self.assertEqual(final_renderer.call_count, 0)
            self.assertEqual(tree_renderer.call_count, 0)
            self.assertEqual(field_renderer.call_count, 0)
            self.assertNotIn("text", observed.__dict__)
            first = observed.text
            self.assertIn("Tree: full", first)
            self.assertIn("Application menu (separate from the working window):", first)
            self.assertEqual(final_renderer.call_count, 1)
            self.assertGreater(tree_renderer.call_count, 0)
            self.assertEqual(observed.text, first)
            self.assertEqual(final_renderer.call_count, 1)

    async def test_retained_native_observation_text_uses_immutable_captured_facts(self):
        observed = await self.reader().read("Native fixture")
        before = observed.document.to_data()
        observed.tree.roots[0]["name"] = "Later semantic mutation"
        observed.tree.facts["nodes"][0]["values"]["AXTitle"] = "Later native mutation"
        observed.tree.refs.clear()
        self.assertEqual(observed.document.to_data(), before)
        self.assertNotIn("Later semantic mutation", observed.text)
        self.assertNotIn("Later native mutation", observed.text)
        self.assertIn("Tree: full", observed.text)

    async def test_desktop_window_selector_binds_exact_window_despite_same_titles(self):
        reader = self.reader("observation-sdk-window-selector")
        observation = await reader.read("second-instance")
        self.assertEqual(observation.selection.root.root_window, "second-root")
        self.assertEqual(observation.target.surface, "second-surface")
        repeated = await reader.read(observation.selection)
        self.assertEqual(repeated.selection, observation.selection)
        self.assertTrue(all(r["params"]["target"]["rootWindow"] == "second-root" for r in self.nodes()))

    async def test_focus_in_another_window_is_shown_without_changing_the_observed_scope(self):
        reader = self.reader("observation-sdk-window-selector")
        collect = reader.directory.collect

        async def with_focus(*args, **kwargs):
            result = await collect(*args, **kwargs)
            return {**result, "focusedWindow": "root"} if result["kind"] == "surfaces" else result

        with patch.object(reader.directory, "collect", with_focus):
            observed = await reader.read("second-instance")
            self.assertIn("reported focus", observed.text)
            self.assertEqual(observed.directory["focusedWindow"], "root")
            self.assertEqual(observed.target.surface, "second-surface")
            self.assertEqual(observed.selection.root.root_window, "second-root")
            self.assertTrue(observed.scope_confirmed)
            self.assertTrue(all(r["params"]["target"]["rootWindow"] == "second-root" for r in self.nodes()))
            aliases = {window: ref for ref, window in observed.windows.items()}
            self.assertIn(
                f"Interaction: the application reports focus in window {aliases['root']}; "
                f"this view is window {aliases['second-root']}.",
                observed.text,
            )
            self.assertIn(f'app.window("{aliases["root"]}").read()', observed.text)
            self.assertFalse(any(r["method"] == "execute" for r in self.requests()))
            unconfirmed = reader._text(
                observed.selection,
                observed.target,
                observed.directory,
                observed.directory,
                observed.windows,
                {"text": "0 window"},
                None,
                [],
                False,
                None,
            )
            self.assertNotIn("Interaction: the application reports focus", unconfirmed)

    async def test_unmatched_native_window_keeps_tree_capture_and_scope(self):
        reader = self.reader()
        collect = reader.directory.collect

        async def without_native_window(*args, **kwargs):
            result = await collect(*args, **kwargs)
            if result["kind"] == "surfaces":
                result = {**result, "surfaces": [{**item, "nativeWindow": False} for item in result["surfaces"]]}
            return result

        with patch.object(reader.directory, "collect", without_native_window):
            observed = await reader.read("Native fixture")
        self.assertIn(
            "Interaction: this view was found through accessibility, with no matched native window "
            "in this observation.",
            observed.text,
        )
        self.assertNotIn("Windows:", observed.text)  # A single entry still gets the explanation.
        self.assertIn("Tree: full", observed.text)
        self.assertIsNotNone(observed.image)  # The flag does not preempt independent capture.
        self.assertEqual(observed.selection.root.root_window, "root")
        self.assertEqual(observed.target.surface, "surface")
        self.assertTrue(observed.scope_confirmed)
        self.assertTrue(all(r["params"]["target"]["rootWindow"] == "root" for r in self.nodes()))
        self.assertFalse(any(r["method"] == "execute" for r in self.requests()))

    async def test_focus_note_requires_a_different_catalogued_window(self):
        for focused in ("second-root", None, "not-in-directory"):
            with self.subTest(focused=focused):
                reader = self.reader("observation-sdk-window-selector")
                collect = reader.directory.collect

                async def with_focus(*args, collect=collect, focused=focused, **kwargs):
                    result = await collect(*args, **kwargs)
                    return {**result, "focusedWindow": focused} if result["kind"] == "surfaces" else result

                with patch.object(reader.directory, "collect", with_focus):
                    observed = await reader.read("second-instance")
                self.assertNotIn("Interaction: the application reports focus", observed.text)
                self.assertEqual(observed.target.surface, "second-surface")

    async def test_native_window_hint_uses_the_final_confirmed_directory(self):
        reader = self.reader()
        observed = await reader.read("Native fixture")
        for before, after, confirmed, expected in (
            (False, True, True, False),
            (True, False, True, True),
            (False, False, False, False),
        ):
            with self.subTest(before=before, after=after, confirmed=confirmed):
                captured = {
                    **observed.directory,
                    "surfaces": [{**item, "nativeWindow": before} for item in observed.directory["surfaces"]],
                }
                final = {
                    **observed.directory,
                    "surfaces": [{**item, "nativeWindow": after} for item in observed.directory["surfaces"]],
                }
                text = reader._text(
                    observed.selection,
                    observed.target,
                    captured,
                    final,
                    observed.windows,
                    {"text": "0 window"},
                    None,
                    [],
                    confirmed,
                    None,
                )
                self.assertEqual("no matched native window" in text, expected)

    def test_changed_window_focus_context_cannot_look_like_an_unchanged_tree(self):
        from display_documents import fixture_renderer, with_document

        from computer_use.interaction.read.delivery.diff.formatting import snapshotDiff

        body = "\n0 window Document\n\t1 text " + "unchanged content " * 100
        note = "Interaction: the application reports focus in window 7; this view is window 0."

        def observation(context):
            return with_document(
                dict(
                    text="App: Fixture.\n" + context + "\nTree: full" + body,
                    refs={"0": "window-token", "1": "text-token"},
                )
            )

        with fixture_renderer():
            original = observation(note)
            for context in (note.replace("window 7;", "window 8;"), ""):
                current = observation(context)
                self.assertEqual(snapshotDiff(original, current), current["text"])
            self.assertIn(note, snapshotDiff(original, original))

    async def test_exact_window_choice_survives_failure_before_first_observation(self):
        reader = self.reader("observation-sdk-window-selector-failed-first")
        scopes = []
        with self.assertRaises(ComputerUseError):
            await reader.read("second-instance", on_scope=scopes.append)
        self.assertIsInstance(scopes[-1], SelectedApplicationWindow)
        self.assertEqual(scopes[-1].window, "second-root")
        with self.assertRaises(ComputerUseError) as caught:
            await reader.read(scopes[-1])
        self.assertEqual(caught.exception.code, "stale_target")
        self.assertFalse(self.nodes(), "Retry must not read the surviving sibling")

    async def test_app_name_with_multiple_windows_exposes_numeric_choices(self):
        reader = self.reader("observation-sdk-window-selector")
        with self.assertRaises(WindowSelectionRequired) as caught:
            await reader.read("Native fixture")
        self.assertEqual(set(reader.window_aliases(caught.exception.directory).values()), {"root", "second-root"})
        self.assertNotIn("second-root", caught.exception.detail)

    async def test_directory_tree_capture_and_recheck_share_call_and_exact_window(self):
        reader = self.reader()
        observation = await reader.read("Native fixture")
        calls = self.requests()
        self.assertEqual(
            [r["method"] for r in calls],
            [
                "hello",
                "begin",
                "inspect",
                "inspect",
                "finish",
                "begin",
                "inspect",
                "capture",
                "finish",
                "begin",
                "inspect",
                "finish",
            ],
        )
        self.assertTrue(all(r["params"]["mode"] == "read" for r in calls if r["method"] == "begin"))
        self.assertTrue(observation.scope_confirmed)
        self.assertFalse(observation.gaps)
        self.assertEqual(observation.selection.root.node, None)
        target = self.images()[0]["params"]["target"]
        self.assertEqual(self.nodes()[0]["params"]["target"], {**target, "node": "node"})
        self.assertEqual(observation.refs["0"], replace(observation.target, node="node"))
        self.assertEqual(observation.image.target.capture, "image-1")
        self.assertNotIn("Windows:", observation.text)
        self.assertIn("Tree: full", observation.text)
        self.assertIn("Native fixture", observation.text)
        # Explicit collectors still expose their independent capture intervals.
        self.assertEqual(observation.tree.facts["observed"]["id"], "facts")
        self.assertEqual(observation.image.observed["id"], "observation")

    async def test_isolated_read_failure_keeps_window_and_independent_pixels(self):
        reader = self.reader("observation-isolated")
        failure = ComputerUseError(
            dict(
                code="execution_failed",
                effect="none",
                target="tree",
                detail="read worker was stopped; old nodes expired",
                next="read again",
            )
        )
        with patch.object(reader.reader, "collect", side_effect=failure):
            observation = await reader.read("Native fixture")
        self.assertIsNone(observation.tree)
        self.assertIsNotNone(observation.image)
        self.assertIn("read worker was stopped", observation.text)
        begins = [r for r in self.requests() if r["method"] == "begin"]
        finishes = [r for r in self.requests() if r["method"] == "finish"]
        self.assertEqual(len(begins), len(finishes))
        self.assertTrue(all(r["params"]["mode"] == "read" for r in begins))
        self.assertEqual(observation.image.target.surface, observation.target.surface)
        restored = await reader.read(observation.selection)
        self.assertIsNotNone(restored.tree)
        self.assertEqual(restored.selection, observation.selection)

    async def test_window_coverage_refresh_has_one_attempt_and_keeps_other_parts(self):
        reader = self.reader()
        with (
            patch(
                ("computer_use.interaction.read.acquisition.passes.classify_read_attempt.window_content_unexposed"),
                return_value=True,
            ),
            patch(
                (
                    "computer_use.interaction.read.numbering.observation.passes.account_acquisiti"
                    "on_gaps.window_content_unexposed"
                ),
                return_value=True,
            ),
        ):
            observation = await reader.read("Native fixture")
        self.assertEqual(len(self.nodes()), 2)
        self.assertEqual(len(self.images()), 1)
        self.assertIn("does not establish that the window is empty or ready", observation.text)
        self.assertTrue(all(r["params"]["mode"] == "read" for r in self.requests() if r["method"] == "begin"))

    async def test_bound_reread_preserves_root_refs_and_prior_capture(self):
        reader = self.reader()
        first = await reader.read("Native fixture")
        second = await reader.read(first.selection)
        applications = [r for r in self.requests() if r["params"].get("query", {}).get("kind") == "applications"]
        self.assertEqual(len(applications), 1)
        self.assertEqual(first.selection, second.selection)
        self.assertEqual(first.refs, second.refs)
        self.assertEqual(first.image.target.capture, "image-1")
        self.assertEqual(second.image.target.capture, "image-2")
        with self.assertRaises(TypeError):
            first.refs["0"] = second.target

    async def test_capture_geometry_change_recollects_once_after_scope_confirmation(self):
        reader = self.reader()
        collect = reader.images.collect
        attempted = []

        async def changing_capture(read, **options):
            attempted.append(read)
            if len(attempted) == 1:
                raise ComputerUseError(
                    dict(
                        code="stale_target",
                        effect="none",
                        target="image",
                        detail="window geometry changed during capture",
                        next="read again",
                    )
                )
            return await collect(read, **options)

        with patch.object(reader.images, "collect", side_effect=changing_capture):
            observation = await reader.read("Native fixture")
        self.assertEqual(len(attempted), 2)
        self.assertEqual(len(self.nodes()), 2)
        self.assertIsNotNone(observation.image)
        self.assertTrue(observation.scope_confirmed)
        self.assertFalse(observation.gaps)
        self.assertEqual(observation.image.target.surface, observation.target.surface)
        requests = self.requests()
        self.assertEqual(sum(r["method"] == "begin" for r in requests), sum(r["method"] == "finish" for r in requests))
        self.assertTrue(all(r["params"]["mode"] == "read" for r in requests if r["method"] == "begin"))
        self.assertEqual(sum(r["params"].get("query", {}).get("kind") == "surfaces" for r in requests), 3)

    async def test_persistent_capture_churn_remains_a_gap_without_extra_attempts(self):
        reader = self.reader()
        error = ComputerUseError(
            dict(
                code="stale_target",
                effect="none",
                target="image",
                detail="window geometry keeps changing",
                next="read again",
            )
        )
        with patch.object(reader.images, "collect", side_effect=error) as capture:
            observation = await reader.read("Native fixture")
        self.assertEqual(capture.call_count, 2)
        self.assertEqual(len(self.nodes()), 2)
        self.assertIsNone(observation.image)
        self.assertIsNotNone(observation.tree)
        self.assertIn("window geometry keeps changing", observation.text)

    async def test_stale_capture_does_not_retry_unconfirmed_or_closed_window(self):
        error = ComputerUseError(
            dict(
                code="stale_target", effect="none", target="image", detail="window geometry changed", next="read again"
            )
        )
        reader = self.reader("observation-partial-modal")
        with patch.object(reader.images, "collect", side_effect=error) as capture:
            observation = await reader.read("Native fixture")
        self.assertEqual(capture.call_count, 1)
        self.assertFalse(observation.scope_confirmed)
        reader = self.reader("observation-root-gone")
        with patch.object(reader.images, "collect", side_effect=error) as capture:
            with self.assertRaises(ComputerUseError) as caught:
                await reader.read("Native fixture")
        self.assertEqual(caught.exception.code, "stale_target")
        self.assertEqual(capture.call_count, 1)

    async def test_loading_refresh_uses_one_wait_and_one_fresh_tree(self):
        reader = self.reader()
        with (
            patch(
                ("computer_use.interaction.read.acquisition.passes.classify_read_attempt.window_content_unexposed"),
                return_value=True,
            ),
            patch(
                (
                    "computer_use.interaction.read.numbering.observation.passes.account_acquisiti"
                    "on_gaps.window_content_unexposed"
                ),
                return_value=True,
            ),
            patch.object(reader.reader, "wait_for_change", new_callable=AsyncMock, return_value="event") as wait,
        ):
            await reader.read("Native fixture")
        wait.assert_awaited_once()
        self.assertEqual(len(self.nodes()), 2)
        self.assertEqual(len(self.images()), 1)

    async def test_loading_signal_does_not_retry_known_incomplete_tree(self):
        reader = self.reader()
        with (
            patch(
                ("computer_use.interaction.read.acquisition.passes.classify_read_attempt.window_content_unexposed"),
                return_value=True,
            ),
            patch(
                (
                    "computer_use.interaction.read.numbering.observation.passes.account_acquisiti"
                    "on_gaps.window_content_unexposed"
                ),
                return_value=True,
            ),
            patch(
                "computer_use.interaction.read.acquisition.passes.classify_read_attempt.refreshable", return_value=False
            ),
        ):
            await reader.read("Native fixture")
        self.assertEqual(len(self.nodes()), 1)
        self.assertEqual(len(self.images()), 1)

    async def test_failed_wait_keeps_capture_without_retrying_expired_nodes(self):
        reader = self.reader()
        error = ComputerUseError(
            dict(
                code="stale_target",
                effect="none",
                target="wait",
                detail="The watched window or worker no longer exists.",
                next="read the selected window again",
            )
        )
        with (
            patch(
                ("computer_use.interaction.read.acquisition.passes.classify_read_attempt.window_content_unexposed"),
                return_value=True,
            ),
            patch(
                (
                    "computer_use.interaction.read.numbering.observation.passes.account_acquisiti"
                    "on_gaps.window_content_unexposed"
                ),
                return_value=True,
            ),
            patch.object(reader.reader, "wait_for_change", new_callable=AsyncMock, side_effect=error),
        ):
            observed = await reader.read("Native fixture")
        self.assertIsNotNone(observed.tree)
        self.assertIsNotNone(observed.image)
        self.assertFalse(observed.scope_confirmed)
        self.assertEqual(len(self.nodes()), 1)
        self.assertEqual(len(self.images()), 1)
        self.assertTrue(any(gap.component == "wait" and gap.code == "stale_target" for gap in observed.gaps))

    async def test_exact_application_keeps_partial_discovery_evidence(self):
        observation = await self.reader("observation-partial-applications").read("running:app")
        self.assertEqual(observation.applications["stopReasons"], ["application discovery incomplete"])
        self.assertTrue(any(gap.component == "applications" for gap in observation.gaps))
        self.assertIn("application discovery incomplete", observation.text)

    async def test_no_ax_still_returns_capture_and_explicit_gap(self):
        observation = await self.reader("capture").read("Native fixture", window="root")
        self.assertIsNone(observation.tree)
        self.assertIsNotNone(observation.image)
        self.assertFalse(self.nodes())
        self.assertIn("Tree: unavailable", observation.text)
        self.assertFalse(observation.scope_confirmed)
        self.assertEqual({gap.component for gap in observation.gaps}, {"tree", "directory"})

    async def test_local_provider_and_image_errors_are_independent(self):
        for mode, has_tree, has_image in [
            ("observation-tree-error", False, True),
            ("observation-image-error", True, False),
            ("observation-both-error", False, False),
        ]:
            with self.subTest(mode=mode):
                observation = await self.reader(mode).read("Native fixture")
                self.assertEqual(observation.tree is not None, has_tree)
                self.assertEqual(observation.image is not None, has_image)
                self.assertEqual(len(observation.gaps), int(not has_tree) + int(not has_image))
                self.assertTrue(observation.scope_confirmed)
                if not has_tree:
                    self.assertFalse(observation.refs)
                    self.assertNotIn("Tree: full", observation.text)

    async def test_modal_change_discards_both_old_components_and_collects_once(self):
        observation = await self.reader("observation-modal").read("Native fixture")
        self.assertEqual(observation.selection.root.root_window, "root")
        self.assertEqual(observation.target.surface, "dialog-surface-2")
        self.assertEqual([r["params"]["target"]["node"] for r in self.nodes()], ["node", "dialog-node-2"])
        self.assertEqual(observation.image.target.surface, observation.target.surface)
        self.assertEqual(observation.image.target.capture, "image-2")
        self.assertEqual(list(observation.refs.values())[0].node, "dialog-node-2")
        self.assertIn('1 "Dialog 2" (current, modal, parent=0)', observation.text)
        self.assertTrue(observation.scope_confirmed)

    async def test_continued_scope_changes_do_not_restart_budget_or_mix_components(self):
        observation = await self.reader("observation-churn").read("Native fixture")
        self.assertEqual(len(self.nodes()), 2)
        self.assertEqual(
            [r["params"]["target"]["surface"] for r in self.images()], ["surface", "dialog-surface-2", "surface"]
        )
        self.assertEqual(observation.target.surface, "dialog-surface-2")
        self.assertEqual(observation.image.target.surface, "dialog-surface-2")
        self.assertEqual(observation.tree.collection.surface, "dialog-surface-2")
        self.assertFalse(observation.scope_confirmed)
        self.assertEqual(observation.source_directory["surfaces"][-1]["title"], "Dialog 2")
        self.assertEqual(observation.directory["surfaces"][-1]["title"], "Dialog 3")
        self.assertIn("Current view is not confirmed", observation.text)
        self.assertIn("Dialog 3", observation.text)
        self.assertIn("Window catalogue changed after these thumbnails", observation.text)
        self.assertEqual([s["window"] for s in observation.overview.sources], ["dialog-2", "root"])

    async def test_partial_catalog_does_not_prove_modal_disappeared(self):
        observation = await self.reader("observation-partial-modal").read("Native fixture")
        self.assertEqual(len(self.nodes()), 1)
        self.assertEqual(observation.target.surface, "dialog-surface-2")
        self.assertFalse(observation.scope_confirmed)
        self.assertIn("provider timeout", observation.text)

    async def test_confirmed_modal_close_returns_to_same_root_after_one_recollection(self):
        observation = await self.reader("observation-modal-closed").read("Native fixture")
        self.assertEqual([r["params"]["target"]["node"] for r in self.nodes()], ["dialog-node-2", "node"])
        self.assertEqual(observation.target, replace(observation.selection.root, node="node"))
        self.assertEqual(observation.image.target.surface, "surface")
        self.assertTrue(observation.scope_confirmed)

    async def test_multiple_modals_are_reported_without_guessing_one(self):
        observation = await self.reader("observation-multiple-modal").read("Native fixture")
        self.assertEqual(len(self.nodes()), 1)
        self.assertEqual(len(self.images()), 1)
        self.assertFalse(observation.scope_confirmed)
        self.assertTrue(any(gap.code == "ambiguous_target" for gap in observation.gaps))
        self.assertIn("Dialog 2", observation.text)
        self.assertIn("Other dialog", observation.text)

    async def test_missing_or_replaced_root_never_retargets_other_document(self):
        for mode, code in [
            ("observation-root-gone", "stale_target"),
            ("observation-root-reused", "stale_target"),
            ("observation-partial-gone", "observation_incomplete"),
        ]:
            with self.subTest(mode=mode):
                with self.assertRaises(ComputerUseError) as caught:
                    await self.reader(mode).read("Native fixture")
                self.assertEqual(caught.exception.code, code)
        self.assertTrue(all(r["params"]["target"]["surface"] == "surface" for r in self.nodes()))

    async def test_read_can_explicitly_disallow_launch(self):
        with self.assertRaisesRegex(ComputerUseError, "launch is disabled"):
            await self.reader("observation-installed").read("Native fixture", allow_launch=False)
        self.assertFalse(self.nodes())
        self.assertFalse(self.images())

    async def test_window_choices_recheck_identity_after_overview_capture(self):
        reader = self.reader("observation-sdk-overview-recreated")
        with self.assertRaises(WindowSelectionRequired) as caught:
            await reader.read("Native fixture")
        choices = caught.exception
        self.assertEqual([s["window"] for s in choices.directory["surfaces"]], ["root", "new-auxiliary"])
        self.assertIn("Window catalogue changed after these thumbnails", choices.overview.text)
        self.assertEqual([s["window"] for s in choices.overview.sources], ["root", "second-root"])
        self.assertEqual(len(self.images()), 2, "Catalogue refresh must not start another capture loop")
        # Selecting the published current identity works; a stale old choice
        # does not silently redirect to the replacement auxiliary object.
        observed = await reader.read("Native fixture", window="new-auxiliary")
        self.assertEqual(observed.selection.root.root_window, "new-auxiliary")
        with self.assertRaisesRegex(ComputerUseError, "fixed working window is missing"):
            await reader.read("Native fixture", window="second-root")

    async def test_protocol_scope_violation_stops_without_capture(self):
        with self.assertRaisesRegex(ValueError, "scope"):
            await self.reader("observation-wrong-scope").read("Native fixture")
        self.assertFalse(self.images())

    async def test_disconnect_and_uncertain_failure_never_become_local_gaps(self):
        for mode in ("observation-disconnect", "observation-uncertain"):
            with self.subTest(mode=mode):
                with self.assertRaises(ComputerUseError):
                    await self.reader(mode).read("Native fixture")
        self.assertFalse(self.images())

    async def test_uncertain_cleanup_prevents_scope_recheck_or_future_operations(self):
        reader = self.reader("observation-cleanup-unknown")
        with self.assertRaisesRegex(RuntimeError, "cleanup"):
            await reader.read("Native fixture")
        self.assertTrue(reader.port.calls.retired)
        self.assertEqual(sum(r["method"] == "begin" for r in self.requests()), 2)
        with self.assertRaisesRegex(RuntimeError, "cleanup"):
            await reader.read("Native fixture")

    async def test_cancellation_ends_active_stage_and_never_becomes_an_image_gap(self):
        reader = self.reader()
        with patch.object(reader.images, "collect", side_effect=asyncio.CancelledError()):
            with self.assertRaises(asyncio.CancelledError):
                await reader.read("Native fixture")
        self.assertFalse(reader.port.calls.busy)
        self.assertFalse(reader.port.calls.retired)
        self.assertEqual(sum(r["method"] == "begin" for r in self.requests()), 2)
        self.assertEqual(self.requests()[-1]["method"], "finish")

    async def test_claim_excludes_input_between_native_stages_without_marking_possible_effect(self):
        reader = self.reader()
        selection = (await reader.read("Native fixture")).selection
        offset = len(self.requests())
        with reader.port.calls.claim(1000):
            with self.assertRaises(ComputerUseError) as caught:
                await reader.port.begin(Intent("key", dict(key="Return")), selection.root, 1000)
        self.assertEqual(caught.exception.effect, "none")
        self.assertEqual(len(self.requests()), offset)

    async def test_projection_overall_deadline_is_checked_before_publication(self):
        reader = self.reader()
        # Actual transport remains real; only the public read clock advances.
        now = [0.0]
        original = PrepareSnapshotPipeline.run

        def slow_render(pipeline, request, control=None):
            now[0] = 2
            return original(pipeline, request, control)

        with (
            patch("computer_use.native.lifecycle.time", SimpleNamespace(monotonic=lambda: now[0])),
            patch.object(PrepareSnapshotPipeline, "run", slow_render),
        ):
            with self.assertRaises(TimeoutError):
                await reader.read("Native fixture", timeout_ms=1000)
        self.assertFalse(reader.port.calls.retired)


if __name__ == "__main__":
    unittest.main()
