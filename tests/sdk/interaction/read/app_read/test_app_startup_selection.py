"""Launch receipts and working-window selection are distinct SDK phases."""

import asyncio
import unittest
from unittest.mock import patch

import integration.sdk.test_native_client as fixture

from computer_use.errors import ComputerUseError
from computer_use.native.scopes import StartingApplication


class AppStartupSelectionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.NativeClientTests.asyncSetUp
    asyncTearDown = fixture.NativeClientTests.asyncTearDown

    def computer(self, mode="observation-sdk"):
        from dataclasses import replace

        from v5_handles import App, computerForController

        current = fixture.NativeClientTests.computer(self, mode)
        controller = current._controller
        controller.adapters = replace(controller.adapters, create_app=lambda c, scope: App(c, scope, _internal=True))
        return computerForController(controller)

    requests = fixture.NativeClientTests.requests
    writes = fixture.NativeClientTests.writes

    def applications(self):
        return [r for r in self.requests() if r["params"].get("query", {}).get("kind") == "applications"]

    def surfaces(self):
        return [r for r in self.requests() if r["params"].get("query", {}).get("kind") == "surfaces"]

    async def test_declared_window_owner_is_selected_without_merging_the_launch_process(self):
        computer = self.computer("observation-launch-wrapper")
        app = computer.app("Native fixture")
        observed = await app.read()
        state = computer._controller.state(app)
        self.assertEqual(state.selection.application, "running:child")
        self.assertEqual(state.name, "Window owner")
        self.assertEqual(state.observation.launch["application"]["id"], "running:launched")
        self.assertIn("Window owner", observed.text)
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(len(self.applications()), 2)
        self.assertTrue(all(r["params"]["query"]["application"] == "running:child" for r in self.surfaces()))
        original = state.selection
        await app.read()
        self.assertEqual(state.selection, original)
        self.assertEqual(len(self.applications()), 2)

    async def test_empty_wrapper_directory_waits_for_the_declared_owner(self):
        computer = self.computer("observation-launch-wrapper-delayed")
        app = computer.app("Native fixture")
        await app.read()
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(len(self.applications()), 3)
        self.assertEqual(self.surfaces()[0]["params"]["query"]["application"], "running:launched")
        self.assertEqual(computer._controller.state(app).selection.application, "running:child")

    async def test_confirmed_wrapper_exit_does_not_prevent_observing_its_declared_successor(self):
        computer = self.computer("observation-launch-wrapper-exited")
        app = computer.app("Native fixture")
        await app.read()
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(len(self.applications()), 3)
        self.assertEqual(computer._controller.state(app).selection.application, "running:child")

    async def test_failed_wait_retains_starting_scope_and_next_read_does_not_launch(self):
        computer = self.computer("observation-launch-wrapper-retry")
        app = computer.app("Native fixture")
        with self.assertRaises(ComputerUseError) as first:
            await app.read()
        self.assertEqual(first.exception.effect, "possible")
        self.assertIsInstance(computer._controller.state(app).running, StartingApplication)
        self.assertEqual(len(self.applications()), 6)
        await app.read()
        state = computer._controller.state(app)
        self.assertEqual(state.selection.application, "running:child")
        self.assertNotIsInstance(state.running, StartingApplication)
        self.assertIsNone(state.observation.launch)  # This call only observed.
        self.assertEqual(len(self.writes()), 1)

    async def test_conflicting_owners_are_not_guessed_or_hidden_by_polling(self):
        computer = self.computer("observation-launch-wrapper-conflict")
        app = computer.app("Native fixture")
        for effect in ("possible", "none"):
            with self.assertRaises(ComputerUseError) as caught:
                await app.read()
            self.assertEqual((caught.exception.code, caught.exception.effect), ("ambiguous_target", effect))
            self.assertIsNone(computer._controller.state(app).selection)
        self.assertEqual(len(self.applications()), 3)
        self.assertFalse(self.surfaces())
        self.assertEqual(len(self.writes()), 1)

    async def test_partial_directory_does_not_prove_the_only_owner(self):
        computer = self.computer("observation-launch-wrapper-partial")
        app = computer.app("Native fixture")
        with self.assertRaises(ComputerUseError) as caught:
            await app.read()
        self.assertEqual((caught.exception.code, caught.exception.effect), ("observation_incomplete", "possible"))
        self.assertEqual(len(self.applications()), 2)
        self.assertFalse(self.surfaces())
        self.assertEqual(len(self.writes()), 1)

    async def test_removed_recipe_is_not_replaced_by_its_name_or_window_class(self):
        app = self.computer("observation-launch-wrapper-removed").app("Native fixture")
        with self.assertRaises(ComputerUseError) as caught:
            await app.read()
        self.assertEqual((caught.exception.code, caught.exception.effect), ("element_not_found", "possible"))
        self.assertEqual(len(self.applications()), 2)
        self.assertFalse(self.surfaces())
        self.assertEqual(len(self.writes()), 1)

    async def test_selected_root_disappearing_never_returns_to_startup_matching(self):
        computer = self.computer("observation-launch-wrapper-root-gone")
        app = computer.app("Native fixture")
        for effect in ("possible", "none"):
            with self.assertRaises(ComputerUseError) as caught:
                await app.read()
            self.assertEqual((caught.exception.code, caught.exception.effect), ("stale_target", effect))
            self.assertEqual(computer._controller.state(app).selection.root.root_window, "root")
        self.assertEqual(len(self.applications()), 2)
        self.assertEqual(len(self.writes()), 1)

    async def test_cancelled_startup_observation_preserves_the_no_relaunch_phase(self):
        computer = self.computer("observation-launch-wrapper")
        app = computer.app("Native fixture")
        with (
            patch.object(computer._controller.reader, "_startup_directory", side_effect=asyncio.CancelledError()),
            self.assertRaises(asyncio.CancelledError),
        ):
            await app.read()
        self.assertIsInstance(computer._controller.state(app).running, StartingApplication)
        await app.read()
        self.assertEqual(computer._controller.state(app).selection.application, "running:child")
        self.assertEqual(len(self.writes()), 1)

    async def test_starting_scope_cannot_outlive_its_driver_session(self):
        computer = self.computer("observation-launch-wrapper-partial")
        app = computer.app("Native fixture")
        with self.assertRaises(ComputerUseError):
            await app.read()
        connection = computer._controller.transport
        connection.close()
        await connection.waitClosed()
        previous = self.requests()
        with self.assertRaises(ComputerUseError) as caught:
            await app.read()
        self.assertEqual((caught.exception.code, caught.exception.effect), ("stale_target", "none"))
        self.assertEqual(self.requests(), previous)


if __name__ == "__main__":
    unittest.main()
