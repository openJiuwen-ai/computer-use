"""Linux selection evidence: production collector/store, substituted AT-SPI only."""

import sys
import threading
import time
import unittest
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import patch

from repository import ROOT as REPO_ROOT

sys.path.append(str(REPO_ROOT / "tests/fixtures"))
from linux_native_provider import Backend, NativeError, Node

from computer_use_linux.contracts.protocol import Failure, Request
from computer_use_linux.control.native_selection import (
    STRUCTURAL_FIELDS,
    Changed,
    NativeSelection,
    decode_selection_request,
)
from computer_use_linux.objects.native_store import NativeStore
from computer_use_linux.read.native_facts import NativeFacts
from computer_use_linux.runtime.native_operation import NativeOperation


def context():
    return Request(time.monotonic() + 10, threading.Event())


class LinuxNativeSelectionTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.ax = self.backend.ax
        self.ax.A.StateType.READ_ONLY = "READ_ONLY"
        self.owner = Node(
            "combo box",
            "Composite field",
            children=[self.backend.editor],
            interfaces=["Action"],
            actions=[("press", "Open the options")],
        )
        self.owner.parent = self.backend.window
        self.backend.window.children = [self.owner]
        self.backend.editor.parent = self.owner
        self.store = NativeStore(self.ax)
        self.owner_ref = self.store.keep("surface", self.owner)
        self.editor_ref = self.store.keep("surface", self.backend.editor)
        self.target = dict(
            session="session",
            instance="instance",
            rootWindow="window",
            surface="surface",
            node=self.owner_ref,
            capture=None,
        )
        self.ctx = context()
        self.ax.begin(self.ctx)
        self.facts = NativeFacts(self.ax, self.store, "session")
        self.execution = NS(
            b=self.backend,
            session="session",
            store=self.store,
            facts=self.facts,
            current=NativeOperation(
                self.target, self.ctx.deadline, dict(kind="replace", parameters=dict(text="new")), True
            ),
        )
        self.selection = NativeSelection(
            lambda: self.execution.current, self.execution.session, self.ax, self.store, self.facts
        )

    def collect(self, **changes):
        query = dict(fields=sorted(STRUCTURAL_FIELDS) + ["name", "Text.text"], maxNodes=1000, maxDepth=4)
        query.update(changes)
        batch = self.facts.collect(self.target, query, self.ctx)
        self.selection.retain(batch)
        return batch

    def basis(self, batch, *, actions=False):
        observed = batch["observed"]["id"]
        return [
            dict(
                observed=observed,
                node=self.owner_ref,
                fields=["role", "enabled"],
                actions=actions,
                relations=["children"],
            ),
            dict(
                observed=observed,
                node=self.editor_ref,
                fields=["role", "enabled", "editable", "interfaces"],
                actions=False,
                relations=["parent"],
            ),
        ]

    def selected(self, batch=None, **changes):
        batch = batch or self.collect()
        return self.selection.select(
            self.target, dict(node=self.editor_ref, relation="containedEditor"), self.basis(batch, **changes)
        )

    def test_exact_batch_and_immutable_selection_without_text_focus_or_labels(self):
        batch = self.collect()
        selected = self.selected(batch)
        batch["nodes"][0]["values"]["role"] = "changed caller copy"
        selected.actuator["node"] = "invented"
        selected.basis[0]["fields"].append("name")
        selected.requested["node"] = "invented"
        self.owner.name = "renamed by application"
        self.backend.editor.text = "autocomplete formatting"
        self.backend.editor.states.add("FOCUSED")
        self.backend.editor.selections = [(0, 1)]
        self.backend.editor.interfaces.reverse()
        selected.check(self.ctx)
        self.assertEqual(selected.recipient, dict(self.target, node=self.editor_ref))
        self.assertEqual(selected.actuator["node"], self.editor_ref)
        self.assertNotIn("name", selected.basis[0]["fields"])
        self.assertTrue({self.owner_ref, self.editor_ref} <= self.store.pins)
        self.assertIsInstance(hash(selected.signature), int)
        self.assertFalse(
            any(
                name in ("set_text_contents", "insert_text", "delete_text", "grab_focus")
                for name, _, _ in self.ax.calls
            )
        )

    def test_new_observation_id_preserves_structurally_identical_route(self):
        first = self.selected()
        second = self.selected()
        self.assertNotEqual(first.basis, second.basis)
        self.assertEqual(first.signature, second.signature)
        self.assertTrue(first.equivalent(second))
        self.owner.role = "spin button"
        third = self.selected()
        self.assertNotEqual(first.signature, third.signature)

    def test_retention_does_not_resample_the_returned_observation(self):
        batch = self.facts.collect(self.target, dict(fields=["role"], maxNodes=100, maxDepth=4), self.ctx)
        self.owner.role = "different native role"
        self.selection.retain(batch)
        basis = [
            dict(observed=batch["observed"]["id"], node=self.owner_ref, fields=["role"], actions=False, relations=[])
        ]
        selected = self.selection.select(self.target, dict(node=self.editor_ref, relation="containedEditor"), basis)
        with self.assertRaises(Changed):
            selected.check(self.ctx)

    def test_changed_native_object_with_same_name_role_is_not_the_same_child(self):
        selected = self.selected()
        replacement = Node(
            "entry",
            self.backend.editor.name,
            interfaces=list(self.backend.editor.interfaces),
            states=set(self.backend.editor.states),
            parent=self.owner,
        )
        self.owner.children[:] = [replacement]
        with self.assertRaises(Changed):
            selected.check(self.ctx)

    def test_disabled_or_changed_interfaces_invalidate_structure(self):
        selected = self.selected()
        self.backend.editor.states.discard("EDITABLE")
        with self.assertRaises(Changed):
            selected.check(self.ctx)
        self.backend.editor.states.add("EDITABLE")
        self.backend.editor.interfaces.remove("EditableText")
        with self.assertRaises(Changed):
            selected.check(self.ctx)

    def test_explicit_read_only_state_is_structural_even_with_editable_still_true(self):
        batch = self.collect()
        basis = self.basis(batch)
        basis[1]["fields"].append("readOnly")
        selected = self.selection.select(self.target, dict(node=self.editor_ref, relation="containedEditor"), basis)
        selected.check(self.ctx)
        self.backend.editor.states.add("READ_ONLY")
        self.assertIn("EDITABLE", self.backend.editor.states)
        with self.assertRaises(Changed):
            selected.check(self.ctx)

    def test_sensitive_only_receivers_keep_the_actual_enabled_evidence(self):
        for obj in (self.owner, self.backend.editor):
            obj.states.discard("ENABLED")
            obj.states.add("SENSITIVE")
        batch = self.collect()
        basis = self.basis(batch)
        for dependency in basis:
            dependency["fields"].append("sensitive")
        selected = self.selection.select(self.target, dict(node=self.editor_ref, relation="containedEditor"), basis)
        selected.check(self.ctx)
        for raw in batch["nodes"]:
            if raw["node"] in (self.owner_ref, self.editor_ref):
                self.assertFalse(raw["values"]["enabled"])
                self.assertTrue(raw["values"]["sensitive"])
        self.backend.editor.states.discard("SENSITIVE")
        with self.assertRaises(Changed):
            selected.check(self.ctx)

    def test_action_descriptions_are_not_guards_but_action_names_are(self):
        selected = self.selected(actions=True)
        self.owner.actions = [("press", "A localized explanation")]
        selected.check(self.ctx)
        self.owner.actions = [("activate", "A localized explanation")]
        with self.assertRaises(Changed):
            selected.check(self.ctx)

    def test_unavailable_observation_is_neither_absent_nor_retroactively_valid(self):
        self.owner.errors["get_role_name"] = NativeError("not available")
        batch = self.collect()
        self.owner.errors.clear()
        with self.assertRaises(Failure) as error:
            self.selected(batch)
        self.assertEqual(error.exception.code, "observation_incomplete")
        selected = self.selected()
        self.backend.editor.errors["get_interfaces"] = NativeError("provider is busy")
        with self.assertRaises(Failure) as error:
            selected.check(self.ctx)
        self.assertEqual(error.exception.code, "observation_incomplete")
        self.assertNotIsInstance(error.exception, Changed)

    def test_relation_pages_keep_their_own_offsets_and_object_identity(self):
        others = [Node("label", str(index), parent=self.owner) for index in range(129)]
        self.owner.children += others
        batch = self.collect(maxNodes=1, maxDepth=0)
        edge = next(edge for edge in batch["relations"] if edge["property"] == "children")
        page = self.facts.read_relation(
            self.target, dict(property="children", cursor=edge["cursor"], limit=128), self.ctx
        )
        self.selection.retain(page)
        basis = [
            dict(
                observed=source["observed"]["id"], node=self.owner_ref, fields=[], actions=False, relations=["children"]
            )
            for source in (batch, page)
        ]
        selected = self.selection.select(self.target, dict(node=self.editor_ref, relation="containedEditor"), basis)
        selected.check(self.ctx)
        self.owner.children[-1] = Node("label", others[-1].name, parent=self.owner)
        with self.assertRaises(Changed):
            selected.check(self.ctx)

    def test_unread_field_unknown_observation_and_foreign_surface_are_rejected(self):
        batch = self.collect(fields=["role"])
        with self.assertRaisesRegex(Failure, "not collected"):
            self.selected(batch)
        basis = self.basis(batch)
        basis[0]["observed"] = "invented"
        with self.assertRaisesRegex(Failure, "not retained"):
            self.selection.select(self.target, dict(node=self.editor_ref, relation="containedEditor"), basis)
        foreign = deepcopy(batch)
        foreign["surface"] = "another-surface"
        with self.assertRaises(Failure):
            self.selection.retain(foreign)
        foreign = deepcopy(batch)
        foreign["session"] = "another-session"
        with self.assertRaises(Failure):
            self.selection.retain(foreign)

    def test_lifetime_reuse_and_budget_failure_are_explicit(self):
        batch = self.collect()
        selected = self.selected(batch)
        with self.assertRaisesRegex(Failure, "reused"):
            self.selection.retain(batch)
        self.selection.clear()
        with self.assertRaisesRegex(Failure, "active write"):
            selected.check(self.ctx)
        self.selection = NativeSelection(
            lambda: self.execution.current, self.execution.session, self.ax, self.store, self.facts
        )
        pins = set(self.store.pins)
        with patch("computer_use_linux.control.native_selection.MAX_BYTES", 1):
            with self.assertRaises(Failure) as error:
                self.selection.retain(batch)
        self.assertEqual(error.exception.code, "observation_incomplete")
        self.assertEqual(self.selection.snapshots, {})
        self.assertEqual(self.store.pins, pins)
        self.selection.retain(batch)
        selected = self.selected(batch)
        self.execution.current.token = "another-operation"
        with self.assertRaisesRegex(Failure, "another operation"):
            selected.check(self.ctx)

    def test_request_shape_disallows_non_structural_and_invented_selection_routes(self):
        batch = self.collect()
        valid = dict(actuator=dict(node=self.editor_ref, relation="containedEditor"), basis=self.basis(batch))
        self.assertEqual(decode_selection_request({}), (None, []))
        for relation in ("containedEditor", "controlPart"):
            supported = dict(valid, actuator=dict(node=self.editor_ref, relation=relation))
            self.assertEqual(decode_selection_request(supported), (supported["actuator"], supported["basis"]))
        for name in ("name", "Text.text", "focused", "Text.selections"):
            invalid = deepcopy(valid)
            invalid["basis"][0]["fields"] = [name]
            with self.subTest(name=name), self.assertRaises(Failure):
                decode_selection_request(invalid)
        for invalid in (
            {"actuator": valid["actuator"]},
            {"basis": valid["basis"]},
            dict(valid, actuator=dict(node=self.editor_ref, relation="scrollPointer")),
            dict(valid, basis=valid["basis"] + valid["basis"]),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(Failure):
                decode_selection_request(invalid)


if __name__ == "__main__":
    unittest.main()
