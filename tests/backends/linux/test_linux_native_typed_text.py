"""Linux typed text through production v5 units, policy and X11 event sender.

Only native map/state, AT-SPI objects and received X events are substituted.
These tests do not establish actual provider or desktop compatibility.
"""

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fixture_driver import environmentDriver
from linux_native_provider import NativeError, Node, TypedPointerBackend

import backends.linux.test_linux_native as base
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


class LinuxNativeTypedTextTests(unittest.TestCase):
    def setUp(self):
        self.b = TypedPointerBackend()
        self.b.desktop.assist = False
        self.driver = NativeExecution(self.b, "session")
        self.pid = patch("computer_use_linux.windowing.native_directory.process_start", return_value="generation")
        self.pid.start()
        application = self.driver.directory.applications(base.context())["applications"][0]["id"]
        row = self.driver.directory.read(application, base.context())["surfaces"][0]
        self.target = base.scope(instance=row["instance"], rootWindow=row["window"], surface=row["surface"])
        self.ready()

    def tearDown(self):
        self.driver.close()
        self.pid.stop()

    def ready(self):
        self.b.desktop.foreground = self.b.desktop.keyboard_window = 11
        self.b.editor.states.add("FOCUSED")
        self.b.other_editor.states.discard("FOCUSED")

    def setup_text(self, text="hi", kind="insert", *, app=False, within=()):
        self.selected = dict(
            self.target, node=None if app else self.driver.store.keep(self.target["surface"], self.b.editor)
        )
        self.within = [self.driver.store.keep(self.target["surface"], obj) for obj in within]
        self.operation = self.driver.perform(
            "begin",
            dict(mode="write", target=self.selected, intent=dict(kind=kind, parameters=dict(text=text))),
            base.context(),
        )["operation"]
        return self.inspect()

    def inspect(self, ctx=None):
        return decodeExecutionResult(
            "inspect",
            self.driver.perform(
                "inspect",
                dict(
                    operation=self.operation, target=self.selected, query=dict(kind="inputContext", within=self.within)
                ),
                ctx or base.context(),
            ),
        )

    def setup_contained_text(self, text="new", kind="replace"):
        """Use collected native facts to select the existing child, not a fake contract."""
        self.b.ax.A.StateType.READ_ONLY = "READ_ONLY"
        self.owner = Node(
            "combo box", "Composite", children=[self.b.editor], parent=self.b.window, interfaces=["Component", "Action"]
        )
        self.b.editor.parent = self.owner
        self.b.window.children[0] = self.owner
        self.selected = dict(self.target, node=self.driver.store.keep(self.target["surface"], self.owner))
        self.editor_ref = self.driver.store.keep(self.target["surface"], self.b.editor)
        self.within = []
        self.operation = self.driver.perform(
            "begin",
            dict(mode="write", target=self.selected, intent=dict(kind=kind, parameters=dict(text=text))),
            base.context(),
        )["operation"]
        batch = base.read_graph(
            self.driver.perform(
                "inspect",
                dict(
                    operation=self.operation,
                    target=self.selected,
                    query=dict(
                        kind="nodes",
                        fields=["role", "enabled", "editable", "focusable", "readOnly"],
                        maxNodes=100,
                        maxDepth=4,
                    ),
                ),
                base.context(),
            )
        )
        relations = {}
        for edge in batch["relations"]:
            if edge["property"] in ("parent", "children"):
                relations.setdefault(edge["source"], []).append(edge["property"])
        query = dict(
            kind="inputContext",
            within=[],
            actuator=dict(node=self.editor_ref, relation="containedEditor"),
            basis=[
                dict(
                    observed=batch["observed"]["id"],
                    node=n["node"],
                    fields=n["requestedFields"],
                    actions=True,
                    relations=relations.get(n["node"], []),
                )
                for n in batch["nodes"]
            ],
        )
        return decodeExecutionResult(
            "inspect",
            self.driver.perform(
                "inspect", dict(operation=self.operation, target=self.selected, query=query), base.context()
            ),
        )

    @staticmethod
    def typed(facts):
        return next(p for p in facts["primitives"] if p["unit"] == "key_sequence")

    def command(self, facts, *, prepare=False, foreground=False, unit="key_sequence"):
        p = next(p for p in facts["primitives"] if p["unit"] == unit)
        return dict(
            kind="prepare_receiver" if prepare else unit,
            primitiveId=p["id"],
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

    def setters(self):
        return [c for c in self.b.ax.calls if c[0] in ("add_selection", "set_selection")]

    def writes(self):
        return [c for c in self.b.ax.calls if c[0] in ("insert_text", "delete_text", "set_text_contents")]

    def focus_calls(self):
        return [c for c in self.b.ax.calls if c[0] == "grab_focus"]

    def hook(self, callback):
        previous = self.b.desktop.event_hook
        self.b.desktop.event_hook = lambda kind, detail: (previous(kind, detail), callback(kind, detail))

    def drift(self):
        self.b.editor.states.discard("FOCUSED")
        self.b.other_editor.states.add("FOCUSED")
        self.b.desktop.foreground = self.b.desktop.keyboard_window = 22

    def test_insert_uses_current_caret_without_replacement_or_native_editabletext(self):
        facts = self.setup_text("Hi!")
        self.assertEqual(self.typed(facts)["readiness"], "yes")
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["cleanup"]), ("dispatched", "complete"))
        self.assertEqual(self.b.editor.text, "abHi!cd")
        self.assertEqual(self.b.other_editor.text, "untouched")
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.setters(), [])
        self.assertEqual(self.focus_calls(), [])
        self.assertEqual(self.b.desktop.activations, [])
        self.assertFalse(self.b.desktop.keys)

    def test_insert_replaces_existing_native_selection_only(self):
        self.b.editor.selections = [(1, 3)]
        facts = self.setup_text("X")
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.assertEqual(self.b.editor.text, "aXd")
        self.assertEqual(self.setters(), [])

    def test_app_insert_does_not_require_accessibility_root(self):
        facts = self.setup_text("q", app=True)
        self.b.desktop.items[0].accessible = None
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.assertEqual(self.b.editor.text, "abqcd")
        self.assertEqual(self.focus_calls(), [])

    def test_no_text_interface_can_insert_but_not_replace(self):
        self.b.editor.interfaces.remove("Text")
        facts = self.setup_text("q")
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.finish()
        facts = self.setup_text("q", "replace")
        self.assertEqual(self.typed(facts)["supported"], "no")
        self.assertEqual(self.b.editor.text, "abqcd")

    def opaque_spin(self, text="1.25"):
        self.b.mode = "exclusive"
        self.b.ax.A.StateType.READ_ONLY = "READ_ONLY"
        self.b.editor.role = "spin button"
        self.b.editor.interfaces = ["Component", "Value"]
        self.b.editor.text, self.b.editor.caret, self.b.editor.selections = text, len(text), []
        self.b.editor.value = 125
        self.edits, self.commits = [], []
        previous = self.b.desktop.event_hook

        def event(kind, detail):
            before = self.b.editor.text
            previous(kind, detail)
            if kind != self.b.desktop.X.KeyPress or "FOCUSED" not in self.b.editor.states:
                return
            if self.b.editor.text != before:
                self.edits.append(self.b.editor.text)
            if detail == "Return":
                # A scaled display and interaction-only callback are native
                # provider behavior. Neither is inferred by the input unit.
                from decimal import Decimal

                self.b.editor.value = int(Decimal(self.b.editor.text) * 100)
                self.commits.append(self.b.editor.value)

        self.b.desktop.event_hook = event
        self.b.ax.A.Value.set_current_value = lambda *args: (_ for _ in ()).throw(
            AssertionError("Keyboard editing must not turn into numeric property replacement")
        )

    def test_opaque_spin_replace_uses_real_editor_and_preserves_display_conversion_callbacks(self):
        self.opaque_spin()
        facts = self.setup_text("2.34", "replace")
        self.assertEqual((self.typed(facts)["supported"], facts["input"]["selectionKnown"]), ("yes", "unknown"))
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["confirmation"]), ("dispatched", "unavailable"))
        self.assertEqual(result["preparation"], ["pointerFocus", "keyboardSelection"])
        self.assertIn("keyboardSelection", result["preparation"])
        self.assertIn("no observable text selection range", result["detail"])
        self.assertEqual(self.b.editor.text, "2.34")
        self.assertTrue(self.edits)
        self.assertEqual((self.b.editor.value, self.commits), (125, []), "Typing does not silently submit")
        self.assertEqual(self.b.other_editor.text, "untouched")
        self.assertEqual(self.setters(), [])
        self.assertFalse(
            any(
                name.startswith("get_")
                and obj is self.b.editor
                and name in ("get_text", "get_character_count", "get_n_selections", "get_selection")
                for name, obj, _ in self.b.ax.calls
            )
        )
        self.finish()
        self.operation = self.driver.perform(
            "begin",
            dict(mode="write", target=self.selected, intent=dict(kind="key", parameters=dict(key="Return"))),
            base.context(),
        )["operation"]
        self.assertEqual(self.send(self.command(self.inspect()))["dispatch"], "dispatched")
        self.assertEqual((self.b.editor.value, self.commits), (234, [234]))

    def test_opaque_spin_replacement_clicks_original_control_before_selecting_or_typing(self):
        self.opaque_spin()
        self.b.editor.states.discard("FOCUSED")
        facts = self.setup_text("84", "replace")
        self.assertEqual(self.typed(facts)["readiness"], "yes", "The recipe can establish control focus itself")
        result = self.send(self.command(facts))
        self.assertEqual(result["dispatch"], "dispatched")
        self.assertEqual(self.b.editor.text, "84")
        self.assertEqual(self.b.desktop.events[:3], [(6, 0, 90, 80), (4, 1, 0, 0), (5, 1, 0, 0)])
        self.assertEqual(self.b.desktop.events[3:7], [(2, "ctrl"), (2, "a"), (3, "a"), (3, "ctrl")])
        self.assertEqual(self.focus_calls(), [])
        self.assertEqual(self.b.other_editor.text, "untouched")

    def test_opaque_spin_real_pointer_and_text_context_supports_following_element_return(self):
        self.opaque_spin()
        facts = self.setup_text("2.34", "replace")
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.finish()
        stale = Node(
            "page tab",
            "Other view",
            parent=self.b.window,
            states={"ENABLED", "SHOWING", "VISIBLE", "FOCUSED", "SELECTED"},
        )
        stale.rect = (-400, -200, 60, 30)
        self.b.window.children.insert(0, stale)
        self.operation = self.driver.perform(
            "begin",
            dict(mode="write", target=self.selected, intent=dict(kind="key", parameters=dict(key="Return"))),
            base.context(),
        )["operation"]
        result = self.send(self.command(self.inspect()))
        self.assertEqual(result["dispatch"], "dispatched")
        self.assertEqual((self.b.editor.value, self.commits), (234, [234]))
        self.assertEqual(self.b.other_editor.text, "untouched")

    def test_opaque_spin_pointer_replacement_clears_independent_host_focus(self):
        self.opaque_spin()
        self.b.other_editor.parent = self.b.window
        self.b.other_editor.states.add("FOCUSED")
        self.b.window.children.insert(0, self.b.other_editor)
        self.b.other_editor.rect = (300, 50, 100, 60)
        facts = self.setup_text("84", "replace")
        result = self.send(self.command(facts))
        self.assertEqual(result["dispatch"], "dispatched")
        self.assertEqual(self.b.editor.text, "84")
        self.assertEqual(self.b.other_editor.text, "untouched")
        self.assertNotIn("FOCUSED", self.b.other_editor.states)

    def test_opaque_spin_pointer_replacement_does_not_scan_unrelated_window_content(self):
        self.opaque_spin()
        facts = self.setup_text("84", "replace")
        self.b.window.children[:0] = [Node("label", str(i), parent=self.b.window) for i in range(450)]
        self.b.window.errors["get_child_count"] = NativeError(
            "Pointer preparation already addressed the original control"
        )
        result = self.send(self.command(facts))
        self.assertEqual(result["dispatch"], "dispatched")
        self.assertEqual(self.b.editor.text, "84")
        self.assertEqual(self.b.other_editor.text, "untouched")

    def test_opaque_spin_independent_focus_after_pointer_stops_before_select_all(self):
        self.opaque_spin()
        facts = self.setup_text("84", "replace")
        self.b.other_editor.parent = self.b.window

        def focus(kind, detail):
            if kind == 5:
                self.b.other_editor.states.add("FOCUSED")
                self.b.ax.focus_events = {self.b.ax.native_key(self.b.other_editor): self.b.other_editor}

        self.hook(focus)
        result = self.send(self.command(facts))
        self.assertEqual(
            (result["dispatch"], result["errorCode"], result["retryReason"]), ("unknown", "input_unavailable", None)
        )
        self.assertFalse(any(event[0] == 2 for event in self.b.desktop.events))
        self.assertEqual(self.b.editor.text, "1.25")
        self.assertEqual(self.b.other_editor.text, "untouched")

    def test_opaque_spin_pointer_focus_requires_mode_authorization(self):
        self.opaque_spin()
        self.b.mode = "shared"
        facts = self.setup_text("84", "replace")
        self.assertEqual(self.typed(facts)["supported"], "no")
        self.assertEqual(self.b.desktop.events, [])
        self.b.mode = "exclusive"
        facts = self.inspect()
        self.b.mode = "shared"
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["errorCode"]), ("notDispatched", "mode_unsupported"))
        self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(self.b.editor.text, "1.25")

    def test_opaque_spin_shared_authorized_pointer_never_activates_another_window(self):
        self.opaque_spin()
        self.b.mode, self.b.desktop.assist = "shared", True
        facts = self.setup_text("84", "replace")
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.assertEqual(self.b.editor.text, "84")
        self.assertEqual(self.b.desktop.activations, [])

    def test_opaque_spin_unknown_hit_or_covered_geometry_never_clicks_or_types(self):
        for problem in ("other-hit", "covered", "hidden", "out-of-window"):
            with self.subTest(problem=problem):
                self.opaque_spin()
                facts = self.setup_text("84", "replace")
                if problem == "other-hit":
                    self.b.hit_overrides[self.b.window] = self.b.button
                elif problem == "covered":
                    self.b.desktop.covered = True
                elif problem == "hidden":
                    self.b.editor.states.discard("VISIBLE")
                else:
                    self.b.editor.rect = (800, 600, 100, 60)
                result = self.send(self.command(facts))
                self.assertEqual(result["dispatch"], "notDispatched")
                self.assertEqual(self.b.desktop.events, [])
                self.assertEqual(self.b.editor.text, "1.25")
                self.finish()
                self.b.hit_overrides.clear()
                self.b.desktop.covered = False
                self.b.editor.states.add("VISIBLE")
                self.b.editor.rect = (40, 50, 100, 60)

    def test_opaque_spin_geometry_callback_cannot_switch_keyboard_window_before_click(self):
        self.opaque_spin()
        facts = self.setup_text("84", "replace")
        self.b.ax.hooks["get_extents"] = lambda *args: self.drift()
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["errorCode"]), ("notDispatched", "input_unavailable"))
        self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(self.b.other_editor.text, "untouched")

    def test_opaque_spin_motion_effect_stops_changed_geometry_without_replay(self):
        self.opaque_spin()
        facts = self.setup_text("84", "replace")
        command = self.command(facts)
        self.hook(lambda kind, detail: setattr(self.b.editor, "rect", (100, 100, 100, 60)) if kind == 6 else None)
        result = self.send(command)
        self.assertEqual(
            (result["dispatch"], result["errorCode"], result["retryReason"], result["cleanup"]),
            ("unknown", "input_unavailable", None, "complete"),
        )
        self.assertEqual(self.b.desktop.events, [(6, 0, 90, 80)])
        self.assertEqual(result["preparation"], ["pointerFocus"])
        self.assertEqual(self.send(command), result)
        with self.assertRaises(Failure):
            self.send(command, 2)
        self.assertEqual(self.b.editor.text, "1.25")

    def test_opaque_spin_defunct_after_click_never_selects_or_types(self):
        self.opaque_spin()
        facts = self.setup_text("84", "replace")
        self.hook(lambda kind, detail: setattr(self.b.editor, "dead", True) if kind == 5 else None)
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["retryReason"], result["cleanup"]), ("unknown", None, "complete"))
        self.assertEqual(self.b.desktop.events, [(6, 0, 90, 80), (4, 1, 0, 0), (5, 1, 0, 0)])
        self.assertEqual(self.b.editor.text, "1.25")

    def test_opaque_spin_post_click_focus_failure_never_fires_global_select_all(self):
        self.opaque_spin()
        facts = self.setup_text("84", "replace")
        self.hook(lambda kind, detail: self.b.editor.states.discard("FOCUSED") if kind == 5 else None)
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["retryReason"]), ("unknown", None))
        self.assertFalse(any(event[0] == 2 for event in self.b.desktop.events))
        self.assertEqual(self.b.editor.text, "1.25")

    def test_opaque_spin_fresh_keyboard_mapping_failure_precedes_all_pointer_input(self):
        self.opaque_spin()
        facts = self.setup_text("84", "replace")
        self.b.desktop.keymap_missing.add("4")
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["errorCode"]), ("notDispatched", "input_unavailable"))
        self.assertEqual(self.b.desktop.events, [])

    def test_opaque_spin_does_not_insert_at_an_unobservable_caret_and_empty_replace_sends_backspace(self):
        self.opaque_spin("12")
        self.b.editor.caret = 1
        facts = self.setup_text("3")
        self.assertEqual(self.typed(facts)["supported"], "no")
        self.assertEqual(self.b.editor.text, "12")
        self.assertEqual(self.b.desktop.events, [])
        self.finish()
        self.b.desktop.events.clear()
        facts = self.setup_text("", "replace")
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], self.b.editor.text), ("dispatched", ""))
        self.assertEqual([event[1] for event in self.b.desktop.events if event[0] == 2], ["ctrl", "a", "BackSpace"])
        self.assertEqual(self.commits, [])

    def test_opaque_spin_readonly_and_missing_component_do_not_grant_replacement(self):
        self.opaque_spin()
        self.b.editor.states.add("READ_ONLY")
        facts = self.setup_text("2", "replace")
        self.assertEqual(self.typed(facts)["supported"], "no")
        self.assertEqual(self.b.desktop.events, [])
        self.finish()
        self.b.editor.states.discard("READ_ONLY")
        self.b.editor.interfaces.remove("Component")
        self.b.editor.states.discard("FOCUSED")
        facts = self.setup_text("2", "replace")
        self.assertEqual(self.typed(facts)["supported"], "no")
        self.assertEqual(self.b.editor.text, "1.25")

    def test_opaque_spin_known_text_selection_failure_does_not_use_shortcut(self):
        self.opaque_spin()
        self.b.editor.interfaces.append("Text")
        self.b.editor.errors["get_n_selections"] = NativeError("Published selection unavailable")
        facts = self.setup_text("2", "replace")
        self.assertEqual(self.typed(facts)["readiness"], "no")
        self.assertIn("selection", self.typed(facts)["pending"])
        self.assertFalse(self.driver.current.contracts[self.typed(facts)["id"]]["shortcutSelection"])
        self.assertEqual(self.b.desktop.events, [])

    def test_opaque_spin_drift_after_selection_stops_before_text_and_never_replays(self):
        self.opaque_spin()
        sibling = Node(
            "entry",
            "Same-window editor",
            "keep",
            parent=self.b.window,
            interfaces=["Component", "Text"],
            states={"ENABLED", "EDITABLE", "FOCUSABLE"},
        )
        self.b.window.children.append(sibling)
        facts = self.setup_text("2.34", "replace")
        command = self.command(facts)

        def change_recipient(kind, detail):
            if (kind, detail) == (3, "a"):
                self.b.editor.states.discard("FOCUSED")
                sibling.states.add("FOCUSED")

        self.hook(change_recipient)
        result = self.send(command)
        self.assertEqual(result["dispatch"], "unknown")
        self.assertIsNone(result["retryReason"])
        self.assertEqual((self.b.editor.text, self.b.other_editor.text), ("1.25", "untouched"))
        self.assertEqual(sibling.text, "keep")
        self.assertEqual(
            [event for event in self.b.desktop.events if event[0] in (2, 3)],
            [(2, "ctrl"), (2, "a"), (3, "a"), (3, "ctrl")],
        )
        self.assertEqual(self.send(command), result)
        with self.assertRaises(Failure):
            self.send(command, 2)

    def test_opaque_spin_payload_or_select_all_mapping_failure_moves_no_focus(self):
        self.opaque_spin()
        self.drift()
        self.b.desktop.keymap_missing.add("a")
        facts = self.setup_text("2", "replace")
        self.assertEqual(self.typed(facts)["supported"], "no")
        self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(self.focus_calls(), [])

    def test_missing_editable_state_does_not_grant_text_from_focusability(self):
        self.b.editor.states.discard("EDITABLE")
        facts = self.setup_text()
        self.assertEqual(self.typed(facts)["supported"], "no")
        self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(self.focus_calls(), [])

    def test_object_unicode_route_remains_usable_when_keyboard_map_cannot_encode_it(self):
        self.b.editor.interfaces.append("EditableText")
        facts = self.setup_text("中😀")
        self.assertEqual(self.typed(facts)["supported"], "no")
        self.assertEqual(self.send(self.command(facts, unit="native_text_edit"))["dispatch"], "dispatched")
        self.assertEqual(self.b.editor.text, "ab中😀cd")
        self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(self.focus_calls(), [])

    def test_optional_keyboard_observation_failure_preserves_object_replace(self):
        self.b.editor.interfaces.append("EditableText")
        for code in ("input_unavailable", "mode_unsupported", "observation_incomplete", "ambiguous_target"):
            with self.subTest(code=code):
                with patch.object(self.b.desktop, "active", side_effect=Failure("Active window unavailable", code)):
                    facts = self.setup_text("new", "replace")
                    self.assertEqual(
                        [(p["unit"], p["channel"], p["supported"]) for p in facts["primitives"]],
                        [("native_text_edit", "object", "yes")],
                    )
                    self.assertEqual(self.send(self.command(facts, unit="native_text_edit"))["dispatch"], "dispatched")
                self.assertEqual(self.b.editor.text, "new")
                self.assertEqual(self.b.other_editor.text, "untouched")
                self.assertEqual([c[0] for c in self.writes()], ["set_text_contents"])
                self.assertEqual(self.b.desktop.events, [])
                self.assertEqual(self.focus_calls(), [])
                self.assertEqual(self.b.desktop.activations, [])
                self.finish()
                self.b.ax.calls.clear()
                self.b.editor.text = "abcd"

    def test_independent_object_edit_does_not_retarget_a_composites_child(self):
        self.b.editor.role = "combo box"
        self.b.editor.interfaces.append("EditableText")
        child = Node(
            "entry",
            text="CHILD",
            parent=self.b.editor,
            interfaces=["Text", "EditableText", "Component"],
            states={"ENABLED", "EDITABLE"},
        )
        self.b.editor.children.append(child)
        # The owner's EditableText is independently executable even when its
        # structural child metadata cannot be enumerated by a query.
        self.b.editor.errors["get_child_count"] = "optional child query failed"
        facts = self.setup_text("OWNER UPDATED", "replace")
        receipt = self.send(self.command(facts, unit="native_text_edit"))
        self.assertEqual(receipt["dispatch"], "dispatched")
        self.assertEqual(
            (self.b.editor.text, child.text, self.b.other_editor.text), ("OWNER UPDATED", "CHILD", "untouched")
        )
        self.assertEqual([(c[0], c[1]) for c in self.writes()], [("set_text_contents", self.b.editor)])
        self.assertEqual(self.b.desktop.events, [])

    def test_optional_keyboard_provider_error_preserves_object_edit(self):
        self.b.editor.interfaces.append("EditableText")
        with patch.object(self.b.desktop, "active", side_effect=NativeError("optional desktop query failed")):
            facts = self.setup_text("OBJECT UPDATED", "replace")
            receipt = self.send(self.command(facts, unit="native_text_edit"))
        self.assertEqual(receipt["dispatch"], "dispatched")
        self.assertEqual((self.b.editor.text, self.b.other_editor.text), ("OBJECT UPDATED", "untouched"))
        self.assertEqual([c[0] for c in self.writes()], ["set_text_contents"])
        self.assertEqual(self.b.desktop.events, [])

    def test_optional_keyboard_provider_error_requires_an_independent_contract(self):
        with patch.object(self.b.desktop, "active", side_effect=NativeError("required desktop query failed")):
            with self.assertRaises(NativeError):
                self.setup_text("never", "replace")
        self.assertEqual(self.writes(), [])

    def test_effectful_keyboard_failure_is_not_an_optional_observation(self):
        self.b.editor.interfaces.append("EditableText")
        with patch.object(
            self.b.desktop,
            "active",
            side_effect=Failure("unexpected query effect", "input_unavailable", effect="possible"),
        ):
            with self.assertRaises(Failure):
                self.setup_text("never", "replace")
        self.assertEqual(self.writes(), [])

    def test_optional_keyboard_failure_does_not_hide_non_channel_errors(self):
        self.b.editor.interfaces.append("EditableText")
        for code in ("stale_target", "blocked_by_modal", "execution_failed"):
            with self.subTest(code=code):
                with patch.object(self.b.desktop, "active", side_effect=Failure("Receiver observation failed", code)):
                    with self.assertRaises(Failure) as error:
                        self.setup_text("new", "replace")
                self.assertEqual(error.exception.code, code)
                self.assertEqual(self.b.editor.text, "abcd")
                self.assertEqual(self.writes(), [])
                self.assertEqual(self.b.desktop.events, [])
                self.assertEqual(self.focus_calls(), [])
                self.finish()

    def test_optional_keyboard_failure_checks_cancellation_and_deadline(self):
        self.b.editor.interfaces.append("EditableText")
        for code in ("cancelled", "deadline_exceeded"):
            with self.subTest(code=code):
                self.setup_text("new", "replace")
                ctx = base.context()

                def fail(*, code=code, ctx=ctx):
                    if code == "cancelled":
                        ctx.cancelled.set()
                    else:
                        ctx.deadline = 0
                    raise Failure("Active window reply unavailable", "input_unavailable")

                with patch.object(self.b.desktop, "active", side_effect=fail):
                    with self.assertRaises(Failure) as error:
                        self.inspect(ctx)
                self.assertEqual(error.exception.code, code)
                self.assertIsNone(self.driver.current)
                self.assertEqual(self.writes(), [])
                self.assertEqual(self.b.desktop.events, [])
                self.assertEqual(self.focus_calls(), [])
                self.assertEqual(self.b.desktop.activations, [])

    def test_required_keyboard_observation_failure_is_not_an_empty_success(self):
        for code in ("input_unavailable", "mode_unsupported"):
            for app in (False, True):
                with self.subTest(code=code, app=app):
                    with patch.object(self.b.desktop, "active", side_effect=Failure("Active window unavailable", code)):
                        with self.assertRaises(Failure) as error:
                            self.setup_text("q", app=app)
                    self.assertEqual(error.exception.code, code)
                    self.assertEqual(self.b.editor.text, "abcd")
                    self.assertEqual(self.writes(), [])
                    self.assertEqual(self.b.desktop.events, [])
                    self.assertEqual(self.focus_calls(), [])
                    self.assertEqual(self.b.desktop.activations, [])
                    self.finish()

    def test_target_reparented_during_optional_receiver_observation_still_fails(self):
        self.b.editor.interfaces.append("EditableText")

        def move_target():
            self.b.editor.parent = self.b.other_editor.parent
            return 11

        with patch.object(self.b.desktop, "active", side_effect=move_target):
            with self.assertRaises(Failure) as error:
                self.setup_text("new", "replace")
        self.assertEqual(error.exception.code, "stale_target")
        self.assertEqual(self.b.editor.text, "abcd")
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(self.focus_calls(), [])

    def test_preserved_object_contract_rechecks_scope_before_dispatch(self):
        self.b.editor.interfaces.append("EditableText")
        with patch.object(
            self.b.desktop, "active", side_effect=Failure("Active window unavailable", "input_unavailable")
        ):
            facts = self.setup_text("new", "replace")
        self.b.editor.parent = self.b.other_editor.parent
        result = self.send(self.command(facts, unit="native_text_edit"))
        self.assertEqual((result["dispatch"], result["errorCode"]), ("notDispatched", "stale_target"))
        self.assertEqual(self.b.editor.text, "abcd")
        self.assertEqual(self.b.other_editor.text, "untouched")
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(self.focus_calls(), [])
        self.assertEqual(self.b.desktop.activations, [])

    def test_full_mapping_preflight_before_any_focus_or_partial_ascii_prefix(self):
        self.drift()
        facts = self.setup_text("abc😀")
        self.assertEqual(self.typed(facts)["supported"], "no")
        self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(self.focus_calls(), [])
        self.assertEqual(self.b.editor.text, "abcd")
        self.assertEqual(self.b.desktop.activations, [])

    def test_replace_adds_or_updates_one_full_selection_before_character(self):
        for before, method in [([], "add_selection"), ([(1, 2)], "set_selection")]:
            with self.subTest(before=before):
                self.b.editor.text = "a😀bc"
                self.b.editor.selections = before.copy()
                facts = self.setup_text("OK", "replace")
                self.assertEqual((self.typed(facts)["readiness"], facts["input"]["selectionKnown"]), ("yes", "yes"))
                result = self.send(self.command(facts))
                self.assertEqual(result["dispatch"], "dispatched")
                self.assertIn("nativeSelection", result["preparation"])
                self.assertEqual(self.b.editor.text, "OK")
                self.assertEqual([c[0] for c in self.setters()], [method])
                self.assertEqual(self.setters()[0][2][-2:], (0, 4))
                self.assertEqual(self.writes(), [])
                self.finish()
                self.b.ax.calls.clear()
                self.b.desktop.events.clear()

    def test_already_full_selection_skips_selection_setter(self):
        self.b.editor.selections = [(0, 4)]
        facts = self.setup_text("new", "replace")
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.assertEqual(self.b.editor.text, "new")
        self.assertEqual(self.setters(), [])

    def test_replace_preparation_only_focuses_and_does_not_select_or_type(self):
        self.drift()
        facts = self.setup_text("new", "replace")
        result = self.send(self.command(facts, prepare=True))
        self.assertEqual(result["dispatch"], "notDispatched")
        self.assertEqual(result["retryReason"], "preparationUnavailable")
        self.assertEqual(self.setters(), [])
        self.assertEqual(self.b.desktop.events, [])
        self.assertFalse(self.driver.current.dispatched)

    def test_empty_replace_deletes_content_and_empty_target_is_confirmed_noop(self):
        facts = self.setup_text("", "replace")
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.assertEqual(self.b.editor.text, "")
        self.assertEqual([detail for kind, detail in self.b.desktop.events if kind == 2], ["BackSpace"])
        self.finish()
        self.b.desktop.events.clear()
        self.b.ax.calls.clear()
        facts = self.setup_text("", "replace")
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["confirmation"]), ("notDispatched", "confirmed"))
        self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(self.setters(), [])

    def test_selection_read_gap_blocks_replace_but_insert_does_not_depend_on_selection(self):
        self.b.editor.errors["get_n_selections"] = NativeError("Text selection unavailable")
        facts = self.setup_text("new", "replace")
        self.assertEqual(self.typed(facts)["readiness"], "no")
        self.assertIn("selection", self.typed(facts)["pending"])
        self.assertEqual(self.b.desktop.events, [])
        self.finish()
        facts = self.setup_text("q")
        self.assertEqual(self.typed(facts)["readiness"], "yes")
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.assertEqual(self.b.editor.text, "abqcd")

    def test_selection_failure_is_possible_effect_and_cannot_replay(self):
        self.b.editor.errors["add_selection"] = False
        facts = self.setup_text("new", "replace")
        command = self.command(facts)
        result = self.send(command)
        self.assertEqual(result["dispatch"], "unknown")
        self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(self.b.editor.text, "abcd")
        self.assertEqual(len(self.setters()), 1)
        with self.assertRaises(Failure):
            self.send(command, 2)
        self.finish()
        self.assertEqual(self.send(command), result)
        self.assertEqual(len(self.setters()), 1)

    def test_selection_effect_is_preserved_when_provider_returns_false_or_throws(self):
        for outcome in (False, NativeError("Selection reply lost")):
            with self.subTest(outcome=repr(outcome)):
                try:
                    self.b.editor.selections = []
                    self.b.editor.errors["add_selection"] = outcome
                    self.b.ax.hooks["add_selection"] = lambda obj, start, end: setattr(
                        obj, "selections", [(start, end)]
                    )
                    facts = self.setup_text("new", "replace")
                    command = self.command(facts)
                    result = self.send(command)
                    self.assertEqual((result["dispatch"], result["cleanup"]), ("unknown", "complete"))
                    self.assertIsNone(result["retryReason"])
                    self.assertEqual(
                        self.b.editor.selections, [(0, 4)], "Possible selection effects must not be undone"
                    )
                    self.assertEqual(self.b.editor.text, "abcd")
                    self.assertEqual(self.b.other_editor.text, "untouched")
                    self.assertEqual(self.b.desktop.events, [])
                    self.assertEqual(len(self.setters()), 1)
                    with self.assertRaises(Failure):
                        self.send(command, 2)
                    self.finish()
                    self.assertEqual(self.send(command), result)
                    self.assertEqual(len(self.setters()), 1)
                finally:
                    if self.driver.current is not None:
                        self.driver.finish(self.driver.current.token)
                    self.b.ax.calls.clear()

    def test_cancel_while_waiting_for_selection_does_not_repeat_setter_or_type(self):
        # The provider accepted a selection request but has not published it.
        self.b.editor.errors["add_selection"] = True
        facts = self.setup_text("new", "replace")
        command = self.command(facts)
        ctx = base.context()
        waits = []

        def cancel_wait():
            waits.append(True)
            ctx.cancelled.set()

        ctx.pump = cancel_wait
        result = self.send(command, ctx=ctx)
        self.assertEqual(
            (result["dispatch"], result["errorCode"], result["cleanup"]), ("unknown", "cancelled", "complete")
        )
        self.assertEqual(waits, [True])
        self.assertEqual(len(self.setters()), 1)
        self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(self.b.editor.text, "abcd")
        self.assertEqual(self.b.other_editor.text, "untouched")
        self.assertIsNone(result["retryReason"])
        with self.assertRaises(Failure):
            self.send(command, 2)

    def test_already_full_selection_loses_receiver_before_first_character_without_dispatch(self):
        self.b.editor.selections = [(0, 4)]
        facts = self.setup_text("abc", "replace")
        # A native selection read, before any setter or key, moves focus.
        self.b.ax.hooks["get_selection"] = lambda obj, index: obj.states.discard("FOCUSED")
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["errorCode"]), ("notDispatched", "input_unavailable"))
        self.assertFalse(self.driver.current.dispatched)
        self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(self.setters(), [])
        self.assertEqual(self.b.editor.text, "abcd")
        self.assertEqual(self.b.other_editor.text, "untouched")

    def test_contained_keyboard_replacement_keeps_owner_and_types_only_child(self):
        facts = self.setup_contained_text()
        self.assertEqual(facts["binding"], self.selected)
        self.assertEqual(facts["input"]["editor"], self.editor_ref)
        result = self.send(self.command(facts))
        self.assertEqual((result["target"], result["dispatch"]), (self.selected, "dispatched"))
        self.assertEqual(self.b.editor.text, "new")
        self.assertEqual(self.owner.text, "")
        self.assertEqual(self.b.other_editor.text, "untouched")
        self.assertTrue(all(call[1] is self.b.editor for call in self.setters()))
        self.assertEqual(self.writes(), [])

    def test_contained_owner_becoming_readonly_stops_before_selection_or_keys(self):
        facts = self.setup_contained_text()
        self.owner.states.add("READ_ONLY")
        result = self.send(self.command(facts))
        self.assertEqual(result["dispatch"], "notDispatched")
        self.assertFalse(self.driver.current.dispatched)
        self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(self.setters(), [])
        self.assertEqual(self.b.editor.text, "abcd")
        self.assertEqual(self.b.other_editor.text, "untouched")

    def test_contained_owner_change_after_select_all_never_replans_or_sends_characters(self):
        for scenario in ("readonly", "reparent"):
            with self.subTest(scenario=scenario):
                try:
                    facts = self.setup_contained_text()
                    command = self.command(facts)

                    def change(obj, start, end, *, scenario=scenario):
                        if scenario == "readonly":
                            self.owner.states.add("READ_ONLY")
                        else:
                            obj.parent = self.b.window

                    self.b.ax.hooks["add_selection"] = change
                    result = self.send(command)
                    self.assertEqual(result["dispatch"], "unknown")
                    self.assertIsNone(result["retryReason"])
                    self.assertEqual(self.b.desktop.events, [])
                    self.assertEqual(len(self.setters()), 1)
                    self.assertEqual(self.b.editor.text, "abcd")
                    self.assertEqual(self.b.other_editor.text, "untouched")
                    with self.assertRaises(Failure):
                        self.send(command, 2)
                finally:
                    if self.driver.current is not None:
                        self.driver.finish(self.driver.current.token)
                    self.b.ax.calls.clear()
                    self.b.editor.selections = []

    def test_unconfirmed_select_all_never_sends_replacement(self):
        self.b.editor.errors["add_selection"] = True
        facts = self.setup_text("new", "replace")
        result = self.send(self.command(facts))
        self.assertEqual(result["dispatch"], "unknown")
        self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(self.b.editor.text, "abcd")
        self.assertEqual(len(self.setters()), 1)

    def test_async_selection_is_waited_for_without_repeating_select_all(self):
        self.b.editor.errors["add_selection"] = True
        attempts = []

        def selected(obj):
            if obj is self.b.editor and self.setters():
                attempts.append(True)
                if len(attempts) == 3:
                    obj.selections = [(0, len(obj.text))]

        self.b.ax.hooks["get_n_selections"] = selected
        facts = self.setup_text("ok", "replace")
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.assertEqual(self.b.editor.text, "ok")
        self.assertEqual(len(self.setters()), 1)

    def test_selection_changed_after_shift_stops_before_first_character(self):
        facts = self.setup_text("ABC", "replace")
        self.hook(
            lambda kind, detail: (
                setattr(self.b.editor, "selections", [(0, 1)]) if (kind, detail) == (2, "shift") else None
            )
        )
        result = self.send(self.command(facts))
        self.assertEqual(result["dispatch"], "unknown")
        self.assertEqual(self.b.desktop.events, [(2, "shift"), (3, "shift")])
        self.assertEqual(self.b.editor.text, "abcd")
        self.assertFalse(self.b.desktop.keys)

    def test_full_selection_is_not_required_after_first_character(self):
        facts = self.setup_text("AbC", "replace")
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.assertEqual(self.b.editor.text, "AbC")
        self.assertEqual(len(self.setters()), 1)

    def test_normal_completion_selection_after_first_character_does_not_block_remaining_text(self):
        facts = self.setup_text("Abc", "replace")

        def complete(kind, detail):
            if (kind, detail) == (2, "a"):
                self.b.editor.text = "A-suggestion"
                self.b.editor.selections = [(1, len(self.b.editor.text))]

        self.hook(complete)
        self.assertEqual(self.send(self.command(facts))["dispatch"], "dispatched")
        self.assertEqual(self.b.editor.text, "Abc")
        self.assertEqual(len(self.setters()), 1)

    def test_selection_callback_cannot_redirect_replacement_characters(self):
        facts = self.setup_text("ABC", "replace")
        self.b.ax.hooks["add_selection"] = lambda *args: self.drift()
        result = self.send(self.command(facts))
        self.assertEqual(result["dispatch"], "unknown")
        self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(self.b.editor.text, "abcd")
        self.assertEqual(self.b.other_editor.text, "untouched")
        self.assertEqual(len(self.setters()), 1)

    def test_keymap_change_after_shift_stops_before_character(self):
        facts = self.setup_text("ABC")

        def remap(kind, detail):
            if (kind, detail) == (2, "shift"):
                self.b.desktop.text_keyboard.generation += 1

        self.hook(remap)
        result = self.send(self.command(facts))
        self.assertEqual(result["dispatch"], "unknown")
        self.assertEqual(self.b.desktop.events, [(2, "shift"), (3, "shift")])
        self.assertEqual(self.b.editor.text, "abcd")
        self.assertFalse(self.b.desktop.keys)

    def test_focus_drift_after_shift_releases_modifier_and_sends_no_character(self):
        facts = self.setup_text("ABC")
        self.hook(lambda kind, detail: self.drift() if (kind, detail) == (2, "shift") else None)
        result = self.send(self.command(facts))
        self.assertEqual(result["dispatch"], "unknown")
        self.assertEqual(self.b.desktop.events, [(2, "shift"), (3, "shift")])
        self.assertEqual(self.b.editor.text, "abcd")
        self.assertEqual(self.b.other_editor.text, "untouched")
        self.assertTrue(self.driver.current.dispatched)
        self.assertFalse(self.b.desktop.keys)

    def test_focus_drift_after_first_character_stops_remaining_text(self):
        facts = self.setup_text("abc")
        self.hook(lambda kind, detail: self.drift() if (kind, detail) == (2, "a") else None)
        result = self.send(self.command(facts))
        self.assertIn(result["dispatch"], ("unknown", "partial"))
        self.assertEqual(self.b.editor.text, "abacd")
        self.assertEqual(self.b.other_editor.text, "untouched")
        self.assertEqual(self.b.desktop.events, [(2, "a"), (3, "a")])

    def test_same_window_other_control_cannot_receive_remaining_characters(self):
        facts = self.setup_text("abc")

        def change(kind, detail):
            if (kind, detail) == (2, "a"):
                self.b.editor.states.discard("FOCUSED")
                self.b.other_editor.states.add("FOCUSED")

        self.hook(change)
        result = self.send(self.command(facts))
        self.assertIn(result["dispatch"], ("unknown", "partial"))
        self.assertEqual(self.b.desktop.events, [(2, "a"), (3, "a")])
        self.assertEqual(self.b.other_editor.text, "untouched")

    def test_original_role_parent_editable_modal_and_lock_rechecked_before_keys(self):
        original = self.b.desktop.items[:]
        for scenario in ("role", "parent", "editable", "modal", "lock"):
            with self.subTest(scenario=scenario):
                facts = self.setup_text("q", within=(self.b.window,))
                if scenario == "role":
                    self.b.editor.role = "table cell"
                elif scenario == "parent":
                    self.b.editor.parent = self.b.other_window
                elif scenario == "editable":
                    self.b.editor.states.discard("EDITABLE")
                elif scenario == "modal":
                    self.b.desktop.items.append(
                        Window(
                            12,
                            "Modal",
                            "Fixture",
                            42,
                            (0, 0, 100, 100),
                            modal=True,
                            transient=11,
                            mapped=True,
                            process_start="generation",
                            accessible=Node("dialog"),
                        )
                    )
                else:
                    self.b.locked = True
                self.assertEqual(self.send(self.command(facts))["dispatch"], "notDispatched")
                self.assertEqual(self.b.desktop.events, [])
                self.finish()
                self.b.editor.role = "entry"
                self.b.editor.parent = self.b.window
                self.b.editor.states.add("EDITABLE")
                self.b.desktop.items = original[:]
                self.b.locked = False

    def test_cancel_after_shift_is_unknown_and_release_is_unconditional(self):
        facts = self.setup_text("ABC")
        ctx = base.context()
        self.hook(lambda kind, detail: ctx.cancelled.set() if (kind, detail) == (2, "shift") else None)
        result = self.send(self.command(facts), ctx=ctx)
        self.assertEqual(
            (result["dispatch"], result["errorCode"], result["cleanup"]), ("unknown", "cancelled", "complete")
        )
        self.assertEqual(self.b.desktop.events, [(2, "shift"), (3, "shift")])
        self.assertEqual(self.b.editor.text, "abcd")

    def test_release_failure_preserves_uncertainty_and_held_key_for_retirement(self):
        facts = self.setup_text("a")

        def fail(kind, detail):
            if kind == 3:
                raise NativeError("Release failed")

        self.hook(fail)
        result = self.send(self.command(facts))
        self.assertEqual((result["dispatch"], result["cleanup"]), ("unknown", "failed"))
        self.assertTrue(self.b.desktop.keys)
        self.assertEqual(self.driver.finish(self.operation)["cleanup"], "failed")

    def test_keyboard_receipt_duplicate_after_finish_does_not_retype(self):
        facts = self.setup_text("q")
        command = self.command(facts)
        result = self.send(command)
        self.finish()
        events = self.b.desktop.events[:]
        self.assertEqual(self.send(command), result)
        self.assertEqual(self.b.desktop.events, events)
        self.assertEqual(self.b.editor.text, "abqcd")

    def test_authorized_foreground_follows_background_preparation_and_does_not_restore(self):
        self.drift()
        self.b.desktop.assist = True
        facts = self.setup_text("new")
        denied = self.send(self.command(facts, prepare=True, foreground=True))
        self.assertEqual(denied["dispatch"], "notDispatched")
        self.assertEqual(self.b.desktop.activations, [])
        result = self.send(self.command(facts, prepare=True), 2)
        self.assertEqual(result["retryReason"], "preparationUnavailable")
        self.assertEqual(self.b.desktop.events, [])
        result = self.send(self.command(self.inspect(), prepare=True, foreground=True), 3)
        self.assertEqual(result["confirmation"], "confirmed")
        self.assertEqual(self.send(self.command(self.inspect()), 4)["dispatch"], "dispatched")
        self.finish()
        self.assertEqual(self.b.desktop.foreground, 11)
        self.assertEqual(self.b.desktop.activations, [11])
        self.assertEqual(self.b.editor.text, "abnewcd")


class LinuxNativeTypedTextSDKTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.connections, self.diagnostics = [], []
        self.temp = TemporaryDirectory()
        self.env = patch.dict(os.environ, {"COMPUTER_USE_DESKTOP": "shared"})
        self.env.start()
        setDiagnosticHandler(self.diagnostics.append)

    async def asyncTearDown(self):
        setDiagnosticHandler(None)
        for connection in self.connections:
            connection.close()
        for connection in self.connections:
            await connection.waitClosed()
        self.env.stop()
        self.temp.cleanup()

    async def connect(self, *, mode="shared", assist=False, ready=True, extra=()):
        os.environ["COMPUTER_USE_DESKTOP"] = mode
        self.journal = Path(self.temp.name) / ("keys-" + str(len(self.connections)) + ".jsonl")
        args = [
            "COMPUTER_USE_DESKTOP=" + mode,
            "COMPUTER_USE_FOREGROUND_ASSIST=" + ("1" if assist else "0"),
            sys.executable,
            str(base.ROOT / "tests/fixtures/linux_native_provider.py"),
            "--typed-text",
            "--typed-journal",
            str(self.journal),
        ]
        if ready:
            args.append("--typed-ready")
        args.extend(extra)
        connection = Connection(lambda: None, environmentDriver(args, timeoutMs=5000), protocol_version=6)
        self.connections.append(connection)
        computer = nativeComputer(ConnectionPort(connection, HostPolicy(mode, assist)))
        app = computer.app((await computer.listApps())[0].id)
        state = await app.read(view="both")
        state = await app.read(view="both")
        return connection, computer, app, state

    def commands(self):
        return [
            e["params"]["command"]
            for e in self.diagnostics
            if e["kind"] == "command_start" and e["method"] == "execute"
        ]

    def events(self):
        return [json.loads(line) for line in self.journal.read_text().splitlines()] if self.journal.exists() else []

    def no_automatic_read(self):
        starts = [e for e in self.diagnostics if e["kind"] == "command_start"]
        last = max(i for i, e in enumerate(starts) if e["method"] == "execute")
        self.assertEqual([e["method"] for e in starts[last + 1 :]], ["finish"])

    async def test_public_element_insert_replace_empty_replace_and_submit(self):
        _, _, app, _ = await self.connect()
        field = await app.element([{"name": "Input"}])
        for text, options, expected in [
            ("Hi!", {}, "abHi!cd"),
            ("New", {"replace": True}, "New"),
            ("", {"replace": True}, ""),
        ]:
            with self.subTest(text=text, options=options):
                result = await field.typeText(text, **options)
                self.assertEqual(result.status, "dispatched")
                self.assertFalse(hasattr(result, "text"))
                self.no_automatic_read()
                self.assertEqual((await field.readText()).text, expected)
        await field.pressKey("Return")
        self.no_automatic_read()
        (self.assertEqual((await (await app.element([{"name": "Submissions"}])).readText()).text, "1"))
        self.assertEqual([c["kind"] for c in self.commands()], ["key_sequence"] * 4)
        self.assertTrue(all(e["other"] == "untouched" for e in self.events()))

    async def test_public_app_type_text_works_without_accessibility_tree(self):
        _, _, app, state = await self.connect(extra=("--typed-no-ax",))
        self.assertNotIn("Input", state.text)
        self.assertEqual((await app.typeText("Hi")).status, "dispatched")
        self.no_automatic_read()
        self.assertEqual(self.events()[-1]["text"], "abHicd")
        self.assertEqual(self.events()[-1]["other"], "untouched")
        self.assertIsNone(self.commands()[0]["target"]["node"])

    async def test_public_native_editable_unicode_is_preferred_over_unmapped_keyboard(self):
        _, _, app, _ = await self.connect(ready=False, extra=("--typed-native-editable",))
        field = await app.element([{"name": "Input"}])
        self.assertEqual((await field.typeText("中😀")).status, "dispatched")
        self.no_automatic_read()
        self.assertEqual([c["kind"] for c in self.commands()], ["native_text_edit"])
        self.assertEqual(self.events(), [])
        self.assertEqual((await field.readText()).text, "ab中😀cd")

    async def test_public_unmapped_or_readonly_rejects_before_preparation(self):
        for extra, text in [((), "abc😀"), (("--typed-readonly",), "abc")]:
            with self.subTest(extra=extra):
                _, _, app, _ = await self.connect(assist=True, ready=False, extra=extra)
                before = len(self.commands())
                with self.assertRaises(ComputerUseError) as error:
                    await (await app.element([{"name": "Input"}])).typeText(text)
                self.assertEqual(error.exception.effect, "none")
                self.assertEqual(self.commands()[before:], [])
                self.assertEqual(self.events(), [])

    async def test_public_no_text_can_insert_but_cannot_claim_full_replacement(self):
        _, _, app, _ = await self.connect(extra=("--typed-no-text",))
        field = await app.element([{"name": "Input"}])
        self.assertEqual((await field.typeText("a")).status, "dispatched")
        self.assertEqual(self.events()[-1]["text"], "abacd")
        before = len(self.events())
        with self.assertRaises(ComputerUseError) as error:
            await field.typeText("x", replace=True)
        self.assertEqual(error.exception.effect, "none")
        self.assertEqual(len(self.events()), before)
        self.assertEqual([c["kind"] for c in self.commands()], ["key_sequence"])

    async def test_public_empty_insert_requires_no_focus_or_key_events(self):
        _, _, app, _ = await self.connect(ready=False)
        self.assertEqual((await app.typeText("")).status, "verified")
        (self.assertEqual((await (await app.element([{"name": "Input"}])).typeText("")).status, "verified"))
        self.assertEqual(self.events(), [])
        self.assertEqual(self.commands(), [])

    async def test_public_shared_denies_input_after_background_only(self):
        _, _, app, _ = await self.connect(ready=False)
        with self.assertRaises(ComputerUseError):
            await (await app.element([{"name": "Input"}])).typeText("abc")
        self.assertEqual([(c["kind"], c["foreground"]) for c in self.commands()], [("prepare_receiver", False)])
        self.assertEqual(self.events(), [])

    async def test_public_authorized_background_foreground_then_one_text_sequence(self):
        _, _, app, _ = await self.connect(assist=True, ready=False)
        (self.assertEqual((await (await app.element([{"name": "Input"}])).typeText("XYZ")).status, "dispatched"))
        self.no_automatic_read()
        self.assertEqual(
            [(c["kind"], c["foreground"]) for c in self.commands()],
            [("prepare_receiver", False), ("prepare_receiver", True), ("key_sequence", False)],
        )
        self.assertEqual(self.events()[-1]["text"], "abXYZcd")
        self.assertTrue(all(e["active"] == 11 for e in self.events()))
        self.assertTrue(all(e["other"] == "untouched" for e in self.events()))

    async def test_public_shift_focus_loss_is_possible_without_foreground_replay(self):
        _, _, app, _ = await self.connect(assist=True, extra=("--typed-move-after-shift",))
        with self.assertRaises(ComputerUseError) as error:
            await (await app.element([{"name": "Input"}])).typeText("ABC")
        self.assertEqual(error.exception.effect, "possible")
        self.assertEqual([c["kind"] for c in self.commands()], ["key_sequence"])
        self.assertEqual([(e["kind"], e["detail"]) for e in self.events()], [(2, "shift"), (3, "shift")])
        self.assertEqual(self.events()[-1]["other"], "untouched")
        self.no_automatic_read()

    async def test_public_failed_release_retires_session(self):
        _, _, app, _ = await self.connect(extra=("--typed-release-failed",))
        field = await app.element([{"name": "Input"}])
        with self.assertRaises(ComputerUseError) as error:
            await field.typeText("a")
        self.assertEqual(error.exception.effect, "possible")
        self.assertIn("cleanup", error.exception.detail)
        before = len(self.events())
        with self.assertRaises(ComputerUseError):
            await field.typeText("a")
        self.assertEqual(len(self.events()), before)
        self.assertEqual([c["kind"] for c in self.commands()], ["key_sequence"])


if __name__ == "__main__":
    unittest.main()
