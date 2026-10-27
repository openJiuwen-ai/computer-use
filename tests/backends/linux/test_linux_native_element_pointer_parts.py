"""Common control composition over Linux native facts and pointer contracts.

Only the AT-SPI/X11 provider and transport are in memory. Python selection,
strict wire decoders, retained native evidence and real sequence code execute.
GUI acceptance separately establishes GTK/Qt's actual hit/event behaviour.
"""

import unittest
from copy import deepcopy
from unittest.mock import patch

from linux_native_provider import Node

import backends.linux.test_linux_native as base
import backends.linux.test_linux_native_element_pointer as pointer_tests
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.interaction.write.resolution.control_part import ControlPartQuery
from computer_use.native.contracts import HostPolicy, Intent, Knowledge as K, UnitCommand
from computer_use.native.transport import ConnectionPort, decode_binding, encode_target
from computer_use.protocol.execution import decodeExecutionResult
from computer_use_linux.contracts.protocol import Failure


class DriverConnection:
    protocol_version = 6
    platform = "linux"
    sessionIdentity = ("session", 1)

    def __init__(self, driver):
        self.driver, self.capabilities, self.requests = driver, driver.capabilities, []

    async def request(self, method, params, options):
        self.requests.append((method, deepcopy(params)))
        try:
            result = self.driver.perform(method, params, base.context(options["timeoutMs"] / 1000))
        except Failure as error:
            raise ComputerUseError(error.wire()) from error
        return decodeExecutionResult(method, result)


class LinuxControlPartTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.case = pointer_tests.LinuxNativeElementPointerTests()
        self.case.setUp()
        self.b, self.driver = self.case.b, self.case.driver
        self.owner = self.b.button
        self.layout = Node("panel", "Button layout", parent=self.owner, interfaces=["Component"])
        self.label = Node("label", "Caption", parent=self.layout, interfaces=["Component"])
        self.owner.children = [self.layout]
        self.layout.children = [self.label]
        self.layout.rect = self.owner.rect
        # Contains the owner's center (250,80), but its own center is (265,85).
        self.label.rect = (240, 70, 50, 30)
        for member in (self.layout, self.label):
            member.states.add("VISIBLE")
        self.binding = decode_binding(dict(self.case.target, node=self.case.node(self.owner)))
        self.connection = DriverConnection(self.driver)
        self.port = ConnectionPort(self.connection, HostPolicy())

    def tearDown(self):
        self.case.tearDown()

    async def selected(self, parameters=None):
        self.intent = Intent("click", parameters or {"button": "right"})
        self.operation = (await self.port.begin(self.intent, self.binding, 3000)).token
        initial = await self.port.inspect(self.operation, self.binding, 3000)
        self.assertEqual(initial.input.pointer_hit, self.case.node(self.label))
        self.assertTrue(any("geometry" in p.pending for p in initial.primitives if p.unit == "pointer_sequence"))
        query = ControlPartQuery(self.port, self.operation, self.binding, lambda: 3000)
        self.actuator = await query.resolve(initial.input.pointer_hit)
        self.assertIsNotNone(self.actuator)
        self.basis = query.basis
        facts = await self.port.inspect(self.operation, self.binding, 3000, actuator=self.actuator, basis=self.basis)
        self.assertEqual([p.unit for p in facts.primitives], ["pointer_sequence"])
        self.assertIsNone(facts.input.editor)
        self.assertIsNone(facts.input.editor_owner)
        return facts

    async def send(self, facts, *, prepare=False, foreground=False, step=1):
        return await self.port.execute(
            self.operation,
            step,
            UnitCommand(
                facts.primitives[0], self.binding, self.intent, self.actuator, prepare=prepare, foreground=foreground
            ),
            3000,
        )

    def commands(self):
        return [params["command"] for method, params in self.connection.requests if method == "execute"]

    def kinds(self):
        return [event[0] for event in self.b.desktop.events]

    async def test_hit_is_only_a_candidate_until_common_python_selects_it(self):
        facts = await self.selected()
        self.assertEqual(facts.primitives[0].readiness, K.YES)
        self.assertEqual(facts.binding, self.binding)
        self.assertEqual({field for dep in self.basis for field in dep.fields}, {"role", "editable", "focusable"})
        self.assertTrue({self.binding.node, self.actuator.node} <= self.driver.store.pins)
        result = await self.send(facts)
        self.assertEqual(result.dispatch.value, "dispatched")
        self.assertEqual(self.b.desktop.events, [(6, 0, 250, 80), (4, 3, 0, 0), (5, 3, 0, 0)])
        self.assertEqual(self.owner.action_history, [])
        self.assertEqual((self.b.desktop.foreground, self.b.desktop.keyboard_window), (22, 22))

    async def test_public_policy_preserves_action_and_resolves_only_pointer_clicks(self):
        for parameters, expected in (
            ({}, "native_action"),
            ({"button": "right"}, "pointer_sequence"),
            ({"button": "middle"}, "pointer_sequence"),
            ({"count": 2}, "pointer_sequence"),
        ):
            with self.subTest(parameters=parameters):
                self.connection.requests.clear()
                self.b.desktop.events.clear()
                result = await Engine(self.port).execute(Intent("click", parameters), self.binding)
                self.assertEqual(result["status"], "dispatched")
                self.assertEqual([c["kind"] for c in self.commands()], [expected])
                nodes = [p for m, p in self.connection.requests if m == "inspect" and p["query"]["kind"] == "nodes"]
                self.assertEqual(bool(nodes), expected == "pointer_sequence")
                self.assertTrue(all(p["mode"] == "write" for m, p in self.connection.requests if m == "begin"))
                self.assertFalse(any(m == "capture" for m, _ in self.connection.requests))
                if expected == "pointer_sequence":
                    self.assertEqual(self.b.desktop.events[0], (6, 0, 250, 80))
                    self.assertEqual(self.kinds().count(4), parameters.get("count", 1))

    async def test_passive_native_labels_icons_and_layouts_share_common_selection(self):
        for role in ("label", "image", "heading", "panel"):
            with self.subTest(role=role):
                self.label.role = role
                self.owner.actions.clear()
                if "Action" in self.owner.interfaces:
                    self.owner.interfaces.remove("Action")
                result = await Engine(self.port).execute(Intent("click"), self.binding)
                self.assertEqual(result["status"], "dispatched")
                self.assertEqual(self.commands()[-1]["kind"], "pointer_sequence")

    async def test_independent_unknown_or_incomplete_members_are_not_authorized(self):
        for scenario in (
            "button",
            "entry",
            "cell",
            "action",
            "editable",
            "focusable",
            "unknown-state",
            "partial-actions",
        ):
            with self.subTest(scenario=scenario):
                self.label.role, self.label.actions, self.label.errors = "label", [], {}
                self.label.states.discard("EDITABLE")
                self.label.states.discard("FOCUSABLE")
                if scenario in ("button", "entry", "cell"):
                    self.label.role = {"button": "push button", "entry": "entry", "cell": "table cell"}[scenario]
                elif scenario == "action":
                    self.label.interfaces.append("Action")
                    self.label.actions = [("click", "")]
                elif scenario in ("editable", "focusable"):
                    self.label.states.add(scenario.upper())
                elif scenario == "unknown-state":
                    self.label.errors["get_state_set"] = base.NativeError("state unavailable")
                else:
                    self.label.interfaces.append("Action")
                    self.label.actions = [(str(i), "") for i in range(129)]
                before = len(self.commands())
                with self.assertRaises(ComputerUseError):
                    await Engine(self.port).execute(Intent("click", {"button": "right"}), self.binding)
                self.assertEqual(len(self.commands()), before)
                self.assertEqual(self.b.desktop.events, [])
                self.label.interfaces = ["Component"]

    def mutate(self, scenario):
        if scenario == "action":
            self.label.interfaces.append("Action")
            self.label.actions = [("click", "")]
        elif scenario == "editable":
            self.label.states.add("EDITABLE")
        elif scenario == "focusable":
            self.label.states.add("FOCUSABLE")
        elif scenario == "role":
            self.label.role = "push button"
        elif scenario == "reparent":
            self.label.parent = self.b.group
        elif scenario == "replace":
            replacement = Node("label", "Caption", parent=self.layout, interfaces=["Component"])
            replacement.rect = self.label.rect
            self.layout.children = [replacement]
        elif scenario == "owner-disabled":
            self.owner.states.difference_update(("ENABLED", "SENSITIVE"))
        elif scenario == "geometry":
            self.owner.rect = (210, 50, 100, 60)

    async def test_retained_basis_blocks_changed_facts_before_first_dispatch(self):
        for scenario in ("action", "editable", "focusable", "role", "reparent", "replace", "owner-disabled"):
            with self.subTest(scenario=scenario):
                # Each subcase starts with a fresh real native operation/object graph.
                self.tearDown()
                self.setUp()
                facts = await self.selected()
                self.mutate(scenario)
                result = await self.send(facts)
                self.assertEqual(result.dispatch.value, "notDispatched")
                self.assertIsNotNone(result.error_code)
                self.assertEqual(self.b.desktop.events, [])

    async def test_geometry_provider_callback_cannot_stale_the_selection_basis(self):
        facts = await self.selected()

        def callback(obj, *_):
            if obj is self.owner:
                self.mutate("action")

        self.b.ax.hooks["get_extents"] = callback
        result = await self.send(facts)
        self.assertEqual(result.dispatch.value, "notDispatched")
        self.assertEqual(self.b.desktop.events, [])

    async def test_foreground_preparation_rechecks_basis_and_parent_path(self):
        for scenario in ("action", "reparent"):
            with self.subTest(scenario=scenario):
                self.tearDown()
                self.setUp()
                self.b.desktop.covered = True
                self.b.desktop.assist = True
                facts = await self.selected()
                original = self.b.desktop._request_activation

                def activate(window, ctx, *, original=original, scenario=scenario):
                    original(window, ctx)
                    self.mutate(scenario)

                with patch.object(self.b.desktop, "_request_activation", side_effect=activate):
                    result = await self.send(facts, foreground=True)
                self.assertEqual(result.dispatch.value, "notDispatched")
                self.assertEqual(result.preparation, ("foreground",))
                self.assertIsNotNone(result.error_code)
                self.assertEqual(self.b.desktop.events, [])

    async def test_successful_preparation_reobserves_same_member_and_current_owner_geometry(self):
        self.b.desktop.covered = True
        self.b.desktop.assist = True
        self.port.policy = HostPolicy("shared", True)
        original = self.b.desktop._request_activation

        def activate(window, ctx):
            original(window, ctx)
            self.owner.rect = self.layout.rect = (300, 50, 100, 60)
            self.label.rect = (340, 70, 50, 30)

        with patch.object(self.b.desktop, "_request_activation", side_effect=activate):
            result = await Engine(self.port).execute(Intent("click", {"button": "right"}), self.binding)
        self.assertEqual(result["status"], "dispatched")
        self.assertEqual([(c["kind"], c["foreground"]) for c in self.commands()], [("pointer_sequence", True)])
        self.assertEqual(self.b.desktop.events, [(6, 0, 350, 80), (4, 3, 0, 0), (5, 3, 0, 0)])
        selections = [p["query"] for m, p in self.connection.requests if m == "inspect" and "actuator" in p["query"]]
        self.assertEqual(len(selections), 1, "The native execute owns re-observation after preparation.")
        self.assertEqual(selections[0]["actuator"]["node"], self.case.node(self.label))
        self.assertEqual(self.b.desktop.keyboard_window, 22, "Pointer readiness does not require keyboard focus.")

    async def test_motion_or_first_press_cannot_authorize_changed_member(self):
        for trigger in (6, 4):
            for scenario in ("action", "reparent", "replace", "geometry"):
                with self.subTest(trigger=trigger, scenario=scenario):
                    self.tearDown()
                    self.setUp()
                    fired = False

                    def event(kind, *_, scenario=scenario, trigger=trigger):
                        nonlocal fired
                        if kind == trigger and not fired:
                            fired = True
                            self.mutate(scenario)

                    self.b.desktop.event_hook = event
                    with self.assertRaises(ComputerUseError) as error:
                        await Engine(self.port).execute(Intent("click", {"count": 2}), self.binding)
                    self.assertEqual(error.exception.effect, "partial")
                    self.assertEqual(self.kinds(), [6] if trigger == 6 else [6, 4, 5])
                    self.assertFalse(self.b.desktop.buttons)
                    self.assertEqual(len(self.commands()), 1)

    async def test_changed_hit_with_same_geometry_never_dispatches_to_replacement(self):
        facts = await self.selected()
        self.b.hit_overrides[self.layout] = self.b.canvas
        result = await self.send(facts)
        self.assertEqual(result.dispatch.value, "notDispatched")
        self.assertEqual(self.b.desktop.events, [])

    async def test_same_member_label_and_geometry_can_change_before_delivery(self):
        facts = await self.selected()
        self.label.name = "New caption"
        self.owner.rect = self.layout.rect = (300, 50, 100, 60)
        self.label.rect = (340, 70, 50, 30)
        result = await self.send(facts)
        self.assertEqual(result.dispatch.value, "dispatched")
        self.assertEqual(self.b.desktop.events[0], (6, 0, 350, 80))

    async def test_scroll_key_and_text_cannot_reuse_control_part_relation(self):
        await self.selected()
        actuator, basis = self.actuator.wire(), [item.wire() for item in self.basis]
        await self.port.finish(self.operation)
        for kind, parameters in (
            ("scroll", dict(direction="down", pages=1)),
            ("key", dict(key="Return")),
            ("insert", dict(text="x")),
        ):
            self.operation = (await self.port.begin(Intent(kind, parameters), self.binding, 3000)).token
            with self.assertRaisesRegex(ComputerUseError, "addressed element click"):
                await self.connection.request(
                    "inspect",
                    dict(
                        operation=self.operation,
                        target=encode_target(self.binding),
                        query=dict(kind="inputContext", within=[], actuator=actuator, basis=basis),
                    ),
                    dict(timeoutMs=3000),
                )
            await self.port.finish(self.operation)
        self.assertEqual(self.b.desktop.events, [])

    async def test_scroll_does_not_gain_member_resolution(self):
        with self.assertRaises(ComputerUseError):
            await Engine(self.port).execute(Intent("scroll", dict(direction="down", pages=0.5)), self.binding)
        self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(self.commands(), [])


if __name__ == "__main__":
    unittest.main()
