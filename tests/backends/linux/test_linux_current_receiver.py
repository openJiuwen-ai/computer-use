"""Current focus evidence uses the real Linux query pipeline with native fixtures."""

import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from linux_native_provider import NativeError, Node

from backends.linux import test_linux_native_keyboard as keyboard_tests
from backends.linux.test_linux_native import context
from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.control.native_focus_discovery import NativeFocusDiscovery
from computer_use_linux.runtime.native import query


class LinuxCurrentReceiverTests(unittest.TestCase):
    setUp = keyboard_tests.LinuxNativeKeyboardTests.setUp
    tearDown = keyboard_tests.LinuxNativeKeyboardTests.tearDown
    begin = keyboard_tests.LinuxNativeKeyboardTests.begin
    inspect = keyboard_tests.LinuxNativeKeyboardTests.inspect
    surfaces = keyboard_tests.LinuxNativeKeyboardTests.surfaces
    target = keyboard_tests.LinuxNativeKeyboardTests.target
    ready = keyboard_tests.LinuxNativeKeyboardTests.ready

    def arrange(self):
        self.ready()
        self.root = self.target()
        self.group = Node("panel", "Input container", parent=self.backend.window, children=[self.backend.editor])
        self.backend.editor.parent = self.group
        self.backend.window.children = [self.group]
        self.scope = dict(self.root, node=self.driver.store.keep(self.root["surface"], self.group))
        self.backend.ax.calls.clear()

    def observe(self, target=None, ctx=None):
        target = self.scope if target is None else target
        operation = self.driver.perform(
            "begin",
            dict(mode="write", target=target, intent=dict(kind="key", parameters=dict(key="Return"))),
            context(),
        )["operation"]
        try:
            result = self.driver.perform(
                "inspect",
                dict(
                    operation=operation, target=target, query=dict(kind="inputContext", within=[], currentReceiver=True)
                ),
                ctx or context(),
            )
            self.assertEqual(result["primitives"], [])
            self.assertFalse(self.driver.current.dispatched)
            self.assertEqual(self.backend.desktop.events, [])
            self.assertEqual(self.backend.desktop.activations, [])
            return result["input"]["currentReceiver"]
        finally:
            self.driver.finish(operation)

    def focused_container_source(self, source):
        self.group.states.add("FOCUSED")
        self.focus_source(source, self.group)

    def focus_source(self, source, candidate):
        self.backend.ax.focus_events = {}
        self.backend.window.interfaces = [item for item in self.backend.window.interfaces if item != "Collection"]
        if source == "event":
            self.backend.ax.focus_events = {self.backend.ax.native_key(candidate): candidate}
        elif source == "collection":
            self.backend.window.interfaces.append("Collection")
            a = self.backend.ax.A
            a.StateSet = lambda: NS(add=lambda value: None)
            a.CollectionMatchType = NS(ALL="all")
            a.CollectionSortOrder = NS(CANONICAL="canonical")
            a.MatchRule = NS(new=lambda *args: object())
            a.Collection = NS(get_matches=lambda *args: [candidate])

    def test_confirmed_editor_never_needs_descendant_completeness(self):
        for source in ("event", "collection", "tree"):
            for via_container in (False, True):
                for role in ("entry", "text"):
                    for fault in ("wide", "child_count"):
                        with self.subTest(source=source, via_container=via_container, role=role, fault=fault):
                            self.arrange()
                            editor = self.backend.editor
                            editor.role = role
                            editor.errors.clear()
                            editor.children = [Node("label", str(i), parent=editor) for i in range(1200)]
                            if fault == "child_count":
                                editor.errors["get_child_count"] = NativeError("editor children unavailable")
                            if via_container:
                                self.focused_container_source(source)
                            else:
                                self.focus_source(source, editor)
                            evidence = self.observe()
                            self.assertEqual(evidence["state"], "confirmed")
                            self.assertIs(self.driver.store.get(evidence["node"], self.scope["surface"]).obj, editor)
                            descendants = {id(child) for child in editor.children}
                            self.assertFalse(any(id(obj) in descendants for _, obj, _ in self.backend.ax.calls))
                            self.assertFalse(
                                any(
                                    name == "get_child_count" and obj is editor
                                    for name, obj, _ in self.backend.ax.calls
                                )
                            )

    def test_unknown_or_failed_candidate_role_does_not_authorize_child_search(self):
        for source in ("event", "collection", "tree"):
            for role in ("unknown", "missing"):
                with self.subTest(source=source, role=role):
                    self.arrange()
                    self.focused_container_source(source)
                    if role == "missing":
                        self.group.errors["get_role_name"] = NativeError("candidate role unavailable")
                    else:
                        self.group.role = role
                    evidence = self.observe()
                    # Focus confirmation is not input permission. The shared
                    # resolver must check this object's own role; it cannot
                    # select the editor below an unclassified candidate.
                    self.assertEqual(evidence["state"], "confirmed")
                    self.assertEqual(evidence["node"], self.scope["node"])
                    self.assertEqual(evidence["ancestors"], [])
                    self.assertFalse(
                        any(name == "get_child_count" and obj is self.group for name, obj, _ in self.backend.ax.calls)
                    )
                    self.assertFalse(any(obj is self.backend.editor for _, obj, _ in self.backend.ax.calls))

    def test_layout_focus_does_not_choose_an_unfocused_editor(self):
        self.arrange()
        self.focused_container_source("event")
        self.backend.editor.states.discard("FOCUSED")
        evidence = self.observe()
        self.assertEqual(evidence["state"], "confirmed")
        self.assertEqual(evidence["node"], self.scope["node"])
        self.assertEqual(evidence["ancestors"], [])

    def test_event_and_collection_container_candidates_refine_only_the_focused_branch(self):
        for source in ("event", "collection"):
            with self.subTest(source=source):
                self.arrange()
                self.focused_container_source(source)
                unrelated = [Node("label", str(i), parent=self.group) for i in range(1200)]
                self.group.children.extend(unrelated)
                evidence = self.observe()
                self.assertEqual(evidence["state"], "confirmed")
                self.assertIs(self.driver.store.get(evidence["node"], self.scope["surface"]).obj, self.backend.editor)
                self.assertEqual(evidence["ancestors"], [self.scope["node"]])
                ignored = {id(obj) for obj in unrelated}
                self.assertFalse(any(id(obj) in ignored for _, obj, _ in self.backend.ax.calls))

    def test_candidate_refinement_keeps_known_independent_focus_conflicts(self):
        self.arrange()
        self.focused_container_source("collection")
        other = Node("entry", "Other", parent=self.backend.window, states={"FOCUSED", "SHOWING", "ENABLED"})
        self.backend.window.children.append(other)
        self.backend.ax.focus_events = {id(self.group): self.group, id(other): other}
        self.assertEqual(self.observe()["state"], "conflict")

    def test_candidate_outside_original_scope_does_not_scan_that_other_subtree(self):
        self.arrange()
        other = Node("panel", "Other container", parent=self.backend.window, states={"FOCUSED", "SHOWING", "ENABLED"})
        other.errors["get_child_count"] = NativeError("foreign subtree must not be queried")
        self.backend.window.children.append(other)
        self.backend.ax.focus_events = {id(other): other}
        self.assertEqual(self.observe()["state"], "conflict")
        self.assertFalse(any(name == "get_child_count" and obj is other for name, obj, _ in self.backend.ax.calls))

    def test_refinement_preserves_one_discovery_node_budget(self):
        self.arrange()
        self.focused_container_source("event")
        wrapper = Node(
            "panel",
            "Focused wrapper",
            parent=self.group,
            children=[self.backend.editor],
            states={"FOCUSED", "SHOWING", "ENABLED"},
        )
        self.backend.editor.parent = wrapper
        self.group.children = [wrapper]
        with patch.object(NativeFocusDiscovery, "SEARCH_NODES", 2):
            self.assertEqual(self.observe()["state"], "unavailable")
        self.assertEqual(self.observe()["state"], "confirmed")

    def test_large_unrelated_subtree_does_not_gate_confirmed_receiver(self):
        self.arrange()
        unrelated = [Node("label", str(i), parent=self.group) for i in range(500)]
        self.group.children.extend(unrelated)
        evidence = self.observe()
        self.assertEqual(evidence["state"], "confirmed")
        self.assertIs(self.driver.store.get(evidence["node"], self.scope["surface"]).obj, self.backend.editor)
        self.assertEqual(evidence["ancestors"], [self.scope["node"]])
        unrelated_ids = {id(obj) for obj in unrelated}
        self.assertFalse(any(id(obj) in unrelated_ids for _, obj, _ in self.backend.ax.calls))

    def test_duplicate_children_do_not_require_an_entire_tree_census(self):
        self.arrange()
        self.group.children.extend([self.backend.editor] * 400)
        self.assertEqual(self.observe()["state"], "confirmed")

    def test_container_metadata_is_not_receiver_discovery_evidence(self):
        self.arrange()
        self.group.errors["get_interfaces"] = NativeError("unrelated interface metadata unavailable")
        self.group.errors["get_role_name"] = NativeError("unrelated role metadata unavailable")
        self.assertEqual(self.observe()["state"], "confirmed")
        self.group.parent = self.backend.other_window
        with self.assertRaises(Failure) as caught:
            self.observe()
        self.assertEqual(caught.exception.code, "stale_target")

    def test_known_independent_focus_conflict_refuses_without_input(self):
        self.arrange()
        self.backend.editor.errors["get_child_count"] = NativeError("terminal children must not be queried")
        other = Node("entry", "Other", parent=self.group, states={"FOCUSED", "SHOWING", "ENABLED"})
        self.group.children.append(other)
        self.backend.ax.focus_events = {id(self.backend.editor): self.backend.editor, id(other): other}
        evidence = self.observe()
        self.assertEqual(evidence["state"], "conflict")
        self.assertEqual((evidence["node"], evidence["ancestors"]), (None, []))
        self.assertFalse(
            any(name == "get_child_count" and obj is self.backend.editor for name, obj, _ in self.backend.ax.calls)
        )

    def test_same_window_focus_outside_requested_container_is_conflict(self):
        self.arrange()
        self.backend.editor.states.discard("FOCUSED")
        other = Node("entry", "Other", parent=self.backend.window, states={"FOCUSED", "SHOWING", "ENABLED"})
        self.backend.window.children.append(other)
        self.assertEqual(self.observe()["state"], "conflict")

    def test_unavailable_focus_is_local_and_same_scope_recovers(self):
        self.arrange()
        self.backend.editor.errors["get_state_set"] = NativeError("temporary focus provider failure")
        self.assertEqual(self.observe()["state"], "unavailable")
        del self.backend.editor.errors["get_state_set"]
        self.assertEqual(self.observe()["state"], "confirmed")

    def test_no_reported_focus_is_distinct_from_unavailable(self):
        self.arrange()
        self.backend.editor.states.discard("FOCUSED")
        self.assertEqual(self.observe()["state"], "absent")

    def test_focus_loss_during_parent_query_never_publishes_confirmation(self):
        self.arrange()

        def lose_focus(obj):
            if obj is self.backend.editor:
                self.backend.editor.states.discard("FOCUSED")

        self.backend.ax.hooks["get_parent"] = lose_focus
        self.assertEqual(self.observe()["state"], "unavailable")

    def test_original_scope_loss_is_not_an_optional_focus_gap(self):
        self.arrange()
        self.group.dead = True
        with self.assertRaises((Failure, NativeError)):
            self.observe()

    def test_query_does_not_consume_continuous_input_hint(self):
        self.arrange()
        marker = (11, 11, self.backend.editor)
        self.driver.keyboard_recipients.hint = marker
        self.observe()
        self.assertIs(self.driver.keyboard_recipients.hint, marker)

    def test_original_scope_boundary_is_revalidated_after_focus_query(self):
        self.arrange()
        calls = 0

        def reparent(obj):
            nonlocal calls
            if obj is self.backend.editor:
                calls += 1
                if calls == 2:
                    self.group.parent = self.backend.other_window

        self.backend.ax.hooks["get_state_set"] = reparent
        with self.assertRaises(Failure) as caught:
            self.observe()
        self.assertEqual(caught.exception.code, "stale_target")

    def test_deadline_is_not_downgraded_to_unavailable(self):
        self.arrange()
        with self.assertRaises(Failure) as caught:
            self.observe(ctx=context(-1))
        self.assertEqual(caught.exception.code, "deadline_exceeded")

    def test_query_rejects_actuator_and_non_boolean_request(self):
        for values in (
            {"currentReceiver": False},
            {"currentReceiver": 1},
            {"currentReceiver": True, "actuator": {}},
            {"currentReceiver": True, "basis": []},
        ):
            with self.subTest(values=values), self.assertRaises(Failure):
                query(dict(kind="inputContext", within=[], **values))


if __name__ == "__main__":
    unittest.main()
