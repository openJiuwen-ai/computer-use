"""V6 public API through the unchanged production v5 JSONL control pipeline."""

import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from repository import ROOT as REPO_ROOT

from computer_use import UI, App, ClickOption, Computer, Element, Window
from computer_use.client import images
from computer_use.errors import ComputerUseError
from computer_use.interaction.controller import SessionController as NativeController
from computer_use.native.connection import Connection
from computer_use.session import images as images_state

ROOT = REPO_ROOT


class NativeClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="native-client-")
        self.journal = Path(self.temp.name) / "wire.jsonl"
        self.connections = []
        self.delivery_required = images_state._deliveryRequired
        images_state._deliveryRequired = False

    async def asyncTearDown(self):
        images_state._deliveryRequired = self.delivery_required
        for connection in self.connections:
            connection.close()
            await connection.waitClosed()
        self.temp.cleanup()

    def computer(self, mode="observation-sdk"):
        driver = dict(
            command=sys.executable,
            args=[str(ROOT / "tests/fixtures/v5-driver.py"), mode, str(self.journal)],
            timeoutMs=3000,
        )
        with patch(
            "computer_use.interaction.controller.Connection",
            side_effect=lambda invalidate, **options: Connection(invalidate, driver, **options),
        ):
            computer = Computer()
        connection = computer._controller.transport
        self.connections.append(connection)
        return computer

    async def test_default_constructor_uses_v6_and_stays_lazy(self):
        from computer_use.client import computer as public_handles

        self.assertFalse(hasattr(public_handles, "Controller"))
        computer = Computer()
        self.assertIsInstance(computer._controller, NativeController)
        self.assertEqual(computer._controller.transport.protocol_version, 6)
        self.assertIsNone(computer._controller.transport._child)
        self.connections.append(computer._controller.transport)

    def test_default_wire_never_imports_legacy_adapters_or_renderer(self):
        code = f"""import asyncio, sys
class NoLegacy:
    def find_spec(self, name, path=None, target=None):
        if name in {{"legacy_client", "legacy_observation_store",
                     "computer_use.client.observation_store", "computer_use.client.results"}}:
            raise AssertionError("Legacy module imported by native session: " + name)
sys.meta_path.insert(0, NoLegacy())
from computer_use import Computer
async def main():
    c = Computer()
    connection = c._controller.transport
    connection.driver = {{"command": sys.executable,
        "args": [{str(ROOT / "tests/fixtures/v5-driver.py")!r}, "directory", {str(self.journal)!r}],
        "timeoutMs": 3000}}
    try:
        await c.listApps()
        assert connection.protocol_version == 6
    finally:
        connection.close()
        await connection.waitClosed()
asyncio.run(main())
"""
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.writes())
        self.assertTrue(
            all(r["params"].get("query", {}).get("kind") in ("applications", "surfaces", None) for r in self.requests())
        )

    def requests(self):
        if not self.journal.exists():
            return []
        return [value for line in self.journal.read_text().splitlines() if "method" in (value := json.loads(line))]

    def writes(self):
        return [r for r in self.requests() if r["method"] == "begin" and r["params"]["mode"] == "write"]

    async def observe(self, mode="observation-sdk"):
        computer = self.computer(mode)
        app = computer.app("Native fixture")
        state = await app.read(view="both")
        return computer, app, state

    async def test_discovery_publishes_integer_instances_and_app_selection_is_lazy(self):
        computer = self.computer("observation-sdk-multiple")
        pending = computer.listApps()
        await asyncio.sleep(0)
        self.assertFalse(self.requests())
        apps = await pending
        self.assertEqual(len(apps), 2)
        self.assertTrue(all(type(item.id) is int for item in apps))
        before = len(self.requests())
        app = computer.app(apps[1].id)
        self.assertEqual(len(self.requests()), before)
        result = await app.read()
        self.assertIn("Second document", result.text)
        self.assertEqual(app._instance.anchor.root.root_window, "second-root")
        self.assertFalse(self.writes())

    async def test_unselected_independent_windows_require_an_app_choice(self):
        computer = self.computer("observation-sdk-multiple")
        app = computer.app("Native fixture")
        result = await app.read()
        self.assertIn("computer.app(", result.text)
        self.assertIsNone(app._instance.anchor)
        self.assertIsNone(result.screenshot)
        with self.assertRaises(ComputerUseError):
            app.window(0)
        with self.assertRaises(ComputerUseError):
            await app.pressKey("Return")
        self.assertFalse(self.writes())

    async def test_default_read_is_tree_only_and_public_observation_has_two_fields(self):
        app = self.computer().app("Native fixture")
        result = await app.read()
        self.assertEqual(set(result), {"text", "screenshot"})
        self.assertIsNone(result.screenshot)
        self.assertIn("Input", result.text)
        self.assertIn("Additional native facts/actions/relations", result.text)
        self.assertFalse(any(r["method"] == "capture" for r in self.requests()))
        with self.assertRaises(AttributeError):
            _ = result.details
        with self.assertRaises(TypeError):
            result["text"] = "rewrite"

    async def test_image_only_read_does_not_collect_tree_or_menu(self):
        app = self.computer("observation-sdk-menu").app("Native fixture")
        result = await app.read(view="image")
        self.assertIsNotNone(result.screenshot)
        self.assertIn("Tree: not requested", result.text)
        self.assertFalse(any(r["params"].get("query", {}).get("kind") in ("nodes", "roots") for r in self.requests()))
        with self.assertRaises(ComputerUseError):
            await app.element(0)

    async def test_integer_lookup_has_no_io_and_names_resolve_before_actions(self):
        _, app, _ = await self.observe()
        before = len(self.requests())
        field = await app.element(1)
        self.assertEqual(len(self.requests()), before)
        self.assertEqual(field._snapshot["binding"].node, "field")
        query_field = await app.element("Input", timeoutMs=0)
        self.assertEqual(query_field._snapshot["binding"].node, "field")
        mark = len(self.requests())
        await query_field.typeText("fixed", replace=True)
        calls = self.requests()[mark:]
        self.assertEqual(len([r for r in calls if r["method"] == "begin" and r["params"]["mode"] == "write"]), 1)
        self.assertFalse(any(r["method"] == "capture" for r in calls))
        self.assertEqual(self.writes()[-1]["params"]["target"]["node"], "field")
        # A numeric string is a name query, not the already-observed reference.
        with self.assertRaises(ComputerUseError) as error:
            await app.element("1", timeoutMs=0)
        self.assertEqual(error.exception.code, "element_not_found")

    async def test_invalid_selectors_and_options_fail_before_native_io(self):
        computer = self.computer()
        app = computer.app("Native fixture")
        for ref in (True, 1.0, None, {}, -1):
            with self.subTest(ref=ref), self.assertRaises(ComputerUseError):
                await app.element(ref)
        for selector in (True, 1.0, None, -1):
            with self.assertRaises(ComputerUseError):
                computer.app(selector)
        for invoke in (
            lambda: app.read(view="invalid"),
            lambda: app.read(menuPath=["File"], view="both"),
            lambda: app.click({"button": "right"}, at=(0, 0)),
            lambda: app.click(at=(True, 2)),
            lambda: app.scroll("down", at=(0, 0), pages=0),
        ):
            with self.assertRaises(ComputerUseError):
                await invoke()
        self.assertFalse(self.requests())
        with self.assertRaises(TypeError):
            ClickOption("right")
        with self.assertRaises(Exception):
            ClickOption().count = 2

    async def test_fixed_saved_elements_keep_native_binding_after_reads(self):
        _, app, first = await self.observe()
        field, button = await app.element(1), await app.element(2)
        before = first.text
        await button.read()
        await field.typeText("changed", replace=True)
        await button.click()
        await app.read()
        await button.performAction("AXPress")
        self.assertEqual([r["params"]["target"]["node"] for r in self.writes()], ["field", "button", "button"])
        self.assertEqual(first.text, before)
        self.assertEqual((await field.readText()).text, "changed")

    async def test_actions_never_automatically_capture_or_publish_a_tree(self):
        _, app, _ = await self.observe()
        field = await app.element(1)
        mark = len(self.requests())
        await field.typeText("replace", replace=True)
        await field.typeText("insert")
        await field.pressKey("Return")
        await (await app.element(2)).click()
        self.assertEqual([r["params"]["intent"]["kind"] for r in self.writes()], ["replace", "insert", "key", "click"])
        self.assertFalse(any(r["method"] == "capture" for r in self.requests()[mark:]))
        for r in self.requests()[mark:]:
            if r["params"].get("query", {}).get("kind") == "nodes":
                self.assertLessEqual(
                    r["params"]["query"]["maxDepth"], 1, "Only narrow control facts are permitted during input"
                )

    async def test_uncertain_input_is_not_replayed(self):
        _, app, _ = await self.observe("observation-sdk-uncertain")
        mark = len(self.requests())
        with self.assertRaises(ComputerUseError) as caught:
            await (await app.element(1)).typeText("once", replace=True)
        self.assertIn(caught.exception.effect, ("possible", "partial"))
        later = self.requests()[mark:]
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(len([r for r in later if r["method"] == "execute"]), 1)
        self.assertFalse(any(r["method"] == "capture" for r in later))

    async def test_window_is_a_fixed_ui_with_shared_element_identity_and_own_image(self):
        _, app, _ = await self.observe()
        mark = len(self.requests())
        ref = next(iter(app._instance.windows))
        window = app.window(ref)
        self.assertIsInstance(app, UI)
        self.assertIsInstance(window, Window)
        self.assertNotIsInstance(window, App)
        self.assertFalse(hasattr(window, "window"))
        self.assertEqual(len(self.requests()), mark)
        saved = await app.element(1)
        self.assertEqual((await window.element(1))._snapshot["binding"], saved._snapshot["binding"])
        original = app._image
        await window.read(view="both")
        self.assertIs(app._image, original)
        self.assertIsNot(window._image, app._image)
        self.assertEqual((await saved.readText()).text, "A🙂e\u0301\r\nZ")

    async def test_text_pages_are_code_points_and_immutable_after_an_edit(self):
        _, app, _ = await self.observe()
        field = await app.element(1)
        page = await field.readText(length=1)
        self.assertEqual((page.text, page.total), ("A", 7))
        self.assertIsInstance(page.next, str)
        mark = len(self.requests())
        emoji = await field.readText(cursor=page.next, length=1)
        self.assertEqual(emoji.text, "🙂")
        self.assertEqual(len(self.requests()), mark)
        await field.typeText("new value", replace=True)
        tail = await field.readText(cursor=emoji.next)
        self.assertEqual(tail.text, "e\u0301\r\nZ")
        fresh = await field.readText()
        self.assertEqual((fresh.text, fresh.total, fresh.next), ("new value", 9, None))
        self.assertEqual(set(fresh), {"text", "total", "next"})

    async def test_cursors_do_not_cross_targets_or_methods(self):
        _, app, _ = await self.observe()
        field, button = await app.element(1), await app.element(2)
        page = await field.readText(length=1)
        mark = len(self.requests())
        for invoke in (
            lambda: button.readText(cursor=page.next),
            lambda: field.read(cursor=page.next),
            lambda: field.readText(cursor="p1"),
            lambda: field.readText(length=0),
        ):
            with self.assertRaises(ComputerUseError) as caught:
                await invoke()
            self.assertEqual(caught.exception.effect, "none")
        self.assertEqual(len(self.requests()), mark)

    async def test_menu_scopes_are_readable_without_replacing_window_image(self):
        _, app, original = await self.observe("observation-sdk-menu")
        image = app._image
        self.assertIn("Application menu", original.text)
        await app.read(menuPath=["File", "Open…"])
        self.assertIs(app._image, image)
        opened = await app.element([{"role": "menuItem", "name": "Open…"}], timeoutMs=0)
        result = await opened.read()
        self.assertIn("Open…", result.text)
        self.assertEqual(opened._snapshot["binding"].surface, "menu-surface")
        self.assertFalse(self.writes())
        await app.read()
        field = await app.element("Input", timeoutMs=0)
        self.assertEqual(field._snapshot["binding"].surface, "surface")

    async def test_windowless_menu_preserves_running_identity_and_readonly_scope(self):
        computer, app, result = await self.observe("observation-sdk-menu-windowless")
        self.assertIn("No working window is bound", result.text)
        self.assertIsNone(result.screenshot)
        await app.read(menuPath=["File"])
        opened = await app.element([{"role": "menuItem", "name": "Open…"}], timeoutMs=0)
        self.assertIn("Open…", (await opened.read()).text)
        with self.assertRaises(ComputerUseError):
            await app.pressKey("Return")
        self.assertFalse(self.writes())
        computer._controller.transport.close()
        await computer._controller.transport.waitClosed()
        mark = len(self.requests())
        with self.assertRaises(ComputerUseError) as caught:
            await app.read(menuPath=["File"])
        self.assertEqual(caught.exception.code, "stale_target")
        self.assertEqual(len(self.requests()), mark)

    async def test_menu_errors_preserve_ambiguity_and_missing_information(self):
        for suffix, path, code in [
            ("-ambiguous", ["File"], "ambiguous_target"),
            ("-unknown-title", ["File"], "observation_incomplete"),
            ("", ["File", "Missing"], "element_not_found"),
        ]:
            app = self.computer("observation-sdk-menu" + suffix).app("Native fixture")
            with self.subTest(suffix=suffix), self.assertRaises(ComputerUseError) as caught:
                await app.read(menuPath=path)
            self.assertEqual(caught.exception.code, code)
        self.assertFalse(self.writes())

    async def test_closed_root_does_not_switch_to_another_document(self):
        app = self.computer("observation-root-gone").app("Native fixture")
        with self.assertRaises(ComputerUseError):
            await app.read()
        with self.assertRaises(ComputerUseError) as caught:
            await app.read()
        self.assertEqual(caught.exception.code, "stale_target")
        self.assertFalse(self.writes())

    async def test_image_must_be_delivered_and_new_capture_preserves_equal_pixel_knowledge(self):
        images_state._deliveryRequired = True
        _, app, first = await self.observe()
        with self.assertRaises(ComputerUseError):
            await app.click(at=(0, 0))
        images.imageDelivered(first.screenshot)
        await app.click(ClickOption(button="right", count=2), at=(0, 0))
        second = await app.read(view="both")
        # A new capture still owns a new native token; no old mapping reuse.
        self.assertIsNot(second.screenshot, first.screenshot)
        images.imageDelivered(second.screenshot)
        await app.click(at=(0, 0))
        self.assertNotEqual(
            self.writes()[0]["params"]["target"]["capture"], self.writes()[1]["params"]["target"]["capture"]
        )

    async def test_handles_cannot_be_constructed_without_scope(self):
        for cls in (UI, App, Window, Element):
            with self.subTest(cls=cls), self.assertRaises(TypeError):
                cls()

    async def test_same_pixels_reuse_exposure_only_in_the_same_ui_and_scope(self):
        images_state._deliveryRequired = True
        _, app, first = await self.observe()
        images.imageDelivered(first.screenshot)
        old_capture = app._image.pieces[0].image.target.capture
        await app.read(view="both")
        await app.click(at=(0, 0))
        self.assertNotEqual(self.writes()[-1]["params"]["target"]["capture"], old_capture)
        window = app.window(next(iter(app._instance.windows)))
        await window.read(view="both")
        with self.assertRaises(ComputerUseError) as caught:
            await window.click(at=(0, 0))
        self.assertEqual(caught.exception.code, "input_unavailable")

    async def test_launch_owner_handoff_and_retry_never_relaunch(self):
        from computer_use.native.scopes import StartingApplication

        app = self.computer("observation-launch-wrapper-retry").app("Native fixture")
        error = ComputerUseError(
            dict(
                code="execution_failed",
                effect="none",
                target="startup directory",
                detail="provider temporarily unavailable",
                next="read again",
            )
        )
        with patch.object(app._controller.reader, "_startup_directory", side_effect=error):
            with self.assertRaises(ComputerUseError) as first:
                await app.read()
        self.assertEqual(first.exception.effect, "possible")
        self.assertIsInstance(app._controller.state(app).running, StartingApplication)
        result = await app.read()
        self.assertIn("Window owner", result.text)
        self.assertEqual(app._instance.anchor.application, "running:child")
        self.assertEqual(len(self.writes()), 1)

    async def test_failed_read_retains_a_launched_root_and_does_not_relaunch(self):
        app = self.computer("observation-launch-root-gone").app("Native fixture")
        with self.assertRaises(ComputerUseError):
            await app.read()
        with self.assertRaises(ComputerUseError) as caught:
            await app.read()
        self.assertEqual((caught.exception.code, caught.exception.effect), ("stale_target", "none"))
        self.assertEqual(len(self.writes()), 1)

    async def test_reused_window_slot_gets_a_new_number_and_cannot_retarget_a_saved_window(self):
        _, app, _ = await self.observe()
        ref = next(iter(app._instance.windows))
        window = app.window(ref)
        original = deepcopy(app._instance.directory)
        changed = deepcopy(original)
        changed["surfaces"][0]["surface"] = "new-generation"
        old_numbers = app._controller.reader.window_aliases(original)
        new_numbers = app._controller.reader.window_aliases(changed)
        self.assertNotEqual(old_numbers, new_numbers)
        app._instance.members[window._anchor]["surface"] = "new-generation"
        mark = len(self.requests())
        with self.assertRaises(ComputerUseError) as caught:
            await window.element(1)
        self.assertEqual(caught.exception.code, "stale_target")
        self.assertEqual(len(self.requests()), mark)

    async def test_partial_member_directory_can_recover_through_saved_window_read(self):
        _, app, _ = await self.observe("observation-sdk-owned-modal")
        controller, instance = app._controller, app._instance
        window = app.window(next(ref for ref, key in instance.windows.items() if key == "dialog"))
        partial = deepcopy(instance.directory)
        partial["surfaces"] = [item for item in partial["surfaces"] if item["window"] != "dialog"]
        partial["stopReasons"] = ["injected unread catalogue remainder"]
        with patch.object(controller.reader, "_discover", return_value=(instance.anchor, partial)):
            with self.assertRaises(ComputerUseError) as caught:
                await window.read(view="image")
            self.assertEqual((caught.exception.code, caught.exception.effect), ("observation_incomplete", "none"))
        mark = len(self.requests())
        result = await window.read(view="both")
        self.assertIn("Owned dialog", result.text)
        self.assertIsNotNone(result.screenshot)
        self.assertIn("dialog", instance.members)
        self.assertTrue(any(r["params"].get("query", {}).get("kind") == "surfaces" for r in self.requests()[mark:]))
        self.assertFalse(self.writes())

    async def test_saved_element_retries_partial_scope_then_dispatches_once_after_recovery(self):
        _, app, _ = await self.observe("observation-sdk-owned-modal")
        controller, instance = app._controller, app._instance
        window = app.window(next(ref for ref, key in instance.windows.items() if key == "dialog"))
        ref = next(int(ref) for ref, item in instance.refs.items() if item["binding"].surface == "dialog-surface")
        button = await window.element(ref)
        original_binding = button._snapshot["binding"]
        partial = deepcopy(instance.directory)
        partial["surfaces"] = [item for item in partial["surfaces"] if item["window"] != "dialog"]
        partial["stopReasons"] = ["injected unread catalogue remainder"]
        with patch.object(controller.reader, "_discover", return_value=(instance.anchor, partial)):
            with self.assertRaises(ComputerUseError) as caught:
                await button.click()
            self.assertEqual((caught.exception.code, caught.exception.effect), ("observation_incomplete", "none"))
            self.assertFalse(self.writes())
        await button.read()  # The saved Element can refresh its own evidence.
        await button.click()
        self.assertEqual(button._snapshot["binding"], original_binding)
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(self.writes()[0]["params"]["target"]["surface"], "dialog-surface")

    async def test_root_invalidation_during_principal_read_retires_the_existing_app(self):
        from computer_use.native.scopes import FixedWindowInvalidated

        _, app, _ = await self.observe()
        error = FixedWindowInvalidated(app._instance.anchor.root, "complete inner directory disproved this root")
        with patch.object(app._controller.reader, "read", side_effect=error):
            with self.assertRaises(ComputerUseError) as caught:
                await app.read()
            self.assertIs(caught.exception, error)
        mark = len(self.requests())
        with self.assertRaises(ComputerUseError) as caught:
            await app.read()
        self.assertEqual((caught.exception.code, caught.exception.effect), ("stale_target", "none"))
        self.assertEqual(len(self.requests()), mark, "A restored catalogue cannot revive a retired root")

    async def test_new_snapshot_has_its_own_window_lifetime_when_surface_is_reused(self):
        _, app, _ = await self.observe("observation-sdk-owned-modal")
        controller, instance = app._controller, app._instance
        old_ref = next(ref for ref, key in instance.windows.items() if key == "dialog")
        old_window = app.window(old_ref)
        ref = next(int(ref) for ref, item in instance.refs.items() if item["binding"].surface == "dialog-surface")
        old_button = await app.element(ref)
        changed = deepcopy(instance.directory)
        next(item for item in changed["surfaces"] if item["window"] == "dialog")["instance"] = "new-generation"
        with patch.object(controller.reader, "_discover", return_value=(instance.anchor, changed)):
            await app.read()
            new_window = app.window(next(ref for ref, key in instance.windows.items() if key == "dialog"))
            self.assertIsNot(new_window, old_window)
            ref = next(int(ref) for ref, item in instance.refs.items() if item["binding"].surface == "dialog-surface")
            new_button = await new_window.element(ref)
            self.assertNotEqual(old_button._snapshot["window_identity"], new_button._snapshot["window_identity"])
            with self.assertRaises(ComputerUseError) as caught:
                await old_button.click()
            self.assertEqual(caught.exception.code, "stale_target")
            self.assertFalse(self.writes())
            await new_button.read()
            await new_button.click()
        self.assertEqual(len(self.writes()), 1)

    async def test_reused_app_window_slot_does_not_rebind_an_already_published_app_id(self):
        from computer_use.session.interfaces import ApplicationEntry

        _, app, _ = await self.observe()
        instance = app._instance
        old = ApplicationEntry(
            instance.directory["session"],
            instance.anchor.application,
            "Fixture",
            True,
            deepcopy(instance.directory),
            instance.anchor.root.root_window,
        )
        refs = app._controller.interfaces.references
        old_id = refs.publish(old)
        changed = deepcopy(old.directory)
        changed["surfaces"][0]["surface"] = "replacement"
        new = ApplicationEntry(old.session, old.selector, old.display_name, True, changed, old.window)
        new_id = refs.publish(new)
        self.assertNotEqual(new_id, old_id)
        self.assertIs(refs.resolve(old_id, old.session), old)

    async def test_dialog_disappearing_during_read_never_publishes_it_as_a_current_member(self):
        from dataclasses import replace

        from computer_use.interaction.query.instances.scope import adopt
        from computer_use.interaction.read.model import AppObservation

        _, app, _ = await self.observe()
        controller, instance = app._controller, app._instance
        final, old = deepcopy(instance.directory), deepcopy(instance.directory)
        modal = {
            **old["surfaces"][0],
            "window": "gone-modal",
            "surface": "gone-surface",
            "node": "gone-node",
            "instance": "gone-instance",
            "parent": instance.anchor.root.root_window,
            "parentState": "known",
            "modal": True,
            "kind": "sheet",
        }
        old["surfaces"].append(modal)
        adopt(controller, app, old, instance.anchor)
        source = AppObservation(
            instance.anchor,
            replace(instance.anchor.root, surface="gone-surface", node="gone-node"),
            None,
            old,
            final,
            None,
            None,
            "view disappeared",
            {},
            {},
            (),
            False,
        )
        mark = len(self.requests())
        with (
            patch.object(controller.reader, "_discover", return_value=(instance.anchor, old)),
            patch.object(controller.reader, "read", return_value=source),
        ):
            result = await app.read()
        self.assertIn("Input", result.text)
        self.assertIn("not uniquely confirmed", result.text)
        self.assertNotIn("gone-modal", instance.members)
        self.assertFalse(
            any(r["params"].get("target", {}).get("surface") == "gone-surface" for r in self.requests()[mark:])
        )

    async def test_oversized_text_source_does_not_return_an_already_evicted_next_cursor(self):
        _, app, _ = await self.observe()
        app._controller.interfaces.text_cursors.max_characters = 3
        with self.assertRaises(ComputerUseError) as caught:
            await (await app.element(1)).readText(length=1)
        self.assertEqual(caught.exception.code, "observation_incomplete")
        self.assertFalse(self.writes())
