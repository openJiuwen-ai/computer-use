"""Addressed composite-input pipeline, using raw native provider callbacks.

The caller selects an editor, but commands/results retain the original owner.
Tests measure writes/events, including structural changes during preparation.
"""

import unittest
from copy import deepcopy
from unittest.mock import patch

from linux_native_provider import KeyboardBackend, NativeError, Node

import backends.linux.test_linux_native as base
from backends.linux.test_linux_native import context
from computer_use.protocol.execution import decodeExecutionResult
from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.runtime.native import NativeExecution


class LinuxNativeReceiverTests(unittest.TestCase):
    target = base.LinuxNativeFactsTests.target
    surfaces = base.LinuxNativeFactsTests.surfaces
    begin = base.LinuxNativeFactsTests.begin
    inspect = base.LinuxNativeFactsTests.inspect
    tearDown = base.LinuxNativeFactsTests.tearDown

    def setUp(self):
        self.backend = KeyboardBackend()
        self.backend.desktop.assist = True
        self.backend.ax.A.StateType.READ_ONLY = "READ_ONLY"
        self.editor = self.backend.editor
        self.owner = Node(
            "combo box",
            "Composite",
            children=[self.editor],
            parent=self.backend.window,
            interfaces=["Component", "Action"],
        )
        self.editor.parent = self.owner
        self.backend.window.children[0] = self.owner
        self.driver = NativeExecution(self.backend, "session")
        self.pid = patch("computer_use_linux.windowing.native_directory.process_start", return_value="generation")
        self.pid.start()

    def start(self, kind="replace", parameters=None):
        target = self.target()
        self.owner_ref = self.driver.store.keep(target["surface"], self.owner)
        self.editor_ref = self.driver.store.keep(target["surface"], self.editor)
        self.requested = dict(target, node=self.owner_ref)
        self.operation = self.driver.perform(
            "begin",
            dict(
                mode="write",
                target=self.requested,
                intent=dict(kind=kind, parameters=(dict(text="updated") if parameters is None else parameters)),
            ),
            context(),
        )["operation"]
        return self.observe()

    def observe(self):
        batch = base.read_graph(
            self.driver.perform(
                "inspect",
                dict(
                    operation=self.operation,
                    target=self.requested,
                    query=dict(
                        kind="nodes",
                        fields=["role", "enabled", "editable", "focusable", "readOnly"],
                        maxNodes=100,
                        maxDepth=4,
                    ),
                ),
                context(),
            )
        )
        relations = {}
        for edge in batch["relations"]:
            if edge["property"] in ("parent", "children"):
                relations.setdefault(edge["source"], []).append(edge["property"])
        self.query = dict(
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
        return self.input()

    def input(self, query=None):
        return decodeExecutionResult(
            "inspect",
            self.driver.perform(
                "inspect", dict(operation=self.operation, target=self.requested, query=query or self.query), context()
            ),
        )

    def send(self, facts, *, step=1, prepare=False, foreground=False):
        primitive = facts["primitives"][0]
        return decodeExecutionResult(
            "execute",
            self.driver.perform(
                "execute",
                dict(
                    operation=self.operation,
                    step=step,
                    command=dict(
                        kind="prepare_receiver" if prepare else primitive["unit"],
                        primitiveId=primitive["id"],
                        target=self.requested,
                        foreground=foreground,
                    ),
                ),
                context(),
            ),
        )

    def writes(self):
        return [
            call for call in self.backend.ax.calls if call[0] in ("set_text_contents", "delete_text", "insert_text")
        ]

    def test_replace_keeps_original_binding_and_writes_only_child_without_focus(self):
        facts = self.start()
        before_other = self.backend.other_editor.text
        self.assertEqual(facts["binding"], self.requested)
        self.assertEqual(facts["actuator"], self.query["actuator"])
        self.assertEqual(facts["basis"], self.query["basis"])
        result = self.send(facts)
        self.assertEqual((result["dispatch"], result["target"]), ("dispatched", self.requested))
        self.assertEqual(self.editor.text, "updated")
        self.assertEqual(self.backend.other_editor.text, before_other)
        self.assertEqual(self.backend.desktop.activations, [])
        self.assertTrue(all(call[1] is self.editor for call in self.writes()))
        self.assertEqual(self.send(facts), result)
        self.assertEqual(len(self.writes()), 1, "Repeated step returns the saved receipt")

    def test_normal_value_label_focus_changes_do_not_invalidate_structure(self):
        facts = self.start()
        self.owner.name = "new display name"
        self.editor.text = "automatic completion"
        self.editor.states.add("FOCUSED")
        result = self.send(facts)
        self.assertEqual(result["dispatch"], "dispatched")
        self.assertEqual(self.editor.text, "updated")

    def test_second_editor_appearing_after_selection_requests_replan_before_write(self):
        facts = self.start()
        self.owner.children.append(
            Node("text", "second", parent=self.owner, states={"ENABLED", "EDITABLE", "FOCUSABLE"})
        )
        result = self.send(facts)
        self.assertEqual((result["dispatch"], result["retryReason"]), ("notDispatched", "preconditionChanged"))
        self.assertEqual(self.writes(), [])
        self.assertFalse(self.driver.current.dispatched)

    def test_recipient_moved_to_unrelated_owner_is_not_written(self):
        facts = self.start()
        self.editor.parent = self.backend.window
        result = self.send(facts)
        self.assertEqual(result["dispatch"], "notDispatched")
        self.assertEqual(self.writes(), [])

    def test_provider_callback_reparent_after_structural_check_still_stops_write(self):
        facts = self.start()
        original = self.driver.text.guard

        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            self.editor.parent = self.backend.window
            return result

        self.driver.text.guard = mutate
        result = self.send(facts)
        self.assertEqual(result["dispatch"], "notDispatched")
        self.assertEqual(self.writes(), [])

    def test_structure_change_after_delete_is_partial_never_replanned(self):
        facts = self.start("insert", dict(text="new"))

        def after_delete(obj, start, end):
            self.backend.ax.delete(obj, start, end)
            self.owner.children.append(Node("text", parent=self.owner))
            return True

        self.backend.ax.A.EditableText.delete_text = after_delete
        result = self.send(facts)
        self.assertEqual(result["dispatch"], "partial")
        self.assertIsNone(result["retryReason"])
        self.assertEqual([call[0] for call in self.writes()], [])  # substituted callback records no wrapper
        self.assertEqual(self.editor.text, "abc")
        with self.assertRaises(Failure):
            self.send(facts, step=2)

    def test_unknown_native_write_is_not_replayed(self):
        facts = self.start()
        self.editor.errors["set_text_contents"] = NativeError("lost reply")
        result = self.send(facts)
        self.assertEqual(result["dispatch"], "unknown")
        self.assertIsNone(result["retryReason"])
        with self.assertRaises(Failure):
            self.send(facts, step=2)
        self.assertEqual(len(self.writes()), 1)

    def test_readonly_wins_even_when_provider_also_declares_editable(self):
        facts = self.start()
        self.editor.states.add("READ_ONLY")
        result = self.send(facts)
        self.assertEqual(result["dispatch"], "notDispatched")
        self.assertEqual(self.writes(), [])
        self.driver.finish(self.operation)
        facts = self.start()
        self.assertFalse(
            any(p["supported"] == "yes" for p in facts["primitives"]),
            "READ_ONLY plus EDITABLE must not offer a writable route",
        )

    def test_direct_readonly_editor_has_no_text_or_paste_contract(self):
        self.editor.states.add("READ_ONLY")
        self.start()
        self.driver.finish(self.operation)
        self.requested["node"] = self.editor_ref
        for kind in ("replace", "insert", "paste"):
            self.operation = self.driver.perform(
                "begin",
                dict(mode="write", target=self.requested, intent=dict(kind=kind, parameters=dict(text="unchanged"))),
                context(),
            )["operation"]
            facts = self.input(dict(kind="inputContext", within=[]))
            self.assertFalse(any(p["supported"] == "yes" for p in facts["primitives"]))
            self.driver.finish(self.operation)
        self.assertEqual(self.writes(), [])

    def test_key_preparation_and_delivery_use_child_but_receipts_keep_owner(self):
        facts = self.start("key", dict(key="Return"))
        self.assertEqual(facts["input"]["editor"], self.editor_ref)
        result = self.send(facts, prepare=True)
        self.assertEqual(result["retryReason"], "preparationUnavailable")
        self.assertEqual(self.backend.desktop.activations, [])
        fresh = self.observe()
        result = self.send(fresh, step=2, prepare=True, foreground=True)
        self.assertEqual(result["confirmation"], "confirmed")
        result = self.send(self.observe(), step=3)
        self.assertEqual(result["dispatch"], "dispatched")
        self.assertEqual(result["target"], self.requested)
        self.assertEqual(self.backend.counter.text, "1")
        focused = [obj for method, obj, _ in self.backend.ax.calls if method == "grab_focus"]
        self.assertTrue(focused and all(obj is self.editor for obj in focused))

    def test_readonly_composite_can_focus_and_receive_keys_but_cannot_edit(self):
        self.owner.states.add("READ_ONLY")
        self.editor.states.add("READ_ONLY")
        self.backend.desktop.foreground = self.backend.desktop.keyboard_window = 11
        self.editor.states.add("FOCUSED")
        before = self.editor.text
        facts = self.start("focus", {})
        result = self.send(facts, prepare=True)
        self.assertEqual(result["confirmation"], "confirmed")
        self.driver.finish(self.operation)
        facts = self.start("key", dict(key="Left"))
        result = self.send(facts)
        self.assertEqual(result["dispatch"], "dispatched")
        self.assertEqual(self.editor.text, before)
        self.assertEqual(self.writes(), [])
        self.driver.finish(self.operation)
        for kind in ("replace", "insert", "paste"):
            with self.assertRaises(Failure) as failure:
                self.start(kind, dict(text="must not edit"))
            self.assertEqual(failure.exception.code, "input_unavailable")
            self.driver.finish(self.operation)
        self.assertEqual(self.editor.text, before)

    def test_changed_basis_cannot_reuse_background_unavailable_receipt(self):
        facts = self.start("key", dict(key="Return"))
        self.assertEqual(self.send(facts, prepare=True)["retryReason"], "preparationUnavailable")
        self.owner.interfaces.append("Selection")
        # Change a used structural field while retaining the same editor object.
        self.owner.states.add("FOCUSABLE")
        facts = self.observe()
        result = self.send(facts, step=2, prepare=True, foreground=True)
        self.assertEqual(result["errorCode"], "invalid_request")
        self.assertEqual(self.backend.desktop.activations, [])

    def test_forged_or_previous_operation_evidence_is_rejected_before_input(self):
        self.start()
        old = deepcopy(self.query)
        forged = deepcopy(old)
        forged["basis"][0]["observed"] = "fake"
        with self.assertRaises(Failure):
            self.input(forged)
        self.driver.finish(self.operation)
        self.start()
        with self.assertRaises(Failure):
            self.input(old)
        self.assertEqual(self.writes(), [])

    def test_failed_dependency_read_is_unknown_not_permission_to_replan(self):
        facts = self.start()
        self.owner.errors["get_child_count"] = NativeError("provider timeout")
        result = self.send(facts)
        self.assertEqual((result["dispatch"], result["errorCode"]), ("notDispatched", "observation_incomplete"))
        self.assertIsNone(result["retryReason"])
        self.assertEqual(self.writes(), [])

    def test_read_operation_does_not_retain_execution_basis(self):
        target = self.target()
        operation = self.begin(target)
        self.assertIsNone(self.driver.current.selection_facts)
        self.driver.finish(operation)


if __name__ == "__main__":
    unittest.main()
