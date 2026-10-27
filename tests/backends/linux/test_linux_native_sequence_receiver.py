"""Continuous native input keeps the actual receiver, including App scope.

The OS provider seam supplies objects and effects; production v5 dispatch and
X11 key pairing remain intact. GUI acceptance separately checks GTK and Qt.
"""

import gc
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from weakref import WeakValueDictionary, ref

from linux_native_provider import NativeError, Node, PasteBackend, TypedTextBackend

import backends.linux.test_linux_native as base
from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.control.native_sequence_receiver import NativeSequenceReceiver
from computer_use_linux.runtime.native import NativeExecution


class LinuxNativeSequenceReceiverTests(unittest.TestCase):
    def setUp(self):
        self.pid = patch("computer_use_linux.windowing.native_directory.process_start", return_value="generation")
        self.pid.start()
        self.make_backend()

    def make_backend(self, backend=TypedTextBackend):
        if hasattr(self, "driver"):
            self.driver.close()
        self.b = backend()
        self.driver = NativeExecution(self.b, "session")
        self.b.desktop.foreground = self.b.desktop.keyboard_window = 11
        self.b.editor.states.add("FOCUSED")
        self.b.other_editor.states.discard("FOCUSED")
        self.sibling = Node(
            "entry",
            "Another control",
            "unchanged",
            parent=self.b.window,
            interfaces=["Text", "Component"],
            states={"ENABLED", "SHOWING", "EDITABLE", "FOCUSABLE"},
        )
        self.b.window.children.append(self.sibling)
        app = self.driver.directory.applications(base.context())["applications"][0]["id"]
        row = self.driver.directory.read(app, base.context())["surfaces"][0]
        self.target = base.scope(instance=row["instance"], rootWindow=row["window"], surface=row["surface"])

    def tearDown(self):
        self.driver.close()
        self.pid.stop()

    def switch_control(self):
        self.b.editor.states.discard("FOCUSED")
        self.sibling.states.add("FOCUSED")

    def setup_operation(self, kind="insert", value="ab", *, element=False):
        target = dict(self.target)
        if element:
            target["node"] = self.driver.store.keep(target["surface"], self.b.editor)
        intent = dict(kind=kind, parameters={("key" if kind == "key" else "text"): value})
        self.operation = self.driver.perform("begin", dict(mode="write", target=target, intent=intent), base.context())[
            "operation"
        ]
        facts = self.driver.perform(
            "inspect",
            dict(operation=self.operation, target=target, query=dict(kind="inputContext", within=[])),
            base.context(),
        )
        primitive = next(p for p in facts["primitives"] if p["unit"] in ("key_sequence", "clipboard_paste"))
        return dict(kind=primitive["unit"], primitiveId=primitive["id"], target=target, foreground=False)

    def send(self, command, *, ctx=None):
        return self.driver.perform(
            "execute", dict(operation=self.operation, step=1, command=command), ctx or base.context()
        )

    def hook(self, callback):
        previous = self.b.desktop.event_hook

        def event(kind, detail):
            previous(kind, detail)
            callback(kind, detail)

        self.b.desktop.event_hook = event

    def test_app_text_stops_after_first_character_when_same_window_control_changes(self):
        command = self.setup_operation(value="abc")
        self.hook(lambda kind, detail: self.switch_control() if (kind, detail) == (2, "a") else None)
        result = self.send(command)
        self.assertEqual(self.b.editor.text, "abacd")
        self.assertEqual(self.sibling.text, "unchanged")
        self.assertEqual(self.b.desktop.events, [(2, "a"), (3, "a")])
        self.assertEqual(
            (result["dispatch"], result["errorCode"], result["retryReason"], result["cleanup"]),
            ("unknown", "input_unavailable", None, "complete"),
        )

    def test_app_chord_releases_modifier_without_sending_key_to_new_control(self):
        command = self.setup_operation(kind="key", value="ctrl+v")
        self.hook(lambda kind, detail: self.switch_control() if (kind, detail) == (2, "ctrl") else None)
        result = self.send(command)
        self.assertEqual(self.b.desktop.events, [(2, "ctrl"), (3, "ctrl")])
        self.assertEqual(result["dispatch"], "unknown")
        self.assertIsNone(result["retryReason"])
        self.assertFalse(self.b.desktop.keys)

    def test_app_paste_pins_before_clipboard_publication(self):
        self.make_backend(PasteBackend)
        command = self.setup_operation(kind="paste", value="private text")
        self.b.clipboard.before_send = self.switch_control
        result = self.send(command)
        self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(result["dispatch"], "notDispatched")
        self.assertIsNone(result["retryReason"])
        self.assertFalse(self.b.clipboard.owned)
        self.assertEqual(self.sibling.text, "unchanged")

    def test_focus_is_captured_after_inspect_not_from_prior_observation(self):
        command = self.setup_operation(kind="key", value="Return")
        self.switch_control()
        result = self.send(command)
        self.assertEqual(result["dispatch"], "dispatched")
        self.assertEqual(self.b.desktop.events, [(2, "Return"), (3, "Return")])

    def test_last_key_can_move_focus_and_next_primitive_recaptures(self):
        command = self.setup_operation(kind="key", value="Tab")
        self.hook(lambda kind, detail: self.switch_control() if (kind, detail) == (2, "Tab") else None)
        self.assertEqual(self.send(command)["dispatch"], "dispatched")
        self.driver.finish(self.operation)
        command = self.setup_operation(kind="key", value="Return")
        self.assertEqual(self.send(command)["dispatch"], "dispatched")

    def test_no_ax_exact_child_xid_change_stops_inside_same_native_window(self):
        self.b.desktop.items[0].accessible = None
        command = self.setup_operation(kind="key", value="ctrl+v")
        self.b.desktop.keyboard_child = 1101
        self.hook(
            lambda kind, detail: (
                setattr(self.b.desktop, "keyboard_child", 1102) if (kind, detail) == (2, "ctrl") else None
            )
        )
        result = self.send(command)
        self.assertEqual(self.b.desktop.keyboard_window, 11)
        self.assertEqual(self.b.desktop.events, [(2, "ctrl"), (3, "ctrl")])
        self.assertEqual(result["errorCode"], "input_unavailable")

    def test_defunct_or_reparented_pinned_object_never_downgrades(self):
        for change in ("defunct", "reparent"):
            with self.subTest(change=change):
                self.make_backend()
                command = self.setup_operation(value="ab")

                def mutate(kind, detail, *, change=change):
                    if (kind, detail) == (2, "a"):
                        if change == "defunct":
                            self.b.editor.dead = True
                        else:
                            self.b.editor.parent = self.b.other_window

                self.hook(mutate)
                result = self.send(command)
                self.assertEqual(self.b.desktop.events, [(2, "a"), (3, "a")])
                self.assertEqual(result["dispatch"], "unknown")
                self.assertIsNone(result["retryReason"])
                self.assertEqual(result["cleanup"], "complete")

    def capture(self, **kwargs):
        return NativeSequenceReceiver(
            self.driver.b, self.b.desktop.items[0], base.context(), recipients=self.driver.keyboard_recipients, **kwargs
        )

    def test_initial_walk_is_not_repeated_per_character(self):
        command = self.setup_operation(value="abc")
        self.b.ax.calls.clear()
        traversals_at_keys = []
        self.hook(
            lambda kind, detail: (
                traversals_at_keys.append(sum(c[0] == "get_child_count" for c in self.b.ax.calls))
                if kind == 2
                else None
            )
        )
        self.assertEqual(self.send(command)["dispatch"], "dispatched")
        self.assertTrue(traversals_at_keys[0] > 0)
        self.assertEqual(len(set(traversals_at_keys)), 1)

    def test_unique_deepest_focused_object_is_actual_recipient(self):
        self.b.window.states.add("FOCUSED")
        self.b.ax.focus_events = {self.b.ax.native_key(self.b.window): self.b.window}
        receiver = self.capture()
        self.assertIs(receiver.obj, self.b.editor)
        self.assertIs(receiver.root, self.b.window)
        self.b.editor.states.discard("FOCUSED")
        with self.assertRaises(Failure):
            receiver.validate()

    def test_focus_event_is_refreshed_and_avoids_large_initial_tree_queries(self):
        self.b.ax.focus_events = {self.b.ax.native_key(self.b.editor): self.b.editor}
        self.b.window.errors["get_child_count"] = NativeError("Large provider should not be walked")
        self.b.ax.calls.clear()
        receiver = self.capture()
        self.assertIs(receiver.obj, self.b.editor)
        self.assertEqual(receiver.source, "event")
        self.assertFalse(any(c[0] == "get_child_count" for c in self.b.ax.calls))

    def test_only_window_focused_is_unknown_not_a_concrete_control(self):
        self.b.editor.states.discard("FOCUSED")
        self.b.window.states.add("FOCUSED")
        self.b.ax.focus_events = {self.b.ax.native_key(self.b.window): self.b.window}
        receiver = self.capture()
        self.assertIsNone(receiver.obj)
        self.assertEqual(receiver.source, "x11")
        self.b.window.states.discard("FOCUSED")
        self.b.editor.states.add("FOCUSED")
        receiver.validate()

    def test_later_unfocused_container_event_preserves_live_editor_candidate(self):
        container = Node("panel", "Container", parent=self.b.window, interfaces=[], states={"ENABLED"})
        self.b.ax.focus_events = {self.b.ax.native_key(obj): obj for obj in (self.b.editor, container)}
        self.b.window.errors["get_child_count"] = NativeError("Must not enumerate virtual content")
        receiver = self.capture()
        self.assertIs(receiver.obj, self.b.editor)
        self.assertEqual(receiver.source, "event")
        receiver.validate()

    def test_candidate_events_from_another_window_do_not_erase_current_focus(self):
        self.b.other_editor.states.add("FOCUSED")
        self.b.ax.focus_events = {self.b.ax.native_key(obj): obj for obj in (self.b.editor, self.b.other_editor)}
        self.b.window.errors["get_child_count"] = NativeError("Must not scan unrelated content")
        self.assertIs(self.capture().obj, self.b.editor)

    def test_independent_live_event_candidates_are_ambiguous(self):
        self.sibling.states.add("FOCUSED")
        self.b.ax.focus_events = {self.b.ax.native_key(obj): obj for obj in (self.b.editor, self.sibling)}
        with self.assertRaisesRegex(Failure, "multiple independent"):
            self.capture()

    def test_known_independent_focused_objects_refuse_before_any_key(self):
        self.sibling.states.add("FOCUSED")
        self.b.ax.focus_events = {self.b.ax.native_key(obj): obj for obj in (self.b.editor, self.sibling)}
        command = self.setup_operation(value="a")
        result = self.send(command)
        self.assertEqual(result["dispatch"], "notDispatched")
        self.assertIn("multiple independent", result["detail"])
        self.assertEqual(self.b.desktop.events, [])

    def test_stale_or_other_window_focus_event_is_only_a_hint(self):
        for event in (self.sibling, self.b.other_editor):
            with self.subTest(event=event.name):
                self.b.ax.focus_events = {self.b.ax.native_key(event): event}
                self.b.other_editor.states.add("FOCUSED")
                self.assertIs(self.capture().obj, self.b.editor)

    def test_new_focus_event_cannot_override_pinned_object_with_stale_focused_flag(self):
        receiver = self.capture()
        self.sibling.states.add("FOCUSED")
        self.b.ax.focus_events = {self.b.ax.native_key(self.sibling): self.sibling}
        with self.assertRaisesRegex(Failure, "Another control"):
            receiver.validate()

    def test_specified_scope_does_not_authorize_an_independent_current_widget(self):
        self.sibling.states.add("FOCUSED")
        self.b.window.children.insert(0, self.sibling)
        command = self.setup_operation(kind="key", value="Return", element=True)
        result = self.send(command)
        self.assertEqual(result["dispatch"], "notDispatched")
        self.assertIn("independent control", result["detail"])
        self.assertEqual(self.b.desktop.events, [])

    def test_specified_target_focus_event_does_not_hide_independent_first_widget(self):
        self.sibling.states.add("FOCUSED")
        self.b.window.children.insert(0, self.sibling)
        self.b.ax.focus_events = {self.b.ax.native_key(self.b.editor): self.b.editor}
        command = self.setup_operation(value="ab", element=True)
        result = self.send(command)
        self.assertEqual(result["dispatch"], "notDispatched")
        self.assertIn("multiple independent", result["detail"])
        self.assertEqual(self.b.desktop.events, [])

    def test_specified_scope_pins_its_deeper_focused_editor(self):
        owner = Node(
            "panel",
            "Focused scope",
            children=[self.b.editor],
            parent=self.b.window,
            states={"ENABLED", "SHOWING", "FOCUSED"},
        )
        self.b.window.children = [owner]
        self.b.editor.parent = owner
        receiver = self.capture(obj=owner)
        self.assertIs(receiver.obj, self.b.editor)
        self.b.editor.states.discard("FOCUSED")
        with self.assertRaisesRegex(Failure, "lost keyboard focus"):
            receiver.validate()

    def test_same_primitive_pointer_focus_starts_at_the_addressed_subtree(self):
        owner = Node(
            "panel",
            "Focus scope",
            children=[self.b.editor],
            parent=self.b.window,
            states={"ENABLED", "SHOWING", "FOCUSED"},
        )
        self.b.editor.parent = owner
        self.b.window.children = [*(Node("label", str(i), parent=self.b.window) for i in range(450)), owner]
        self.b.window.errors["get_child_count"] = NativeError("Must not start another whole-window census")
        receiver = self.capture(obj=owner, pointer_focused=True)
        self.assertIs(receiver.obj, self.b.editor)
        self.assertIn(self.b.ax.native_key(owner), receiver.parent_keys)

    def test_pointer_subtree_still_rejects_known_independent_focus(self):
        self.sibling.states.add("FOCUSED")
        self.b.ax.focus_events = {self.b.ax.native_key(self.sibling): self.sibling}
        with self.assertRaisesRegex(Failure, "independent"):
            self.capture(obj=self.b.editor, pointer_focused=True)

    def closed_popup(self):
        combo = Node("combo box", "Scale", parent=self.b.window, states={"ENABLED", "SHOWING", "EXPANDABLE"})
        popup = Node("list", "Choices", parent=combo, states={"ENABLED"})
        stale = Node(
            "list item",
            "Retained selection",
            parent=popup,
            states={"ENABLED", "SHOWING", "VISIBLE", "FOCUSED", "SELECTED"},
        )
        combo.children, popup.children = [popup], [stale]
        self.b.window.children.insert(0, combo)
        return popup, stale

    def test_closed_popup_retained_child_focus_does_not_replace_current_recipient(self):
        popup, stale = self.closed_popup()
        popup.errors["get_child_count"] = NativeError("Hidden branch must not be enumerated")
        for known_event in (False, True):
            with self.subTest(known_event=known_event):
                self.b.ax.focus_events = {self.b.ax.native_key(stale): stale} if known_event else {}
                self.assertIs(self.capture(obj=self.b.editor).obj, self.b.editor)

    def test_known_hidden_focus_is_ignored_only_while_its_ancestor_is_hidden(self):
        popup, stale = self.closed_popup()
        receiver = self.capture(obj=self.b.editor)
        self.b.ax.focus_events = {self.b.ax.native_key(stale): stale}
        receiver.validate()
        popup.states.add("SHOWING")
        with self.assertRaisesRegex(Failure, "Another control"):
            receiver.validate()
        with self.assertRaisesRegex(Failure, "independent"):
            self.capture(obj=self.b.editor)

    def test_collection_first_hidden_focus_continues_with_bounded_tree_discovery(self):
        popup, stale = self.closed_popup()
        self.b.window.interfaces.append("Collection")
        a_local = self.b.ax.A
        a_local.StateSet = lambda: NS(add=lambda value: None)
        a_local.CollectionMatchType = NS(ALL="all")
        a_local.CollectionSortOrder = NS(CANONICAL="canonical")
        a_local.MatchRule = NS(new=lambda *args: "focused rule")
        a_local.Collection = NS(get_matches=lambda *args: [stale])
        receiver = self.capture(obj=self.b.editor)
        self.assertIs(receiver.obj, self.b.editor)
        self.assertEqual(receiver.source, "tree")

    def test_unobservable_popup_ancestor_is_not_assumed_hidden(self):
        popup, stale = self.closed_popup()
        popup.errors["get_state_set"] = NativeError("Ancestor state unavailable")
        self.b.ax.focus_events = {self.b.ax.native_key(stale): stale}
        with self.assertRaisesRegex(Failure, "Cannot establish"):
            self.capture(obj=self.b.editor)

    def test_visible_only_independent_control_is_not_filtered_as_hidden(self):
        self.sibling.states.discard("SHOWING")
        self.sibling.states.update(("VISIBLE", "FOCUSED"))
        self.b.ax.focus_events = {self.b.ax.native_key(self.sibling): self.sibling}
        with self.assertRaisesRegex(Failure, "independent"):
            self.capture(obj=self.b.editor)

    def test_current_control_hidden_during_text_stops_without_replay(self):
        owner = Node("panel", "Input scope", children=[self.b.editor], parent=self.b.window)
        self.b.window.children[0], self.b.editor.parent = owner, owner
        command = self.setup_operation(value="ab")
        self.hook(lambda kind, detail: owner.states.discard("SHOWING") if (kind, detail) == (2, "a") else None)
        result = self.send(command)
        self.assertEqual(self.b.desktop.events, [(2, "a"), (3, "a")])
        self.assertEqual(
            (result["dispatch"], result["errorCode"], result["retryReason"]), ("unknown", "input_unavailable", None)
        )
        self.assertEqual(self.sibling.text, "unchanged")

    def test_parent_proxy_identity_is_pinned_only_for_the_primitive(self):
        class Proxy:
            def __init__(self, remote):
                self.remote = remote

        root, editor, cache = Proxy("window"), Proxy("editor"), WeakValueDictionary()

        def parent(obj):
            if obj is editor:
                if "host" not in cache:
                    cache["host"] = host = Proxy("host")
                    return host
                return cache["host"]
            return root

        receiver = object.__new__(NativeSequenceReceiver)
        receiver.objects, receiver.root = {}, root
        receiver.ax = NS(
            native_key=id,
            live=lambda obj: None,
            call=lambda fn, *args: fn(*args),
            A=NS(Accessible=NS(get_parent=parent)),
        )
        first = receiver._path(editor)
        host = ref(cache["host"])
        gc.collect()
        self.assertIsNotNone(host(), "A path key must retain the native proxy it identifies")
        self.assertEqual(receiver._path(editor), first, "The same remote parent must keep one live proxy identity")
        del receiver
        gc.collect()
        self.assertIsNone(host(), "Identity pins must not survive the continuous primitive")

    def test_census_or_focus_notification_alone_never_seeds_a_recipient_hint(self):
        self.capture(obj=self.b.editor)
        self.assertIsNone(self.driver.keyboard_recipients.hint)
        self.b.ax.focus_events = {self.b.ax.native_key(self.b.editor): self.b.editor}
        self.capture(obj=self.b.editor)
        self.assertIsNone(self.driver.keyboard_recipients.hint)

    def test_pointer_context_is_revalidated_without_unrelated_focused_census(self):
        self.capture(obj=self.b.editor, pointer_focused=True)
        stale = Node(
            "page tab",
            "Inactive view",
            parent=self.b.window,
            states={"ENABLED", "SHOWING", "VISIBLE", "FOCUSED", "SELECTED"},
        )
        self.b.window.children.insert(0, stale)
        self.b.window.errors["get_child_count"] = NativeError("Known recipient does not need another census")
        receiver = self.capture(obj=self.b.editor)
        self.assertIs(receiver.obj, self.b.editor)
        self.assertEqual(receiver.source, "hint")
        self.assertIs(self.driver.keyboard_recipients.hint[2], self.b.editor)

    def test_pointer_hint_does_not_authorize_new_independent_focus(self):
        self.capture(obj=self.b.editor, pointer_focused=True)
        self.sibling.states.add("FOCUSED")
        self.b.ax.focus_events = {self.b.ax.native_key(self.sibling): self.sibling}
        with self.assertRaisesRegex(Failure, "Another control"):
            self.capture(obj=self.b.editor)
        self.assertIsNone(self.driver.keyboard_recipients.hint)

    def test_pointer_hint_cannot_authorize_lost_focus_defunct_reparent_or_cancelled_target(self):
        for change in ("focus", "defunct", "parent", "cancelled"):
            with self.subTest(change=change):
                self.make_backend()
                self.capture(obj=self.b.editor, pointer_focused=True)
                ctx = base.context()
                if change == "focus":
                    self.switch_control()
                elif change == "defunct":
                    self.b.editor.dead = True
                elif change == "parent":
                    self.b.editor.parent = self.b.other_window
                else:
                    ctx.cancelled.set()
                with self.assertRaises(Failure):
                    NativeSequenceReceiver(
                        self.driver.b,
                        self.b.desktop.items[0],
                        ctx,
                        recipients=self.driver.keyboard_recipients,
                        obj=self.b.editor,
                    )
                self.assertIsNone(self.driver.keyboard_recipients.hint)
                self.assertEqual(self.b.desktop.events, [])

    def test_hint_changed_xid_or_stale_old_object_does_not_lock_a_new_request(self):
        self.capture(obj=self.b.editor, pointer_focused=True)
        self.b.desktop.keyboard_child = 1102
        receiver = self.capture(obj=self.b.editor)
        self.assertNotEqual(receiver.source, "hint")
        self.assertIsNone(self.driver.keyboard_recipients.hint)
        self.capture(obj=self.b.editor, pointer_focused=True)
        self.switch_control()
        self.b.editor.dead = True
        self.b.window.children = [self.sibling]
        receiver = self.capture(obj=self.sibling)
        self.assertIs(receiver.obj, self.sibling)
        self.assertNotEqual(receiver.source, "hint")
        self.assertIsNone(self.driver.keyboard_recipients.hint)

    def test_native_focus_loss_then_fake_scope_focus_cannot_revive_pointer_hint(self):
        self.capture(obj=self.b.editor, pointer_focused=True)
        self.switch_control()
        self.b.ax.focus_lost()
        self.b.editor.states.add("FOCUSED")  # AX-only scope focus; native widget is still the sibling.
        self.b.window.children.insert(0, self.sibling)
        command = self.setup_operation(kind="key", value="Return", element=True)
        result = self.send(command)
        self.assertEqual(result["dispatch"], "notDispatched")
        self.assertIn("independent", result["detail"])
        self.assertIsNone(self.driver.keyboard_recipients.hint)
        self.assertEqual(self.b.desktop.events, [])

    def test_native_focus_loss_while_checking_local_hint_entry_cannot_revive_it(self):
        self.capture(obj=self.b.editor, pointer_focused=True)
        self.sibling.states.add("FOCUSED")
        self.b.window.children.insert(0, self.sibling)
        pending = [True]

        def loss(obj):
            if obj is self.b.editor and pending:
                pending.pop()
                self.b.ax.focus_lost()

        self.b.ax.hooks["get_state_set"] = loss
        with self.assertRaisesRegex(Failure, "independent"):
            self.capture(obj=self.b.editor)
        self.assertIsNone(self.driver.keyboard_recipients.hint)

    def test_new_pointer_establishes_a_new_object_and_close_drops_the_hint(self):
        self.capture(obj=self.b.editor, pointer_focused=True)
        self.switch_control()
        receiver = self.capture(obj=self.sibling, pointer_focused=True)
        self.assertIs(receiver.obj, self.sibling)
        self.assertIs(self.driver.keyboard_recipients.hint[2], self.sibling)
        self.driver.close()
        self.assertIsNone(self.driver.keyboard_recipients.hint)
        self.assertIsNone(self.b.ax.focus_lost)

    def test_specified_mandatory_path_uses_request_context(self):
        ctx = base.context()
        contexts = []
        self.b.ax.hooks["get_parent"] = lambda obj: contexts.append(self.b.ax.ctx)
        receiver = NativeSequenceReceiver(
            self.driver.b, self.b.desktop.items[0], ctx, recipients=self.driver.keyboard_recipients, obj=self.b.editor
        )
        self.assertIs(receiver.obj, self.b.editor)
        self.assertIs(contexts[0], ctx, "Required target identity must not consume the optional discovery budget")
        self.assertTrue(all(item is ctx for item in contexts))

    def test_specified_known_focus_checks_use_request_context_not_discovery_allowance(self):
        ctx = base.context()
        self.b.ax.focus_events = {self.b.ax.native_key(self.b.editor): self.b.editor}
        contexts = []
        self.b.ax.hooks["get_state_set"] = lambda obj: contexts.append(self.b.ax.ctx)
        receiver = NativeSequenceReceiver(
            self.driver.b, self.b.desktop.items[0], ctx, recipients=self.driver.keyboard_recipients, obj=self.b.editor
        )
        self.assertIs(receiver.obj, self.b.editor)
        self.assertIs(contexts[0], ctx, "Known focus identity/state/path are required boundary checks")
        self.assertTrue(all(item is ctx for item in contexts))

    def test_specified_known_focus_checks_keep_request_deadline_and_cancellation(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                ctx = base.context()
                self.b.ax.focus_events = {self.b.ax.native_key(self.b.editor): self.b.editor}

                def expire(obj, *, cancelled=cancelled, ctx=ctx):
                    if cancelled:
                        ctx.cancelled.set()
                    else:
                        ctx.deadline = 0

                self.b.ax.hooks["get_state_set"] = expire
                with self.assertRaises(Failure) as error:
                    NativeSequenceReceiver(
                        self.driver.b,
                        self.b.desktop.items[0],
                        ctx,
                        recipients=self.driver.keyboard_recipients,
                        obj=self.b.editor,
                    )
                self.assertEqual(error.exception.code, "cancelled" if cancelled else "deadline_exceeded")

    def test_specified_recipient_discovery_remains_node_bounded_and_never_downgrades(self):
        with patch.object(NativeSequenceReceiver, "SEARCH_NODES", 1):
            with self.assertRaisesRegex(Failure, "Cannot establish"):
                self.capture(obj=self.b.editor)

    def test_confirmed_receiver_does_not_require_unrelated_tree_completeness(self):
        self.sibling.errors["get_child_count"] = NativeError("Incomplete subtree")
        self.assertIs(self.capture().obj, self.b.editor)
        self.assertFalse(any(obj is self.sibling for _, obj, _ in self.b.ax.calls))

    def test_slow_unrelated_tail_does_not_consume_input_discovery_budget(self):
        self.b.window.children.extend(Node("label", str(i), parent=self.b.window) for i in range(450))

        def slow_tail(obj):
            if obj is self.sibling:
                raise AssertionError("A confirmed recipient must stop unrelated tree queries")

        self.b.ax.hooks["get_child_count"] = slow_tail
        command = self.setup_operation(value="ab")
        result = self.send(command)
        self.assertEqual(result["dispatch"], "dispatched")
        self.assertEqual(self.sibling.text, "unchanged")

    def test_many_same_level_labels_are_not_prefetched_before_the_first_editor(self):
        self.b.window.children.extend(Node("label", str(i), parent=self.b.window) for i in range(450))

        def no_sibling_prefetch(obj, index):
            if obj is self.b.window and index:
                raise AssertionError("Unrelated sibling objects must not be fetched before the first editor")

        self.b.ax.hooks["get_child_at_index"] = no_sibling_prefetch
        command = self.setup_operation(value="ab")
        result = self.send(command)
        self.assertEqual(result["dispatch"], "dispatched")
        self.assertEqual(self.sibling.text, "unchanged")

    def test_large_child_count_does_not_disable_a_first_focused_editor(self):
        self.b.window.errors["get_child_count"] = 2000
        receiver = self.capture()
        self.assertIs(receiver.obj, self.b.editor)
        self.assertEqual(receiver.source, "tree")

    def test_nested_focused_scope_selects_deepest_live_recipient(self):
        container = Node(
            "panel",
            "Focus scope",
            children=[self.b.editor, self.sibling],
            parent=self.b.window,
            states={"ENABLED", "SHOWING", "FOCUSED"},
        )
        self.b.window.children = [container]
        self.b.editor.parent = self.sibling.parent = container
        self.sibling.errors["get_child_count"] = NativeError("Unrelated descendant must not be scanned")
        receiver = self.capture()
        self.assertIs(receiver.obj, self.b.editor)
        self.assertIn(self.b.ax.native_key(container), receiver.parent_keys)
        self.switch_control()
        with self.assertRaisesRegex(Failure, "lost keyboard focus"):
            receiver.validate()

    def test_incomplete_search_before_a_recipient_still_refuses(self):
        self.b.editor.states.discard("FOCUSED")
        self.b.editor.errors["get_child_count"] = Failure(
            "Input discovery budget exhausted before any receiver", "input_unavailable"
        )
        with self.assertRaises(Failure):
            self.capture()

    def test_complete_no_focused_object_keeps_visual_route(self):
        self.b.editor.states.discard("FOCUSED")
        receiver = self.capture()
        self.assertIsNone(receiver.obj)
        receiver.validate()

    def test_local_node_budget_before_a_recipient_preserves_exact_xid_route(self):
        with patch.object(NativeSequenceReceiver, "SEARCH_NODES", 1):
            receiver = self.capture()
        self.assertIsNone(receiver.obj)
        self.assertIs(receiver.root, self.b.window)
        self.assertEqual(receiver.source, "x11")
        receiver.validate()
        self.b.desktop.keyboard_child = 1102
        with self.assertRaisesRegex(Failure, "native keyboard focus changed"):
            receiver.validate()

    def expire_probe(self, obj):
        self.b.ax.ctx.deadline = 0
        self.b.ax.ctx.check()

    def test_local_time_budget_before_any_recipient_does_not_block_app_text(self):
        command = self.setup_operation(value="ab")
        self.b.ax.hooks["get_child_count"] = self.expire_probe
        result = self.send(command)
        self.assertEqual(result["dispatch"], "dispatched")
        self.assertEqual(self.b.desktop.events, [(2, "a"), (3, "a"), (2, "b"), (3, "b")])
        self.assertEqual(self.sibling.text, "unchanged")

    def test_local_collection_budget_without_a_recipient_keeps_xid_boundary(self):
        self.b.window.interfaces.append("Collection")
        atspi = self.b.ax.A
        atspi.StateSet = lambda: NS(add=lambda value: None)
        atspi.CollectionMatchType = NS(ALL="all")
        atspi.CollectionSortOrder = NS(CANONICAL="canonical")
        atspi.MatchRule = NS(new=lambda *args: "focused rule")
        atspi.Collection = NS(get_matches=lambda *args: self.expire_probe(self.b.window))
        receiver = self.capture()
        self.assertIsNone(receiver.obj)
        self.assertIs(receiver.root, self.b.window)
        receiver.validate()

    def test_complete_empty_focus_query_also_stops_on_later_positive_evidence(self):
        self.b.editor.states.discard("FOCUSED")
        command = self.setup_operation(kind="key", value="ctrl+v")

        def focus(kind, detail):
            if (kind, detail) == (2, "ctrl"):
                self.sibling.states.add("FOCUSED")
                self.b.ax.focus_events = {self.b.ax.native_key(self.sibling): self.sibling}

        self.hook(focus)
        result = self.send(command)
        self.assertEqual(self.b.desktop.events, [(2, "ctrl"), (3, "ctrl")])
        self.assertEqual(
            (result["dispatch"], result["errorCode"], result["retryReason"]), ("unknown", "input_unavailable", None)
        )
        self.assertEqual(self.sibling.text, "unchanged")

    def test_incomplete_focused_subtree_never_downgrades_to_xid(self):
        self.b.ax.hooks["get_child_count"] = lambda obj: self.expire_probe(obj) if obj is self.b.editor else None
        with self.assertRaisesRegex(Failure, "initial focused-object evidence became incomplete"):
            self.capture()

    def test_local_event_budget_after_a_confirmed_candidate_never_downgrades(self):
        self.b.ax.focus_events = {self.b.ax.native_key(obj): obj for obj in (self.sibling, self.b.editor)}
        self.b.ax.hooks["get_state_set"] = lambda obj: self.expire_probe(obj) if obj is self.sibling else None
        with self.assertRaisesRegex(Failure, "Cannot establish one continuous keyboard recipient"):
            self.capture()

    def test_specified_mandatory_path_keeps_request_deadline_and_cancellation(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                ctx = base.context()

                def expire(obj, *, cancelled=cancelled, ctx=ctx):
                    if cancelled:
                        ctx.cancelled.set()
                    else:
                        ctx.deadline = 0

                self.b.ax.hooks["get_parent"] = expire
                with self.assertRaises(Failure) as error:
                    NativeSequenceReceiver(
                        self.driver.b,
                        self.b.desktop.items[0],
                        ctx,
                        recipients=self.driver.keyboard_recipients,
                        obj=self.b.editor,
                    )
                self.assertEqual(error.exception.code, "cancelled" if cancelled else "deadline_exceeded")

    def test_xid_probe_fallback_stops_on_new_same_window_focus_evidence(self):
        command = self.setup_operation(value="ab")
        self.b.ax.hooks["get_child_count"] = self.expire_probe

        def focus(kind, detail):
            if (kind, detail) == (2, "a"):
                self.switch_control()
                self.b.ax.focus_events = {self.b.ax.native_key(self.sibling): self.sibling}

        self.hook(focus)
        result = self.send(command)
        self.assertEqual(self.b.desktop.events, [(2, "a"), (3, "a")])
        self.assertEqual(
            (result["dispatch"], result["errorCode"], result["retryReason"]), ("unknown", "input_unavailable", None)
        )
        self.assertEqual(self.sibling.text, "unchanged")

    def test_xid_probe_does_not_ignore_same_key_focus_event_becoming_live(self):
        self.b.ax.focus_events = {self.b.ax.native_key(self.sibling): self.sibling}
        command = self.setup_operation(value="ab")
        self.b.ax.hooks["get_child_count"] = self.expire_probe
        self.hook(lambda kind, detail: self.switch_control() if (kind, detail) == (2, "a") else None)
        result = self.send(command)
        self.assertEqual(self.b.desktop.events, [(2, "a"), (3, "a")])
        self.assertEqual(
            (result["dispatch"], result["errorCode"], result["retryReason"]), ("unknown", "input_unavailable", None)
        )
        self.assertEqual(self.sibling.text, "unchanged")

    def test_arbitrary_deadline_failure_does_not_masquerade_as_local_probe_budget(self):
        self.b.editor.errors["get_child_count"] = Failure("Provider returned a deadline error", "deadline_exceeded")
        self.b.editor.states.discard("FOCUSED")
        with self.assertRaises(Failure):
            self.capture()

    def test_request_deadline_and_cancellation_are_never_optional(self):
        ctx = base.context()
        self.b.ax.hooks["get_child_count"] = lambda obj: setattr(ctx, "deadline", 0)
        with self.assertRaises(Failure) as error:
            NativeSequenceReceiver(
                self.driver.b, self.b.desktop.items[0], ctx, recipients=self.driver.keyboard_recipients
            )
        self.assertEqual(error.exception.code, "deadline_exceeded")
        ctx = base.context()
        self.b.ax.hooks["get_child_count"] = lambda obj: ctx.cancelled.set()
        with self.assertRaises(Failure) as error:
            NativeSequenceReceiver(
                self.driver.b, self.b.desktop.items[0], ctx, recipients=self.driver.keyboard_recipients
            )
        self.assertEqual(error.exception.code, "cancelled")

    def test_collection_is_only_an_initial_candidate_query(self):
        calls = []
        a = self.b.ax.A
        self.b.window.interfaces.append("Collection")
        a.StateSet = lambda: NS(add=lambda value: None)
        a.CollectionMatchType = NS(ALL="all")
        a.CollectionSortOrder = NS(CANONICAL="canonical")
        a.MatchRule = NS(new=lambda *args: "focused rule")
        a.Collection = NS(get_matches=lambda *args: calls.append(args) or [self.b.editor])
        receiver = self.capture()
        receiver.validate()
        receiver.validate()
        self.assertIs(receiver.obj, self.b.editor)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-2:], (1, True))
        self.assertFalse(any(c[0] == "get_child_count" for c in self.b.ax.calls))
        a.Collection.get_matches = lambda *args: [self.b.editor, self.sibling]
        with self.assertRaisesRegex(Failure, "query is incomplete"):
            self.capture()


if __name__ == "__main__":
    unittest.main()
