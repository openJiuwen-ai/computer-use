"""Retained AT-SPI element pointer fallback with only native OS seams replaced.

The public SDK tests include the server, protocol decoder, Python policy and
native execution units. These portable mechanisms complement desktop acceptance.
"""

import json
import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fixture_driver import environmentDriver
from linux_native_provider import Backend, ElementPointerBackend, NativeError, Node

import backends.linux.test_linux_native as base
from computer_use import ClickOption
from computer_use.client.factory import nativeComputer
from computer_use.diagnostics import setDiagnosticHandler
from computer_use.errors import ComputerUseError
from computer_use.native.connection import Connection
from computer_use.native.contracts import HostPolicy
from computer_use.native.transport import ConnectionPort
from computer_use.protocol.execution import decodeExecutionResult
from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.objects.registry import Window
from computer_use_linux.runtime.native import NativeExecution


class LinuxNativeElementPointerTests(unittest.TestCase):
    def setUp(self):
        self.b = ElementPointerBackend()
        self.b.desktop.assist = False
        self.driver = NativeExecution(self.b, "session")
        self.pid = patch("computer_use_linux.windowing.native_directory.process_start", return_value="generation")
        self.pid.start()
        application = self.driver.directory.applications(base.context())["applications"][0]["id"]
        row = self.driver.directory.read(application, base.context())["surfaces"][0]
        self.target = base.scope(instance=row["instance"], rootWindow=row["window"], surface=row["surface"])

    def tearDown(self):
        self.driver.close()
        self.pid.stop()

    def node(self, obj):
        return self.driver.store.keep(self.target["surface"], obj)

    def setup_pointer(self, kind="click", parameters=None, *, obj=None, within=()):
        self.selected = dict(self.target, node=self.node(obj or self.b.canvas))
        self.within = [self.node(item) for item in within]
        self.operation = self.driver.perform(
            "begin",
            dict(mode="write", target=self.selected, intent=dict(kind=kind, parameters=parameters or {})),
            base.context(),
        )["operation"]
        return self.inspect()

    def inspect(self):
        return decodeExecutionResult(
            "inspect",
            self.driver.perform(
                "inspect",
                dict(
                    operation=self.operation, target=self.selected, query=dict(kind="inputContext", within=self.within)
                ),
                base.context(),
            ),
        )

    @staticmethod
    def pointer(facts):
        return next(p for p in facts["primitives"] if p["unit"] == "pointer_sequence")

    def command(self, facts, *, prepare=False, foreground=False, unit="pointer_sequence"):
        primitive = next(p for p in facts["primitives"] if p["unit"] == unit)
        return dict(
            kind="prepare_receiver" if prepare else unit,
            primitiveId=primitive["id"],
            target=self.selected,
            foreground=foreground,
        )

    def send(self, command, step=1, ctx=None):
        return decodeExecutionResult(
            "execute",
            self.driver.perform(
                "execute", dict(operation=self.operation, step=step, command=command), ctx or base.context()
            ),
        )

    def finish(self):
        self.driver.finish(self.operation)

    def kinds(self):
        return [event[0] for event in self.b.desktop.events]

    def test_visible_background_exact_element_needs_no_keyboard_or_foreground(self):
        facts = self.setup_pointer(within=(self.b.group,))
        self.assertEqual((self.pointer(facts)["readiness"], facts["input"]["geometryCurrent"]), ("yes", "yes"))
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.assertEqual(self.b.desktop.events, [(6, 0, 90, 80), (4, 1, 0, 0), (5, 1, 0, 0)])
        self.assertEqual((self.b.desktop.foreground, self.b.desktop.keyboard_window), (22, 22))
        self.assertEqual(self.b.desktop.activations, [])
        self.assertEqual(self.b.canvas.text, "1")
        self.assertFalse(any(name == "grab_focus" for name, _, _ in self.b.ax.calls))
        self.assertTrue(all(item[1:] == (90, 80) for item in self.b.hit_queries))
        self.assertIn(self.b.window, [item[0] for item in self.b.hit_queries])
        self.assertIn(self.b.group, [item[0] for item in self.b.hit_queries])

    def test_live_same_object_geometry_between_inspect_and_execute_is_resampled(self):
        facts = self.setup_pointer()
        self.b.canvas.rect = (120, 160, 100, 60)
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.assertEqual(self.b.desktop.events[0], (6, 0, 170, 190))

    def test_right_middle_double_click_use_exact_pointer_buttons(self):
        for params, detail, count in [({"button": "right"}, 3, 1), ({"button": "middle"}, 2, 1), ({"count": 2}, 1, 2)]:
            with self.subTest(params=params):
                facts = self.setup_pointer(parameters=params, obj=self.b.button)
                self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
                self.assertEqual(self.kinds(), [6] + [4, 5] * count)
                self.assertEqual([event[1] for event in self.b.desktop.events[1:]], [detail] * (count * 2))
                self.assertEqual(self.b.button.action_history, [])
                self.finish()
                self.b.desktop.events.clear()

    def test_scroll_routes_wheel_to_exact_component_with_paired_releases(self):
        for direction, button in [("up", 4), ("down", 5), ("left", 6), ("right", 7)]:
            with self.subTest(direction=direction):
                facts = self.setup_pointer("scroll", dict(direction=direction, pages=0.25))
                self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
                self.assertEqual(self.kinds(), [6, 4, 5, 4, 5])
                self.assertEqual([e[1] for e in self.b.desktop.events[1:]], [button] * 4)
                self.assertFalse(self.b.desktop.buttons)
                self.finish()
                self.b.desktop.events.clear()

    def test_no_component_is_a_pointer_limit_not_a_native_action_limit(self):
        self.b.button.interfaces.remove("Component")
        facts = self.setup_pointer(obj=self.b.button)
        self.assertIn("geometry", self.pointer(facts)["pending"])
        self.assertEqual(self.send(self.command(facts, unit="native_action"))["dispatch"], "dispatched")
        self.assertEqual(self.b.button.action_history, ["click"])
        self.assertEqual(self.b.desktop.events, [])

    def test_capability_requires_native_pointer_input(self):
        self.assertIn("nativeElementPointer", self.driver.capabilities)
        self.assertIn("nativeControlParts", self.driver.capabilities)
        b = Backend()
        driver = NativeExecution(b, "other")
        try:
            self.assertNotIn("nativeElementPointer", driver.capabilities)
            self.assertNotIn("nativeControlParts", driver.capabilities)
        finally:
            driver.close()

    def test_missing_ax_root_never_enters_pointer_route(self):
        facts = self.setup_pointer()
        self.b.desktop.items[0].accessible = None
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["errorCode"]), ("notDispatched", "input_unavailable"))
        self.assertEqual(self.b.desktop.events, [])

    def test_same_window_overlap_and_descendant_are_not_substituted(self):
        facts = self.setup_pointer()
        for target in (Node("button", "Sibling"), Node("text", "Descendant", parent=self.b.canvas)):
            with self.subTest(target=target.name):
                self.b.hit_overrides[self.b.group] = target
                result = self.send(self.command(facts), step=1 if target.name == "Sibling" else 2)
                self.assertEqual(result["dispatch"], "notDispatched")
                self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(self.b.desktop.activations, [])

    def test_container_rect_does_not_authorize_clicking_its_descendant(self):
        self.b.group.rect = self.b.canvas.rect
        facts = self.setup_pointer(obj=self.b.group)
        self.assertIn("geometry", self.pointer(facts)["pending"])
        self.assertEqual(self.send(self.command(facts))["dispatch"], "notDispatched")
        self.assertEqual(self.b.desktop.events, [])

    def test_unknown_root_hit_cycle_and_missing_root_component_remain_unavailable(self):
        for scenario in ("null", "cycle", "no-root-component"):
            with self.subTest(scenario=scenario):
                if scenario == "null":
                    self.b.hit_overrides[self.b.window] = None
                elif scenario == "cycle":
                    self.b.hit_overrides[self.b.group] = self.b.window
                else:
                    self.b.window.interfaces = []
                facts = self.setup_pointer()
                self.assertIn("geometry", self.pointer(facts)["pending"])
                self.assertEqual(self.send(self.command(facts))["dispatch"], "notDispatched")
                self.assertEqual(self.b.desktop.events, [])
                self.finish()
                self.b.hit_overrides.clear()
                self.b.window.interfaces = ["Component"]

    def test_non_component_leaf_can_be_hit_but_cannot_gain_pointer_geometry(self):
        self.b.canvas.interfaces.remove("Component")
        facts = self.setup_pointer()
        self.assertIn("geometry", self.pointer(facts)["pending"])
        self.assertEqual(self.send(self.command(facts))["dispatch"], "notDispatched")
        self.assertEqual(self.b.desktop.events, [])

    def test_hidden_disabled_invalid_or_offwindow_geometry_is_not_dispatched(self):
        original = self.b.canvas.rect
        for scenario in ("hidden", "disabled", "zero", "nonfinite", "offwindow", "native-error"):
            with self.subTest(scenario=scenario):
                facts = self.setup_pointer()
                if scenario == "hidden":
                    self.b.canvas.states.discard("VISIBLE")
                elif scenario == "disabled":
                    self.b.canvas.states -= {"ENABLED", "SENSITIVE"}
                elif scenario == "zero":
                    self.b.canvas.rect = (0, 0, 0, 20)
                elif scenario == "nonfinite":
                    self.b.canvas.rect = (float("nan"), 0, 20, 20)
                elif scenario == "offwindow":
                    self.b.canvas.rect = (800, 800, 20, 20)
                else:
                    self.b.canvas.errors["get_extents"] = NativeError("Provider unavailable")
                self.assertEqual(self.send(self.command(facts))["dispatch"], "notDispatched")
                self.assertEqual(self.b.desktop.events, [])
                self.finish()
                self.b.canvas.rect = original
                self.b.canvas.states |= {"ENABLED", "SENSITIVE", "VISIBLE"}
                self.b.canvas.errors.clear()

    def test_covered_window_needs_authorized_preparation_and_no_restore(self):
        self.b.desktop.covered = True
        facts = self.setup_pointer()
        self.assertEqual(self.pointer(facts)["pending"], ["foreground"])
        self.assertEqual(self.send(self.command(facts, prepare=True))["retryReason"], "preparationUnavailable")
        denied = self.send(self.command(facts, prepare=True, foreground=True), 2)
        self.assertEqual((denied["dispatch"], denied["errorCode"]), ("notDispatched", "mode_unsupported"))
        self.assertEqual(self.b.desktop.activations, [])
        self.b.desktop.assist = True
        allowed = self.send(self.command(facts, prepare=True, foreground=True), 3)
        self.assertEqual((allowed["confirmation"], allowed["preparation"]), ("confirmed", ["foreground"]))
        self.assertEqual(self.b.desktop.events, [])
        facts = self.inspect()
        self.assertEqual(self.send(self.command(facts), 4)["dispatch"], "dispatched")
        self.finish()
        self.assertEqual(self.b.desktop.activations, [11])
        self.assertEqual(self.b.desktop.foreground, 11)

    def test_preparation_may_move_same_element_then_inspect_dispatch_current_geometry(self):
        self.b.desktop.covered = True
        self.b.desktop.assist = True
        facts = self.setup_pointer()
        self.b.desktop.activate_hook = lambda: setattr(self.b.canvas, "rect", (100, 150, 100, 60))
        self.assertEqual(self.send(self.command(facts, prepare=True, foreground=True))["confirmation"], "confirmed")
        self.assertEqual(self.send(self.command(self.inspect()), 2)["dispatch"], "dispatched")
        self.assertEqual(self.b.desktop.events[0], (6, 0, 150, 180))

    def test_original_root_role_ancestor_and_modal_are_rechecked(self):
        original = self.b.desktop.items[0]
        for scenario in ("root", "role", "within", "modal", "lock"):
            with self.subTest(scenario=scenario):
                facts = self.setup_pointer(within=(self.b.group,))
                if scenario == "root":
                    self.b.canvas.parent = Node("frame", "Other document")
                elif scenario == "role":
                    self.b.canvas.role = "entry"
                elif scenario == "within":
                    self.b.canvas.parent = self.b.window
                elif scenario == "modal":
                    self.b.desktop.items.append(
                        Window(
                            12,
                            "Dialog",
                            "Other",
                            42,
                            (0, 0, 100, 100),
                            modal=True,
                            mapped=True,
                            transient=11,
                            process_start="generation",
                            accessible=Node("dialog"),
                        )
                    )
                else:
                    self.b.locked = True
                result = self.send(self.command(facts))
                self.assertEqual(result["dispatch"], "notDispatched")
                self.assertEqual(self.b.desktop.events, [])
                self.finish()
                self.b.canvas.parent = self.b.group
                self.b.canvas.role = "drawing area"
                self.b.desktop.items = [original]
                self.b.locked = False

    def test_after_initial_motion_geometry_or_hit_change_has_possible_effect_and_no_press(self):
        for scenario in ("geometry", "hit", "covered"):
            with self.subTest(scenario=scenario):
                facts = self.setup_pointer()

                def change(kind, *_, scenario=scenario):
                    if kind != 6:
                        return
                    if scenario == "geometry":
                        self.b.canvas.rect = (50, 60, 100, 60)
                    elif scenario == "hit":
                        self.b.hit_overrides[self.b.group] = self.b.button
                    else:
                        self.b.desktop.covered = True

                self.b.desktop.event_hook = change
                command = self.command(facts)
                result = self.send(command)
                self.assertEqual((result["dispatch"], result["cleanup"]), ("unknown", "complete"))
                self.assertEqual(self.kinds(), [6])
                with self.assertRaises(Failure):
                    self.send(command, 2)
                self.finish()
                self.assertEqual(self.send(command), result)
                self.b.desktop.events.clear()
                self.b.canvas.rect = (40, 50, 100, 60)
                self.b.hit_overrides.clear()
                self.b.desktop.covered = False
                self.b.desktop.event_hook = None

    def test_cancel_after_motion_or_press_only_releases_owned_input(self):
        for kind, expected in [(6, [6]), (4, [6, 4, 5])]:
            with self.subTest(kind=kind):
                facts = self.setup_pointer()
                ctx = base.context()
                self.b.desktop.event_hook = lambda event, *_, ctx=ctx, kind=kind: (
                    ctx.cancelled.set() if event == kind else None
                )
                result = self.send(self.command(facts), ctx=ctx)
                self.assertEqual(
                    (result["dispatch"], result["errorCode"], result["cleanup"]),
                    ("unknown" if kind == 6 else "dispatched", "cancelled", "complete"),
                )
                self.assertEqual(self.kinds(), expected)
                self.assertFalse(self.b.desktop.buttons)
                self.finish()
                self.b.desktop.events.clear()
                self.b.desktop.event_hook = None

    def test_lost_press_reply_and_release_failure_never_replay(self):
        for kind in (4, 5):
            with self.subTest(kind=kind):
                facts = self.setup_pointer()

                def fail(event, *_, kind=kind):
                    if event == kind:
                        raise NativeError("Native event reply lost")

                self.b.desktop.event_hook = fail
                command = self.command(facts)
                result = self.send(command)
                self.assertEqual(result["dispatch"], "unknown")
                self.assertEqual(result["cleanup"], "failed" if kind == 5 else "complete")
                with self.assertRaises(Failure):
                    self.send(command, 2)
                self.finish()
                self.b.desktop.event_hook = None
                self.b.desktop.release()
                self.b.desktop.events.clear()

    def test_window_may_close_after_press_and_owned_button_is_still_released(self):
        facts = self.setup_pointer()
        self.b.desktop.event_hook = lambda kind, *_: self.b.desktop.items.clear() if kind == 4 else None
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["cleanup"]), ("dispatched", "complete"))
        self.assertEqual(self.kinds(), [6, 4, 5])

    def test_malformed_or_offscreen_geometry_does_not_hide_native_action(self):
        original = self.b.button.rect
        for scenario in ("none", "missing", "huge", "nan", "offscreen"):
            with self.subTest(scenario=scenario):
                if scenario == "none":
                    self.b.button.errors["get_extents"] = None
                elif scenario == "missing":
                    self.b.button.errors["get_extents"] = object()
                elif scenario == "huge":
                    self.b.button.rect = (10**1000, 0, 100, 30)
                elif scenario == "nan":
                    self.b.button.rect = (float("nan"), 0, 100, 30)
                else:
                    self.b.desktop.screen_size = (100, 100)
                facts = self.setup_pointer(obj=self.b.button)
                self.assertIn("geometry", self.pointer(facts)["pending"])
                self.assertEqual(self.send(self.command(facts, unit="native_action"))["dispatch"], "dispatched")
                self.assertEqual(self.b.desktop.events, [])
                self.finish()
                self.b.button.rect = original
                self.b.button.errors.clear()
                self.b.desktop.screen_size = (1024, 768)

    def test_partial_window_intersection_uses_an_inbounds_native_pixel(self):
        self.b.canvas.rect = (-10, 50, 20, 60)
        self.b.group.rect = (0, 0, 350, 250)
        facts = self.setup_pointer()
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.assertEqual(self.b.desktop.events[0], (6, 0, 5, 80))

    def test_single_pixel_control_uses_its_only_native_pixel(self):
        self.b.canvas.rect = (40, 50, 1, 1)
        facts = self.setup_pointer()
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.assertEqual(self.b.desktop.events[0], (6, 0, 40, 50))

    def test_hit_depth_budget_is_bounded_and_preserves_native_action(self):
        nodes = [Node("panel", interfaces=["Component"]) for _ in range(66)]
        self.b.hit_overrides[self.b.window] = nodes[0]
        self.b.hit_overrides.update(zip(nodes, nodes[1:]))
        facts = self.setup_pointer(obj=self.b.button)
        self.assertIn("geometry", self.pointer(facts)["pending"])
        self.assertLessEqual(len(self.b.hit_queries), 64)
        self.assertEqual(self.send(self.command(facts, unit="native_action"))["dispatch"], "dispatched")

    def test_changed_native_process_or_closed_root_never_retargets(self):
        original = self.b.desktop.items[0]
        for scenario in ("process", "closed"):
            with self.subTest(scenario=scenario):
                facts = self.setup_pointer()
                if scenario == "process":
                    self.b.desktop.items[0] = replace(original, process_start="replacement")
                else:
                    self.b.desktop.items.clear()
                self.assertEqual(self.send(self.command(facts))["dispatch"], "notDispatched")
                self.assertEqual(self.b.desktop.events, [])
                self.finish()
                self.b.desktop.items = [original]
                self.b.registry.instances[self.target["instance"]].alive = True

    def test_within_cannot_contain_target_itself(self):
        with self.assertRaises(Failure) as error:
            self.setup_pointer(within=(self.b.canvas,))
        self.assertEqual(error.exception.code, "invalid_request")
        self.assertEqual(self.b.desktop.events, [])

    def test_input_held_by_user_is_not_released_or_moved(self):
        facts = self.setup_pointer()
        self.b.desktop.user_held = True
        self.assertEqual(self.send(self.command(facts))["dispatch"], "notDispatched")
        self.assertEqual(self.b.desktop.events, [])
        self.assertTrue(self.b.desktop.user_held)

    def test_transient_unmanaged_window_can_be_clicked_but_not_raised(self):
        self.b.desktop.items[0] = replace(self.b.desktop.items[0], override_redirect=True)
        facts = self.setup_pointer()
        self.assertEqual(self.pointer(facts)["supported"], "yes")
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.finish()
        self.b.desktop.events.clear()
        self.b.desktop.covered = True
        facts = self.setup_pointer()
        self.assertEqual(self.pointer(facts)["supported"], "no")
        self.b.desktop.assist = True
        with self.assertRaises(Failure):
            self.send(self.command(facts, prepare=True, foreground=True))
        self.assertEqual(self.b.desktop.activations, [])

    def test_element_dispatch_cannot_smuggle_foreground_action_or_different_target(self):
        facts = self.setup_pointer()
        command = self.command(facts)
        for step, changes in enumerate(
            (
                dict(foreground=True),
                dict(kind="native_action"),
                dict(target={**self.selected, "node": self.node(self.b.button)}),
            ),
            1,
        ):
            with self.assertRaises(Failure):
                self.send({**command, **changes}, step)
        self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(self.b.desktop.activations, [])

    def test_incomplete_action_catalogue_keeps_independent_exact_pointer_route(self):
        self.b.button.actions = [(f"action{i}", "") for i in range(129)]
        facts = self.setup_pointer(obj=self.b.button)
        self.assertFalse(facts["actionsComplete"])
        self.assertEqual([p["unit"] for p in facts["primitives"]], ["pointer_sequence"])
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.assertEqual(self.b.button.action_history, [])

    def test_full_action_catalogue_plus_pointer_keeps_all_native_contracts(self):
        self.b.button.actions = [("click", "Click")] + [(f"action{i}", "") for i in range(127)]
        facts = self.setup_pointer(obj=self.b.button)
        self.assertEqual(len(facts["primitives"]), 129)
        self.assertTrue({p["id"] for p in facts["primitives"]} <= self.driver.current.contracts.keys())
        self.assertEqual(self.send(self.command(facts, unit="native_action"))["dispatch"], "dispatched")
        self.assertEqual(self.b.button.action_history, ["click"])


class LinuxNativeElementPointerSDKTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.connections, self.diagnostics = [], []
        self.temp = TemporaryDirectory()
        self.mode = patch.dict(os.environ, {"COMPUTER_USE_DESKTOP": "shared"})
        self.mode.start()
        setDiagnosticHandler(self.diagnostics.append)

    async def asyncTearDown(self):
        setDiagnosticHandler(None)
        for connection in self.connections:
            connection.close()
        for connection in self.connections:
            await connection.waitClosed()
        self.mode.stop()
        self.temp.cleanup()

    async def connect(self, *, mode="shared", assist=False, covered=False, extra=()):
        os.environ["COMPUTER_USE_DESKTOP"] = mode
        self.journal = Path(self.temp.name) / ("events-" + str(len(self.connections)) + ".jsonl")
        args = [
            "COMPUTER_USE_DESKTOP=" + mode,
            "COMPUTER_USE_FOREGROUND_ASSIST=" + ("1" if assist else "0"),
            sys.executable,
            str(base.ROOT / "tests/fixtures/linux_native_provider.py"),
            "--element-pointer",
            "--pointer-journal",
            str(self.journal),
        ]
        if covered:
            args.append("--covered")
        args.extend(extra)
        connection = Connection(lambda: None, environmentDriver(args, timeoutMs=5000), protocol_version=6)
        self.connections.append(connection)
        computer = nativeComputer(ConnectionPort(connection, HostPolicy(mode, assist)))
        app = computer.app((await computer.listApps())[0].id)
        state = await app.read(view="both")
        self.assertIn("Canvas", state.text)
        return connection, computer, app

    def events(self):
        return [json.loads(line) for line in self.journal.read_text().splitlines()] if self.journal.exists() else []

    def commands(self):
        return [
            e["params"]["command"]
            for e in self.diagnostics
            if e["kind"] == "command_start" and e["method"] == "execute"
        ]

    def no_automatic_read(self):
        events = [e for e in self.diagnostics if e["kind"] == "command_start"]
        last = max(i for i, e in enumerate(events) if e["method"] == "execute")
        self.assertEqual([e["method"] for e in events[last + 1 :]], ["finish"])

    async def test_public_native_action_is_preferred_without_pointer_or_automatic_read(self):
        _, _, app = await self.connect()
        button = await app.element([{"name": "确认"}])
        result = await button.click()
        self.assertEqual(result.status, "dispatched")
        self.assertFalse(hasattr(result, "text"))
        self.assertEqual([c["kind"] for c in self.commands()], ["native_action"])
        self.assertEqual(self.events(), [])
        self.no_automatic_read()
        self.assertEqual((await button.readText()).text, "1")

    async def test_public_element_click_right_double_scroll_use_one_native_pipeline(self):
        connection, _, app = await self.connect()
        canvas = await app.element([{"name": "Controls"}, {"name": "Canvas"}])
        for action in (
            canvas.click,
            lambda: canvas.click(ClickOption(button="right")),
            lambda: canvas.click(ClickOption(count=2)),
            lambda: canvas.scroll("down", pages=0.25),
        ):
            result = await action()
            self.assertEqual(result.status, "dispatched")
            self.assertFalse(hasattr(result, "text"))
            self.no_automatic_read()
        self.assertEqual([c["kind"] for c in self.commands()], ["pointer_sequence"] * 4)
        self.assertTrue(all(c["target"]["node"] and c["target"]["capture"] is None for c in self.commands()))
        self.assertTrue(all(e["active"] == 22 for e in self.events()))
        self.assertEqual((await canvas.readText()).text, "6")
        self.assertIn("nativeElementPointer", connection.capabilities)

    async def test_public_unknown_native_action_is_not_replayed_as_pointer(self):
        _, _, app = await self.connect(assist=True, extra=("--element-action-false",))
        with self.assertRaises(ComputerUseError) as error:
            await (await app.element([{"name": "确认"}])).click()
        self.assertEqual(error.exception.effect, "possible")
        self.assertEqual([c["kind"] for c in self.commands()], ["native_action"])
        self.assertEqual(self.events(), [])
        self.no_automatic_read()

    async def test_public_right_and_double_bypass_declared_native_action(self):
        _, _, app = await self.connect()
        button = await app.element([{"name": "确认"}])
        await button.click(ClickOption(button="right"))
        await button.click(ClickOption(count=2))
        self.assertEqual([c["kind"] for c in self.commands()], ["pointer_sequence"] * 2)
        self.assertEqual((await button.readText()).text, "0")
        self.assertEqual([e["detail"] for e in self.events() if e["kind"] == 4], [3, 1, 1])

    async def test_public_covered_shared_denies_but_native_action_still_works(self):
        _, _, app = await self.connect(covered=True)
        with self.assertRaises(ComputerUseError) as error:
            await (await app.element([{"name": "Canvas"}])).click()
        self.assertEqual(error.exception.effect, "none")
        self.assertEqual(self.events(), [])
        self.assertEqual(self.commands(), [])
        (self.assertEqual((await (await app.element([{"name": "确认"}])).click()).status, "dispatched"))
        self.assertEqual([c["kind"] for c in self.commands()], ["native_action"])

    async def test_public_authorized_or_exclusive_covered_element_prepares_once(self):
        for mode, assist in [("shared", True), ("exclusive", False)]:
            with self.subTest(mode=mode):
                _, _, app = await self.connect(mode=mode, assist=assist, covered=True)
                before = len(self.commands())
                (self.assertEqual((await (await app.element([{"name": "Canvas"}])).click()).status, "dispatched"))
                self.assertEqual(
                    [(c["kind"], c["foreground"]) for c in self.commands()[before:]], [("pointer_sequence", True)]
                )
                self.assertEqual([e["kind"] for e in self.events()], [6, 4, 5])
                self.assertTrue(all(e["active"] == 11 for e in self.events()))
                self.no_automatic_read()

    async def test_public_samewindow_overlap_and_missing_component_do_not_activate(self):
        for flag in ("--element-overlap", "--element-no-component", "--element-no-input"):
            with self.subTest(flag=flag):
                connection, _, app = await self.connect(assist=True, extra=(flag,))
                before = len(self.commands())
                with self.assertRaises(ComputerUseError) as error:
                    await (await app.element([{"name": "Canvas"}])).click()
                self.assertEqual(error.exception.effect, "none")
                self.assertEqual(self.commands()[before:], [])
                self.assertEqual(self.events(), [])
                if flag == "--element-no-input":
                    self.assertNotIn("nativeElementPointer", connection.capabilities)

    async def test_public_after_motion_geometry_change_is_partial_and_has_no_fallback(self):
        _, _, app = await self.connect(assist=True, extra=("--element-move-on-motion",))
        with self.assertRaises(ComputerUseError) as error:
            await (await app.element([{"name": "Canvas"}])).click()
        self.assertEqual(error.exception.effect, "partial")
        self.assertEqual([e["kind"] for e in self.events()], [6])
        self.assertEqual([c["kind"] for c in self.commands()], ["pointer_sequence"])
        self.assertTrue(all(e["active"] == 22 for e in self.events()))
        self.no_automatic_read()

    async def test_public_failed_release_retires_connection_and_never_replays(self):
        _, _, app = await self.connect(extra=("--pointer-release-failed",))
        with self.assertRaises(ComputerUseError) as error:
            await (await app.element([{"name": "Canvas"}])).click()
        self.assertEqual(error.exception.effect, "possible")
        self.assertIn("cleanup", error.exception.detail)
        count = len(self.events())
        with self.assertRaises(ComputerUseError):
            await (await app.element([{"name": "Canvas"}])).click()
        self.assertEqual(len(self.events()), count)
        self.assertEqual([c["kind"] for c in self.commands()], ["pointer_sequence"])


if __name__ == "__main__":
    unittest.main()
