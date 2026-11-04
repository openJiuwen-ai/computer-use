# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A completed input remains completed when its bounded read suffix fails."""

import asyncio
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from computer_use.client.factory import session_controller
from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.components.support import ObservationGap
from computer_use.interaction.read.views.related import RelatedRead
from computer_use.interaction.requests import ExecuteAction
from computer_use.interaction.write.request.model import ObservedAction
from computer_use.interaction.write.request.observation import WriteWithObservation
from computer_use.native.contracts import Binding, Intent
from computer_use.native.lifecycle import CallLifecycle
from computer_use.native.scopes import FixedWindowInvalidated, FixedWindowUnconfirmed, RunningApplication
from computer_use.session.events import set_action_handler
from computer_use.session.observation import CapturedTextDocument, lazy_observation
from computer_use.session.observation_data import retain_observation_data
from computer_use.session.results import result_receipt


class UI(SimpleNamespace):
    pass


class PostwriteObservationTests(IsolatedAsyncioTestCase):
    def setUp(self):
        port = SimpleNamespace(
            connection=SimpleNamespace(
                session="test", capabilities=(), session_identity=(1, "test"), retain_session=lambda _: nullcontext()
            )
        )
        port.calls = CallLifecycle(port)
        self.controller = session_controller(port)
        self.target = Binding("test", "app", "root", "surface", "node")
        self.ui = UI(_instance=SimpleNamespace(selector="fixture", anchor=SimpleNamespace(name="Fixture")), _image=None)
        self.controller.register(self.ui, dict(selector="fixture"))
        self.controller.state(self.ui).running = RunningApplication("fixture", "Fixture", "test")
        self.receipt = dict(status="dispatched", target="original field", detail="Input sent once.")
        self.execute = AsyncMock(return_value=self.receipt)
        self.controller.engine.execute = self.execute
        self.observation = lazy_observation(CapturedTextDocument("New original window state"))
        self.directory = self.window_directory("root", "surface", "Original Window")
        retain_observation_data(self.observation, (), (), directory=self.directory)
        self.read = AsyncMock(return_value=self.observation)
        self.controller.interfaces.read = self.read
        self.controller.interfaces.read_desktop = AsyncMock(return_value=self.observation)
        self.events = []
        set_action_handler(lambda receipt: self.events.append(receipt))

    def tearDown(self):
        set_action_handler(None)

    def window_directory(self, window, surface, title):
        return dict(
            kind="surfaces",
            session="test",
            application="fixture",
            platform="macos",
            observed=dict(id="postwrite-read", startedAt=1, completedAt=2),
            provider=dict(status="notRequested", attribute=None, writeStatus=None),
            focusedWindow=window,
            rootDiscoveryComplete=True,
            stopReasons=[],
            surfaces=[dict(
                window=window, surface=surface, instance="app", node="node:" + window, title=title,
                kind="window", nativeWindow=True, accessibility=True, modal=False, parent=None, parentState="none",
            )],
        )

    async def run_action(self, *, owner=True, target=None):
        return await self.controller.run(
            ExecuteAction(self.ui if owner else None, Intent("click", {}), target or self.target)
        )

    async def test_same_owner_read_is_after_authoritative_receipt_and_budgeted(self):
        async def read(owner, view, full, menu_path):
            self.assertEqual(self.events, [self.receipt])
            self.assertIs(owner, self.ui)
            self.assertEqual((view, full, menu_path), ("tree", False, None))
            with self.controller.port.calls.claim(30000) as remaining:
                self.assertLessEqual(remaining(), 5000)
            return self.observation

        self.read.side_effect = read
        result = await self.run_action()
        self.assertIs(result_receipt(result), self.receipt)
        self.assertIs(result.observation, self.observation)
        self.assertEqual(result.observation_status, "available")
        self.assertEqual(result.window_lifecycle, "observed")
        self.assertEqual(result.status, "dispatched")
        self.execute.assert_awaited_once()
        self.read.assert_awaited_once()
        self.assertIsNone(self.controller.port.calls._limit)

    async def test_partial_observation_is_not_reported_complete(self):
        retain_observation_data(self.observation, (), (), partial=True, directory=self.directory)
        result = await self.run_action()
        self.assertEqual(result.observation_status, "partial")
        self.assertIs(result.observation, self.observation)
        self.assertEqual(result.window_lifecycle, "observed")

    async def test_failed_optional_refresh_keeps_first_content_and_reports_its_gap(self):
        gap = ObservationGap("refresh", "observation_refresh_failed", "first capture retained")
        retain_observation_data(self.observation, (), (), partial=True, directory=self.directory, gaps=(gap,))
        result = await self.run_action()
        self.assertIs(result.observation, self.observation)
        self.assertEqual(result.observation_status, "partial")
        self.assertEqual(result.observation_error.code, gap.code)
        self.assertEqual(result.status, "dispatched")
        self.execute.assert_awaited_once()

    async def test_available_text_without_original_window_directory_keeps_lifecycle_unknown(self):
        retain_observation_data(self.observation, (), ())
        result = await self.run_action()
        self.assertEqual(result.observation_status, "available")
        self.assertEqual(result.window_lifecycle, "unknown")
        self.execute.assert_awaited_once()

    async def test_scope_metadata_without_requested_pixels_is_unavailable(self):
        retain_observation_data(
            self.observation,
            (),
            (),
            partial=True,
            content_available=False,
            gaps=(ObservationGap("image", "stale_target", "Window disappeared during capture"),),
        )
        result = await self.run_action(target=replace(self.target, node=None, capture="capture"))
        self.assertEqual(result.observation_status, "unavailable")
        self.assertEqual(result.window_lifecycle, "unknown")
        self.assertEqual(result.observation_error.code, "stale_target")
        self.assertIn("disappeared", result.observation_error.detail)
        self.assertIs(result.observation, self.observation)
        self.execute.assert_awaited_once()

    async def test_image_input_keeps_visual_and_accessibility_observation_available(self):
        await self.run_action(target=replace(self.target, node=None, capture="capture"))
        self.assertEqual(self.read.call_args.args[1], "both")
        self.assertEqual(self.read.call_args.kwargs, {})  # default read never activates

    async def test_window_key_does_not_repeat_cached_image_when_tree_body_is_available(self):
        self.ui._image = object()
        request = ExecuteAction(self.ui, Intent("key", {}), replace(self.target, node=None))
        related = RelatedRead(self.target, (), before_tree=object())
        suffix = WriteWithObservation(self.controller, SimpleNamespace(contract=SimpleNamespace(effects=frozenset())))
        with patch("computer_use.interaction.write.request.observation.window_content_unexposed", return_value=False):
            await suffix.observe(request, ObservedAction(self.receipt), "dispatched", (1, "test"), related)
        self.assertEqual(self.read.call_args.args[1], "tree")

    async def test_window_key_keeps_pixels_when_prior_tree_is_only_a_shell(self):
        self.ui._image = object()
        request = ExecuteAction(self.ui, Intent("key", {}), replace(self.target, node=None))
        related = RelatedRead(self.target, (), before_tree=object())
        suffix = WriteWithObservation(self.controller, SimpleNamespace(contract=SimpleNamespace(effects=frozenset())))
        with patch("computer_use.interaction.write.request.observation.window_content_unexposed", return_value=True):
            await suffix.observe(request, ObservedAction(self.receipt), "dispatched", (1, "test"), related)
        self.assertEqual(self.read.call_args.args[1], "both")

    async def test_desktop_input_keeps_desktop_observation(self):
        await self.run_action(owner=False, target=Binding("test", None, None, None, capture="image"))
        self.controller.interfaces.read_desktop.assert_awaited_once_with("image", False)
        self.read.assert_not_awaited()

    async def test_read_timeout_does_not_erase_or_repeat_write(self):
        self.read.side_effect = TimeoutError("read budget exhausted")
        result = await self.run_action()
        self.assertEqual(result.status, "dispatched")
        self.assertEqual(result.detail, self.receipt["detail"])
        self.assertEqual(result.observation_status, "unavailable")
        self.assertEqual(result.observation_error.code, "observation_timeout")
        self.assertTrue(result.observation_error.session_usable)
        self.assertEqual(result.observation_error.detail, "read budget exhausted")
        self.assertNotIn("next", result.observation_error)
        self.execute.assert_awaited_once()

    async def test_closed_root_offers_exact_new_choices_without_rebinding(self):
        directory = self.window_directory("new-root", "new-surface", "New Window")
        self.read.side_effect = FixedWindowInvalidated(self.target, "Original window closed", directory=directory)
        anchor = self.ui._instance.anchor
        result = await self.run_action()
        self.assertEqual(result.window_lifecycle, "closed")
        self.assertEqual(result.observation_error.code, "window_closed")
        self.assertEqual([dict(choice) for choice in result.choices], [dict(id=0, title="New Window")])
        self.assertIs(self.ui._instance.anchor, anchor)
        entry = self.controller.interfaces.windows.resolve(result.choices[0].id, "test")
        self.assertIs(entry.directory, directory)
        self.assertEqual((entry.name, entry.identity.window), ("Fixture", "new-root"))
        self.execute.assert_awaited_once()

    async def test_unknown_window_is_not_reported_closed(self):
        self.read.side_effect = FixedWindowUnconfirmed(self.target)
        result = await self.run_action()
        self.assertEqual(result.window_lifecycle, "unknown")
        self.assertEqual(result.observation_error.code, "observation_incomplete")

    async def test_caller_cancellation_propagates_with_receipt_evidence(self):
        cancelled = asyncio.CancelledError()
        self.read.side_effect = cancelled
        with self.assertRaises(asyncio.CancelledError) as caught:
            await self.run_action()
        self.assertIs(caught.exception, cancelled)
        self.assertIn("Action already dispatched: Input sent once.", cancelled.__notes__)
        self.assertNotIn("do not replay", " ".join(cancelled.__notes__))
        self.assertEqual(self.events, [self.receipt])
        self.execute.assert_awaited_once()

    async def test_failed_write_does_not_start_optional_read(self):
        self.execute.side_effect = ComputerUseError(
            dict(code="input_unavailable", target="original", effect="none", detail="not dispatched", next="observe")
        )
        with self.assertRaises(ComputerUseError):
            await self.run_action()
        self.read.assert_not_awaited()
        self.execute.assert_awaited_once()

    async def test_uncertain_write_retains_same_error_effect_context_and_cause_after_read(self):
        for effect in ("partial", "possible"):
            with self.subTest(effect=effect):
                original_cause = ValueError("native incomplete delivery")
                failure = ComputerUseError(
                    dict(
                        code="execution_unknown", target="original", effect=effect, detail="uncertain", next="observe"
                    ),
                    cause=original_cause,
                )
                context, impact = object(), {"attempts": ()}
                failure._failure_context, failure.impact = context, impact
                self.execute.side_effect = failure
                self.execute.reset_mock()
                self.read.reset_mock()
                with self.assertRaises(ComputerUseError) as caught:
                    await self.run_action()
                self.assertIs(caught.exception, failure)
                self.assertEqual(
                    (failure.code, failure.effect, failure.detail),
                    ("execution_unknown", effect, "uncertain"),
                )
                self.assertIs(failure._failure_context, context)
                self.assertIs(failure.impact, impact)
                self.assertIs(failure.__cause__, original_cause)
                self.assertIs(failure.observation, self.observation)
                self.assertEqual(failure.observation_status, "available")
                self.execute.assert_awaited_once()
                self.read.assert_awaited_once()
                self.assertEqual(self.events, [])  # no fabricated success receipt

    async def test_uncertain_write_and_failed_read_keep_two_independent_failures(self):
        failure = ComputerUseError(
            dict(
                code="execution_unknown", target="original", effect="partial", detail="body partly sent", next="observe"
            )
        )
        self.execute.side_effect = failure
        self.read.side_effect = TimeoutError("read deadline")
        with self.assertRaises(ComputerUseError) as caught:
            await self.run_action()
        self.assertIs(caught.exception, failure)
        self.assertEqual(failure.effect, "partial")
        self.assertEqual(failure.detail, "body partly sent")
        self.assertEqual(failure.observation_error.code, "observation_timeout")
        self.assertEqual(failure.observation_status, "unavailable")
        self.execute.assert_awaited_once()

    async def test_uncertain_write_does_not_reconnect_a_retired_or_changed_session(self):
        for retired, session in ((True, "test"), (False, "different")):
            with self.subTest(retired=retired, session=session):
                failure = ComputerUseError(
                    dict(code="execution_unknown", target="original", effect="possible", detail="lost", next="observe")
                )

                async def execute(*args, **kwargs):
                    self.controller.port.calls.retired = retired
                    self.controller.transport.session = session
                    raise failure

                self.controller.port.calls.retired = False
                self.controller.transport.session = "test"
                self.execute.side_effect = execute
                self.read.reset_mock()
                with self.assertRaises(ComputerUseError) as caught:
                    await self.run_action()
                self.assertIs(caught.exception, failure)
                self.assertEqual(failure.observation_status, "unavailable")
                self.assertFalse(failure.observation_error.session_usable)
                self.assertEqual(failure.observation_error.code, "session_unavailable")
                self.read.assert_not_awaited()

    async def test_same_wire_session_from_another_generation_is_not_a_related_observation(self):
        async def execute(*_args, **_kwargs):
            self.controller.transport.session_identity = (2, "test")
            return self.receipt

        self.execute.side_effect = execute
        result = await self.run_action()
        self.assertIs(result_receipt(result), self.receipt)
        self.assertEqual(result.observation_status, "unavailable")
        self.assertEqual(result.observation_error.code, "session_unavailable")
        self.assertFalse(result.observation_error.session_usable)
        self.read.assert_not_awaited()

    async def test_cancellation_during_error_suffix_keeps_original_error_as_context(self):
        failure = ComputerUseError(
            dict(code="execution_unknown", target="original", effect="possible", detail="uncertain", next="observe")
        )
        self.execute.side_effect = failure
        cancelled = asyncio.CancelledError()
        self.read.side_effect = cancelled
        with self.assertRaises(asyncio.CancelledError) as caught:
            await self.run_action()
        self.assertIs(caught.exception, cancelled)
        self.assertIs(cancelled.__context__, failure)
        self.assertIn("effect remains possible", " ".join(cancelled.__notes__))
        self.execute.assert_awaited_once()

    async def test_uncertain_closed_root_preserves_error_and_current_choices(self):
        failure = ComputerUseError(
            dict(code="execution_unknown", target="original", effect="possible", detail="uncertain", next="observe")
        )
        self.execute.side_effect = failure
        directory = self.window_directory("new-root", "new-surface", "New Window")
        self.read.side_effect = FixedWindowInvalidated(self.target, "Original window closed", directory=directory)
        anchor = self.ui._instance.anchor
        with self.assertRaises(ComputerUseError) as caught:
            await self.run_action()
        self.assertIs(caught.exception, failure)
        self.assertEqual(failure.effect, "possible")
        self.assertEqual(failure.window_lifecycle, "closed")
        self.assertEqual([dict(choice) for choice in failure.choices], [dict(id=0, title="New Window")])
        self.assertIs(self.controller.interfaces.windows.resolve(failure.choices[0].id, "test").directory, directory)
        self.assertIs(self.ui._instance.anchor, anchor)

    async def test_unanchored_internal_owner_is_not_launched_by_suffix(self):
        self.ui._instance.anchor = None
        result = await self.run_action()
        self.assertEqual(result.observation_status, "not_requested")
        self.read.assert_not_awaited()


class ObservationBudgetTests(IsolatedAsyncioTestCase):
    async def test_successive_claims_share_deadline_and_expiry_does_not_retire(self):
        lifecycle = CallLifecycle(SimpleNamespace())
        clock = [10.0]
        with patch("computer_use.native.lifecycle.time.monotonic", side_effect=lambda: clock[0]):
            with lifecycle.limit(5000):
                with lifecycle.claim(30000) as remaining:
                    self.assertEqual(remaining(), 5000)
                clock[0] = 13.0
                with lifecycle.claim(30000) as remaining:
                    self.assertEqual(remaining(), 2000)
                clock[0] = 15.1
                with lifecycle.claim(30000) as remaining:
                    with self.assertRaises(TimeoutError):
                        remaining()
            with lifecycle.claim(30000) as remaining:
                self.assertEqual(remaining(), 30000)
        self.assertFalse(lifecycle.retired)
        self.assertFalse(lifecycle.busy)
