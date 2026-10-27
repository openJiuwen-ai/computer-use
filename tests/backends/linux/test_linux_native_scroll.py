"""Public Python scroll selection and production Linux pointer contracts.

Only AT-SPI/X11 are substituted. Real toolkit acceptance records viewport
changes separately; successful event dispatch here does not prove UI effects.
"""

import unittest
from dataclasses import replace

from linux_native_provider import Node

import backends.linux.test_linux_native_element_pointer as pointer_tests
import backends.linux.test_linux_native_element_pointer_parts as parts
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.interaction.write.resolution.scroll import ScrollPointerQuery
from computer_use.native.contracts import HostPolicy, Intent, UnitCommand
from computer_use.native.transport import ConnectionPort, decode_binding, encode_target
from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.runtime.native import query


class LinuxScrollTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.case = pointer_tests.LinuxNativeElementPointerTests()
        self.case.setUp()
        self.b, self.driver = self.case.b, self.case.driver
        self.region, self.content = self.b.group, self.b.canvas
        self.region.role, self.content.role = "scroll pane", "text"
        self.region.name, self.content.name = "Viewport", "Editable document"
        self.content.rect = (30, 30, 300, 210)
        self.content.states.update(("EDITABLE", "FOCUSABLE"))
        self.requested = Node("label", "Requested paragraph", parent=self.content, interfaces=["Component"])
        self.requested.rect = (50, 50, 80, 15)
        self.requested.states.add("VISIBLE")
        self.content.children = [self.requested]
        self.binding = decode_binding(dict(self.case.target, node=self.case.node(self.requested)))
        self.connection = parts.DriverConnection(self.driver)
        self.port = ConnectionPort(self.connection, HostPolicy())

    def tearDown(self):
        self.case.tearDown()

    def events(self):
        return [event[0] for event in self.b.desktop.events]

    def commands(self):
        return [p["command"] for method, p in self.connection.requests if method == "execute"]

    async def selected(self):
        self.intent = Intent("scroll", dict(direction="down", pages=0.5))
        self.operation = (await self.port.begin(self.intent, self.binding, 5000)).token
        initial = ScrollPointerQuery(self.port, self.operation, self.binding, lambda: 5000)
        area = await initial.resolve()
        self.assertEqual(area.region, self.case.node(self.region))
        facts = await self.port.inspect(self.operation, self.binding, 5000, actuator=area, basis=initial.basis)
        self.assertEqual(facts.input.pointer_hit, self.case.node(self.content))
        query_value = ScrollPointerQuery(self.port, self.operation, self.binding, lambda: 5000)
        self.actuator = await query_value.resolve(facts.input.pointer_hit)
        self.basis = query_value.basis
        self.assertEqual(self.actuator.node, self.case.node(self.content))
        return await self.port.inspect(self.operation, self.binding, 5000, actuator=self.actuator, basis=self.basis)

    async def send(self, facts):
        return await self.port.execute(
            self.operation, 1, UnitCommand(facts.primitives[0], self.binding, self.intent, self.actuator), 5000
        )

    async def test_background_scroll_uses_nearest_viewport_not_requested_label_bounds(self):
        for direction, button in (("down", 5), ("up", 4), ("left", 6), ("right", 7)):
            with self.subTest(direction=direction):
                self.b.desktop.events.clear()
                await Engine(self.port).execute(Intent("scroll", dict(direction=direction, pages=0.25)), self.binding)
                self.assertEqual(
                    self.b.desktop.events,
                    [(6, 0, 195, 145), (4, button, 0, 0), (5, button, 0, 0), (4, button, 0, 0), (5, button, 0, 0)],
                )
                self.assertFalse(self.b.desktop.activations)
                self.assertFalse(self.b.desktop.buttons)
                self.assertEqual(self.commands()[-1]["target"], encode_target(self.binding))

    async def test_requested_region_itself_is_valid_and_keeps_its_original_binding(self):
        target = replace(self.binding, node=self.case.node(self.region))
        await Engine(self.port).execute(Intent("scroll", dict(direction="down", pages=0.125)), target)
        self.assertEqual(self.events(), [6, 4, 5])
        self.assertEqual(self.commands()[0]["target"], encode_target(target))

    async def test_nested_scroller_or_independent_control_is_not_substituted_for_outer_viewport(self):
        target = replace(self.binding, node=self.case.node(self.region))
        for role in ("scroll pane", "spin button", "combo box", "slider", "push button", "unknown widget"):
            with self.subTest(role=role):
                self.content.role = role
                with self.assertRaises(ComputerUseError):
                    await Engine(self.port).execute(Intent("scroll", dict(direction="down", pages=0.5)), target)
                self.assertEqual(self.events(), [])

    async def test_requested_inner_content_uses_its_nearest_nested_region(self):
        self.content.role = "scroll pane"
        self.content.children = []
        target = replace(self.binding, node=self.case.node(self.content))
        await Engine(self.port).execute(Intent("scroll", dict(direction="down", pages=0.125)), target)
        self.assertEqual(self.b.desktop.events[0], (6, 0, 180, 135))

    async def test_sensitive_without_enabled_remains_usable(self):
        self.region.states.discard("ENABLED")
        await Engine(self.port).execute(Intent("scroll", dict(direction="down", pages=0.125)), self.binding)
        self.assertEqual(self.events(), [6, 4, 5])

    async def test_addressed_generic_container_can_scroll_its_passive_content(self):
        self.region.role, self.content.role = "panel", "label"
        self.content.states.difference_update(("EDITABLE", "FOCUSABLE"))
        target = replace(self.binding, node=self.case.node(self.region))
        await Engine(self.port).execute(Intent("scroll", dict(direction="down", pages=0.25)), target)
        self.assertEqual(self.events(), [6, 4, 5, 4, 5])
        self.assertEqual(self.commands()[0]["target"], encode_target(target))

    async def test_generic_ancestor_is_not_invented_as_a_descendants_scroller(self):
        self.region.role = "panel"
        intent = Intent("scroll", dict(direction="down", pages=0.5))
        operation = (await self.port.begin(intent, self.binding, 5000)).token
        query_value_local = ScrollPointerQuery(self.port, operation, self.binding, lambda: 5000)
        self.assertIsNone(await query_value_local.resolve())
        self.assertEqual(self.events(), [])

    async def test_explicit_container_keeps_nested_scroller_and_control_boundaries(self):
        self.region.role = "panel"
        target = replace(self.binding, node=self.case.node(self.region))
        for role in ("scroll pane", "spin button", "push button", "unknown widget"):
            with self.subTest(role=role):
                self.content.role = role
                with self.assertRaises(ComputerUseError):
                    await Engine(self.port).execute(Intent("scroll", dict(direction="down", pages=0.5)), target)
                self.assertEqual(self.events(), [])

    async def test_disabled_original_cannot_borrow_enabled_ancestor(self):
        self.requested.states.difference_update(("ENABLED", "SENSITIVE"))
        with self.assertRaises(ComputerUseError):
            await Engine(self.port).execute(Intent("scroll", dict(direction="down", pages=0.5)), self.binding)
        self.assertEqual(self.events(), [])

    async def test_selected_evidence_is_live_but_content_text_is_not_a_guard(self):
        facts = await self.selected()
        # The native seam's wheel recipient is a numeric event counter.
        self.content.text = "12"
        self.requested.name = "New paragraph name"
        result = await self.send(facts)
        self.assertEqual(result.dispatch.value, "dispatched")
        self.assertEqual(self.events(), [6] + [4, 5] * 4)

    async def test_changed_role_or_ancestor_after_motion_stops_before_wheel_without_replay(self):
        for mutation in ("role", "parent"):
            with self.subTest(mutation=mutation):
                self.content.role, self.content.parent = "text", self.region
                self.b.desktop.events.clear()
                count = len(self.commands())

                def changed(kind, *args, mutation=mutation):
                    if kind == 6:
                        if mutation == "role":
                            self.content.role = "spin button"
                        else:
                            self.content.parent = self.b.window

                self.b.desktop.event_hook = changed
                with self.assertRaises(ComputerUseError) as error:
                    await Engine(self.port).execute(Intent("scroll", dict(direction="down", pages=0.5)), self.binding)
                self.assertEqual(error.exception.effect, "possible")
                self.assertEqual(self.events(), [6])
                self.assertEqual(len(self.commands()), count + 1)
                self.assertFalse(self.b.desktop.buttons)

    async def test_same_geometry_different_hit_is_not_a_permanent_scroll_permission(self):
        facts = await self.selected()
        self.b.hit_overrides[self.region] = self.b.button
        result = await self.send(facts)
        self.assertEqual(result.dispatch.value, "notDispatched")
        self.assertEqual(self.events(), [])

    async def test_scroll_contract_cannot_be_used_by_click_or_typing(self):
        await self.selected()
        actuator, basis = self.actuator.wire(), [entry.wire() for entry in self.basis]
        await self.port.finish(self.operation)
        for intent in (Intent("click", {}), Intent("key", dict(key="Return"))):
            operation = (await self.port.begin(intent, self.binding, 5000)).token
            with self.assertRaisesRegex(ComputerUseError, "addressed element scroll"):
                await self.connection.request(
                    "inspect",
                    dict(
                        operation=operation,
                        target=encode_target(self.binding),
                        query=dict(kind="inputContext", within=[], actuator=actuator, basis=basis),
                    ),
                    dict(timeoutMs=5000),
                )
            await self.port.finish(operation)
        self.assertEqual(self.events(), [])

    async def test_native_decoder_requires_fixed_region_and_complete_basis(self):
        await self.selected()
        correct = dict(
            kind="inputContext", within=[], actuator=self.actuator.wire(), basis=[d.wire() for d in self.basis]
        )
        self.assertEqual(query(correct), correct)
        for actuator in (
            {"node": self.actuator.node, "relation": "scrollPointer"},
            dict(self.actuator.wire(), region=""),
            dict(self.actuator.wire(), x=100),
        ):
            with self.subTest(actuator=actuator), self.assertRaises(Failure):
                query(dict(correct, actuator=actuator))

    def table_rows(self):
        self.content.role = "table"
        rows = []
        for index in range(6):
            row = Node("list item", f"Row {index}", parent=self.content, interfaces=["Component"])
            cell = Node("table cell", f"Cell {index}", parent=row, interfaces=["Component", "Action"])
            row.rect = cell.rect = self.content.rect
            row.states.add("VISIBLE")
            cell.states.add("VISIBLE")
            cell.actions = [(name, name) for name in ("expand or contract", "edit", "activate")]
            row.children = [cell]
            rows.append((row, cell))
        self.content.children = [rows[0][0]]
        return rows, replace(self.binding, node=self.case.node(self.region))

    async def test_moving_rows_keep_fixed_collection_and_equivalent_raw_structure(self):
        rows, target = self.table_rows()
        index = 0

        def move(kind, *args):
            nonlocal index
            if kind == 5:
                old_row, old_cell = rows[index]
                old_row.dead = old_cell.dead = True
                index += 1
                self.content.children = [rows[index][0]]

        self.b.desktop.event_hook = move
        result = await Engine(self.port).execute(Intent("scroll", dict(direction="down", pages=0.5)), target)
        self.assertEqual(result["status"], "dispatched")
        self.assertEqual(self.events(), [6] + [4, 5] * 4)
        self.assertEqual(len(self.commands()), 1)
        selected = [
            p["query"]["actuator"]
            for method, p in self.connection.requests
            if method == "inspect" and p["query"].get("actuator", {}).get("contentPath")
        ]
        self.assertEqual(
            selected[-1]["contentPath"],
            [self.case.node(rows[0][1]), self.case.node(rows[0][0]), self.case.node(self.content)],
        )
        self.assertEqual(self.commands()[0]["target"], encode_target(target))

    async def test_moving_content_permission_starts_after_first_wheel_not_after_motion(self):
        rows, target = self.table_rows()
        self.b.desktop.event_hook = lambda kind, *args: (
            setattr(self.content, "children", [rows[1][0]]) if kind == 6 else None
        )
        with self.assertRaises(ComputerUseError) as error:
            await Engine(self.port).execute(Intent("scroll", dict(direction="down", pages=0.5)), target)
        self.assertEqual(error.exception.effect, "possible")
        self.assertEqual(self.events(), [6])

    async def test_moving_content_cannot_enter_other_control_or_unread_actions(self):
        for mutation in ("button", "slider", "action", "unread", "parent"):
            with self.subTest(mutation=mutation):
                self.b.desktop.events.clear()
                rows, target = self.table_rows()
                row, cell = rows[1]
                if mutation == "button":
                    cell.role = "push button"
                elif mutation == "slider":
                    cell.role = "slider"
                elif mutation == "action":
                    cell.actions.append(("increment", "Change value"))
                elif mutation == "unread":
                    cell.errors["get_n_actions"] = self.b.ax.GLib.Error("Unread actions")
                else:
                    row.parent = self.region
                self.b.desktop.event_hook = lambda kind, *args, row=row: (
                    setattr(self.content, "children", [row]) if kind == 5 else None
                )
                before = len(self.commands())
                with self.assertRaises(ComputerUseError) as error:
                    await Engine(self.port).execute(Intent("scroll", dict(direction="down", pages=0.5)), target)
                self.assertEqual(error.exception.effect, "possible")
                self.assertEqual(self.events(), [6, 4, 5])
                self.assertEqual(len(self.commands()), before + 1)
                self.assertFalse(self.b.desktop.buttons)

    async def test_replacing_collection_with_lookalike_is_not_scrolling_contents(self):
        rows, target = self.table_rows()
        replacement = Node("table", parent=self.region, interfaces=["Component"])
        replacement.rect = self.content.rect
        replacement.states.add("VISIBLE")
        rows[1][0].parent = replacement
        replacement.children = [rows[1][0]]
        self.b.desktop.event_hook = lambda kind, *args: (
            setattr(self.region, "children", [replacement]) if kind == 5 else None
        )
        with self.assertRaises(ComputerUseError):
            await Engine(self.port).execute(Intent("scroll", dict(direction="down", pages=0.5)), target)
        self.assertEqual(self.events(), [6, 4, 5])

    async def test_explicit_collection_without_scroll_pane_uses_same_content_contract(self):
        rows, _ = self.table_rows()
        self.region.role = "panel"
        target = replace(self.binding, node=self.case.node(self.content))
        self.b.desktop.event_hook = lambda kind, *args: (
            setattr(self.content, "children", [rows[1][0]]) if kind == 5 else None
        )
        await Engine(self.port).execute(Intent("scroll", dict(direction="down", pages=0.5)), target)
        self.assertEqual(self.events(), [6] + [4, 5] * 4)

    async def test_action_classification_keeps_original_provider_spelling_in_basis(self):
        rows, target = self.table_rows()
        for _, cell in rows:
            cell.actions = [("Toggle", "Toggles the state")]
        self.b.desktop.event_hook = lambda kind, *args: (
            setattr(self.content, "children", [rows[1][0]]) if kind == 5 else None
        )
        await Engine(self.port).execute(Intent("scroll", dict(direction="down", pages=0.5)), target)
        self.assertEqual(self.events(), [6] + [4, 5] * 4)
        self.assertEqual(rows[1][1].actions, [("Toggle", "Toggles the state")])

    async def test_content_path_requires_collected_roles_actions_and_parent_chain(self):
        rows, target = self.table_rows()
        intent = Intent("scroll", dict(direction="down", pages=0.5))
        operation = (await self.port.begin(intent, target, 5000)).token
        query_ = ScrollPointerQuery(self.port, operation, target, lambda: 5000)
        selected = await query_.resolve(self.case.node(rows[0][1]))
        self.assertTrue(selected.content_path)
        for kind in ("role", "actions", "parent", "wrong-order"):
            basis = [b.wire() for b in query_.basis]
            actuator = selected.wire()
            for b in basis:
                if b["node"] == actuator["node"]:
                    if kind == "role":
                        b["fields"] = [f for f in b["fields"] if f != "role"]
                    elif kind == "actions":
                        b["actions"] = False
                    elif kind == "parent":
                        b["relations"] = []
            if kind == "wrong-order":
                actuator["contentPath"] = [actuator["node"], self.case.node(self.region), self.case.node(self.content)]
            with self.subTest(kind=kind), self.assertRaises(ComputerUseError):
                await self.connection.request(
                    "inspect",
                    dict(
                        operation=operation,
                        target=encode_target(target),
                        query=dict(kind="inputContext", within=[], actuator=actuator, basis=basis),
                    ),
                    dict(timeoutMs=5000),
                )
        self.assertEqual(self.events(), [])


if __name__ == "__main__":
    unittest.main()
