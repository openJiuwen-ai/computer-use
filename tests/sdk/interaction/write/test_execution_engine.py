"""Execution contracts exercise routing/effect boundaries independently of GUI names."""

import asyncio
import unittest
from dataclasses import replace

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.interaction.write.routes.policy import (
    Attempts,
    choose,
    has_independent_text_route,
    has_ready_route,
    route_key,
)
from computer_use.native.contracts import (
    Actuator,
    Binding,
    Channel,
    Cleanup,
    Confirmation,
    Dispatch,
    Facts,
    HostPolicy,
    InputContext,
    Intent,
    Knowledge as K,
    OpenedOperation,
    Primitive,
    StepResult,
)
from computer_use.native.lifecycle import CallLifecycle

TARGET = Binding("session", "document", "root", "surface", "field")
READY = InputContext(
    keyboard_surface="surface",
    focused_node="editor",
    focus_ancestors=("field",),
    editor="editor",
    editor_owner="field",
    window_route=K.YES,
    selection_known=K.YES,
)
OBJECT = Primitive("native.edit", "native_text_edit", Channel.OBJECT, K.YES, K.YES)
VALUE = Primitive("native.value", "native_value_replace", Channel.OBJECT, K.YES, K.YES)
EDITOR = Primitive("native.editor", "native_text_edit", Channel.EDITOR, K.YES, K.YES, background_preparation=K.YES)
EDITOR_CONTEXT = Primitive(
    "native.context", "prepare_receiver", Channel.EDITOR, K.YES, K.YES, background_preparation=K.YES
)
KEY = Primitive("keys", "key_sequence", Channel.KEYBOARD, K.YES, K.YES, background_preparation=K.YES)
POINTER = Primitive("pointer", "pointer_sequence", Channel.POINTER, K.YES, K.YES, background_preparation=K.YES)
INVOKE = Primitive("invoke", "native_action", Channel.OBJECT, K.YES, K.YES, action="AXPress")


def facts(*primitives, context=InputContext(), **changes):
    # This peer supplies per-unit readiness, just as a native collector does.
    # Policy itself must not recreate this from a global focus snapshot.
    target = changes.get("binding", TARGET)
    declared = []
    for p in primitives:
        pending = []
        if p.channel == Channel.KEYBOARD:
            receiver = (
                context.editor if context.editor_owner == target.node and context.editor is not None else target.node
            )
            ready = (
                context.window_route == K.YES
                and context.keyboard_surface == target.surface
                and (target.node is None or context.focused_node == receiver or receiver in context.focus_ancestors)
            )
            if not ready:
                pending.append("receiver")
        if p.channel == Channel.EDITOR and (context.editor is None or context.editor_owner != target.node):
            pending.append("receiver")
        if p.channel == Channel.POINTER:
            if context.geometry_current != K.YES:
                pending.append("geometry")
            if context.pointer_route != K.YES:
                pending.append("receiver")
        if "foreground" in p.pending and context.foreground_surface != target.surface:
            pending.append("foreground")
        if changes.get("enabled") == K.NO:
            pending.append("enabled")
        declared.append(replace(p, readiness=K.NO if pending else K.YES, pending=tuple(pending)))
    state = Facts(
        target,
        "observation",
        "input",
        K.YES,
        K.YES,
        K.YES,
        (),
        tuple(declared),
        context,
        platform="macos",
        native_role="AXButton",
        actions_complete=True,
    )
    return replace(state, **changes)


class PolicyTests(unittest.TestCase):
    def test_shared_background_replacement_uses_ready_addressed_value_without_key_preparation(self):
        intent = Intent("replace", {"text": "value"})
        for context in (InputContext(), replace(READY, window_route=K.NO)):
            for policy in (HostPolicy(), HostPolicy(foreground_assist=True)):
                with self.subTest(context=context, policy=policy):
                    command = choose(facts(VALUE, KEY, context=context), intent, policy, Attempts())
                    self.assertEqual(command.primitive, VALUE)
                    self.assertFalse(command.prepare or command.foreground)
        independent = replace(OBJECT, id="z-independent")
        value = replace(VALUE, id="a-property")
        command = choose(facts(value, KEY, independent), intent, HostPolicy(), Attempts())
        self.assertEqual(command.primitive, independent)
        ready = choose(facts(VALUE, KEY, context=READY), intent, HostPolicy(), Attempts())
        self.assertEqual(ready.primitive, KEY)
        self.assertFalse(ready.prepare or ready.foreground)

    def test_background_property_priority_does_not_apply_to_other_platform_contracts(self):
        for platform in ("windows", "linux"):
            command = choose(
                facts(VALUE, KEY, platform=platform), Intent("replace", {"text": "value"}),
                HostPolicy(), Attempts()
            )
            self.assertEqual(command.primitive.unit, "key_sequence")
            self.assertTrue(command.prepare)

    def test_foreground_property_replacement_preserves_normal_editing(self):
        intent = Intent("replace", {"text": "value"})
        foreground = InputContext(foreground_surface=TARGET.surface)
        state = facts(VALUE, KEY, context=foreground)
        self.assertFalse(has_ready_route(state, intent))
        self.assertFalse(has_independent_text_route(state, intent))
        first = choose(state, intent, HostPolicy(foreground_assist=True), Attempts())
        self.assertEqual(first.primitive.unit, "key_sequence")
        self.assertTrue(first.prepare)
        self.assertFalse(first.foreground)
        ready = choose(
            facts(VALUE, KEY, context=replace(READY, foreground_surface=TARGET.surface)),
            intent, HostPolicy(), Attempts()
        )
        self.assertEqual(ready.primitive, KEY)
        self.assertFalse(ready.prepare)
        # A real independent object-edit contract keeps its original priority.
        self.assertEqual(choose(facts(VALUE, OBJECT, KEY), intent, HostPolicy(), Attempts()).primitive, OBJECT)

    def test_exclusive_property_replacement_waits_for_background_preparation(self):
        intent = Intent("replace", {"text": "value"})
        state = facts(
            VALUE, KEY, replace(POINTER, unit="prepare_receiver"), context=InputContext(geometry_current=K.YES)
        )
        attempts = Attempts()
        for unit in ("key_sequence", "prepare_receiver"):
            command = choose(state, intent, HostPolicy(mode="exclusive", foreground_assist=True), attempts)
            self.assertEqual(command.primitive.unit, unit)
            self.assertTrue(command.prepare)
            self.assertFalse(command.foreground)
            attempts.background_unavailable.add(route_key(command))
            attempts.prepared.add((route_key(command), False))
        last = choose(state, intent, HostPolicy(mode="exclusive", foreground_assist=True), attempts)
        self.assertEqual(last.primitive, VALUE)
        self.assertFalse(last.prepare or last.foreground)

    def test_property_replacement_cannot_change_insert_paste_or_key_intent(self):
        for kind in ("insert", "paste", "key"):
            with self.subTest(kind=kind), self.assertRaises(ComputerUseError):
                choose(facts(VALUE), Intent(kind), HostPolicy(), Attempts())
        for changes in ({"enabled": K.NO}, {"blocked_by": ("modal",)}, {"identity": K.NO}):
            with self.subTest(changes=changes), self.assertRaises(ComputerUseError):
                choose(facts(VALUE, **changes), Intent("replace"), HostPolicy(), Attempts())

    def test_discovery_scope_cannot_be_used_as_an_input_target(self):
        observation = facts(OBJECT, binding=Binding("session", None, None, None))
        for policy in (HostPolicy(), HostPolicy(mode="exclusive")):
            with self.subTest(mode=policy.mode), self.assertRaises(ComputerUseError) as error:
                choose(observation, Intent("replace", {"text": "wrong scope"}), policy, Attempts())
            self.assertEqual(error.exception.code, "invalid_request")

    def test_direct_object_edit_does_not_require_unrelated_keyboard_focus(self):
        decision = choose(facts(OBJECT, KEY), Intent("replace", {"text": "value"}), HostPolicy(), Attempts())
        self.assertEqual(decision.primitive, OBJECT)
        self.assertFalse(decision.prepare)
        self.assertFalse(decision.foreground)

    def test_windows_exclusive_selectable_replacement_chooses_editing_before_object_mutation(self):
        state = facts(OBJECT, KEY, platform="windows", native_role="ControlType.Spinner")
        decision = choose(state, Intent("replace", {"text": "2026/10/16"}), HostPolicy(mode="exclusive"), Attempts())
        self.assertEqual(decision.primitive.unit, "key_sequence")
        self.assertTrue(decision.prepare)
        shared = choose(state, Intent("replace", {"text": "2026/10/16"}), HostPolicy(), Attempts())
        self.assertEqual(shared.primitive, OBJECT)
        unsupported = replace(state, primitives=(OBJECT, replace(KEY, supported=K.NO)))
        self.assertEqual(
            choose(
                unsupported, Intent("replace", {"text": "value"}), HostPolicy(mode="exclusive"), Attempts()
            ).primitive,
            OBJECT,
        )
        ordinary = replace(state, native_role="ControlType.Edit")
        self.assertEqual(
            choose(
                ordinary, Intent("replace", {"text": "产品经理岗位线上面试"}), HostPolicy(mode="exclusive"), Attempts()
            ).primitive,
            OBJECT,
        )
        numeric = choose(state, Intent("replace", {"text": "42.50"}), HostPolicy(mode="exclusive"), Attempts())
        self.assertEqual(numeric.primitive, OBJECT)

    def test_ready_text_keyboard_precedes_contextual_setter_but_not_independent_object(self):
        for kind in ("insert", "replace"):
            with self.subTest(kind=kind):
                intent = Intent(kind, {"text": "value"})
                state = facts(EDITOR, KEY, context=READY)
                decision = choose(state, intent, HostPolicy(), Attempts())
                self.assertEqual(decision.primitive, KEY)
                self.assertFalse(decision.prepare or decision.foreground)
                independent = choose(facts(EDITOR, KEY, OBJECT, context=READY), intent, HostPolicy(), Attempts())
                self.assertEqual(independent.primitive, OBJECT)
                self.assertFalse(independent.prepare or independent.foreground)

    def test_windows_exclusive_composite_replacement_prepares_before_a_noop_setter(self):
        state = facts(OBJECT, KEY, platform="windows", native_role="ControlType.ComboBox")
        state = replace(
            state, primitives=(OBJECT, replace(state.primitives[1], pending=("selection",), readiness=K.NO))
        )
        intent = Intent("replace", {"text": "14:00"})
        command = choose(state, intent, HostPolicy(mode="exclusive"), Attempts())
        self.assertEqual(command.primitive.unit, "key_sequence")
        self.assertTrue(command.prepare)
        for policy in (HostPolicy(), HostPolicy(foreground_assist=True)):
            self.assertEqual(choose(state, intent, policy, Attempts()).primitive, OBJECT)
        unsupported = replace(state, primitives=(OBJECT, replace(KEY, supported=K.NO)))
        self.assertEqual(choose(unsupported, intent, HostPolicy(mode="exclusive"), Attempts()).primitive, OBJECT)

    def test_selectable_combo_itself_keeps_the_independent_value_route(self):
        intent = Intent("replace", {"text": "interview"})
        for context in (InputContext(), replace(READY, editor=None, editor_owner=None, focused_node="field")):
            state = facts(OBJECT, KEY, context=context, platform="windows", native_role="ControlType.ComboBox")
            self.assertEqual(choose(state, intent, HostPolicy(mode="exclusive"), Attempts()).primitive, OBJECT)
        resolved = facts(OBJECT, KEY, context=READY, platform="windows", native_role="ControlType.ComboBox")
        self.assertEqual(choose(resolved, intent, HostPolicy(mode="exclusive"), Attempts()).primitive, KEY)

    def test_native_editor_does_not_require_a_keyboard_event_route(self):
        observation = facts(
            EDITOR,
            replace(KEY, background_preparation=K.NO),
            context=replace(READY, window_route=K.NO, keyboard_surface=None),
        )
        for kind in ("insert", "replace"):
            with self.subTest(kind=kind):
                decision = choose(observation, Intent(kind, {"text": "native selection"}), HostPolicy(), Attempts())
                self.assertEqual(decision.primitive.unit, "native_text_edit")
                self.assertEqual(decision.primitive.channel, Channel.EDITOR)
                self.assertFalse(decision.prepare or decision.foreground)

    def test_contextual_setter_waits_for_one_background_keyboard_preparation(self):
        for kind in ("insert", "replace"):
            intent = Intent(kind, {"text": "value"})
            state = facts(EDITOR, KEY, context=replace(READY, window_route=K.NO, keyboard_surface=None))
            first = choose(state, intent, HostPolicy(foreground_assist=True), Attempts())
            self.assertEqual(first.primitive.unit, "key_sequence")
            self.assertTrue(first.prepare)
            self.assertFalse(first.foreground)
            key = route_key(first)
            for attempts in (
                Attempts(background_unavailable={key}),
                Attempts(prepared={(key, False)}),
                Attempts(unavailable={key}),
            ):
                selected = choose(state, intent, HostPolicy(foreground_assist=True), attempts)
                self.assertEqual(selected.primitive.unit, "native_text_edit")
                self.assertFalse(selected.prepare or selected.foreground)

    def test_contextual_preference_does_not_prepare_unknown_or_incomplete_keyboard_contracts(self):
        for background, pending in (
            (K.NO, ("receiver",)),
            (K.UNKNOWN, ("receiver",)),
            (K.YES, ("receiver", "selection")),
            (K.YES, ("foreground",)),
        ):
            keyboard = replace(KEY, readiness=K.NO, pending=pending, background_preparation=background)
            state = replace(facts(EDITOR, context=READY), primitives=(EDITOR, keyboard))
            selected = choose(state, Intent("insert", {"text": "value"}), HostPolicy(), Attempts())
            self.assertEqual(selected.primitive, EDITOR)
            self.assertFalse(selected.prepare)

    def test_ready_text_preference_does_not_change_explicit_paste_or_key_semantics(self):
        paste = replace(KEY, id="clipboard", unit="clipboard_paste")
        state = facts(OBJECT, EDITOR, EDITOR_CONTEXT, paste, KEY, context=READY)
        for intent, expected in ((Intent("paste", {"text": "value"}), paste), (Intent("key", {"key": "Return"}), KEY)):
            with self.subTest(intent=intent.kind):
                decision = choose(state, intent, HostPolicy(), Attempts())
                self.assertEqual(decision.primitive, expected)
                self.assertFalse(decision.prepare or decision.foreground)

    def test_editor_preparation_precedes_unready_keyboard_without_claiming_edit_support(self):
        for kind in ("insert", "replace"):
            decision = choose(facts(EDITOR_CONTEXT, KEY), Intent(kind, {"text": "value"}), HostPolicy(), Attempts())
            self.assertEqual(decision.primitive.unit, "prepare_receiver")
            self.assertEqual(decision.primitive.channel, Channel.EDITOR)
            self.assertTrue(decision.prepare)
            self.assertFalse(decision.foreground)

    def test_editor_preparation_does_not_replace_ready_input_or_paste_semantics(self):
        decision = choose(facts(EDITOR_CONTEXT, OBJECT), Intent("replace", {"text": "value"}), HostPolicy(), Attempts())
        self.assertEqual(decision.primitive.unit, "native_text_edit")
        self.assertFalse(decision.prepare)
        for intent in (Intent("paste", {"text": "value"}), Intent("key", {"key": "Return"})):
            with self.assertRaises(ComputerUseError):
                choose(facts(EDITOR_CONTEXT), intent, HostPolicy(), Attempts())

    def test_unknown_editing_semantics_cannot_use_a_ready_keyboard(self):
        for kind, unit in [("insert", KEY), ("replace", KEY), ("paste", replace(KEY, unit="clipboard_paste"))]:
            with self.subTest(kind=kind), self.assertRaises(ComputerUseError):
                choose(
                    facts(unit, context=READY),
                    Intent(kind, {"text": "value"}),
                    HostPolicy(),
                    Attempts(),
                    needs_editing_evidence=True,
                )

    def test_native_editing_contract_can_supply_missing_editing_evidence(self):
        state = facts(EDITOR, KEY, context=READY)
        for kind in ("insert", "replace"):
            with self.subTest(kind=kind):
                command = choose(
                    state, Intent(kind, {"text": "value"}), HostPolicy(), Attempts(), needs_editing_evidence=True
                )
                self.assertEqual(command.primitive.unit, "native_text_edit")
                self.assertFalse(command.prepare)

    def test_unknown_paste_target_can_only_prepare_and_cannot_switch_to_text_edit(self):
        state = facts(EDITOR_CONTEXT, EDITOR, replace(KEY, unit="clipboard_paste"))
        command = choose(
            state, Intent("paste", {"text": "value"}), HostPolicy(), Attempts(), needs_editing_evidence=True
        )
        self.assertTrue(command.prepare)
        self.assertEqual(command.primitive.unit, "prepare_receiver")
        known = choose(state, Intent("paste", {"text": "value"}), HostPolicy(), Attempts())
        self.assertEqual(known.primitive.unit, "clipboard_paste")

    def test_verified_pointer_preparation_precedes_foreground_fallback_for_known_paste(self):
        paste = replace(KEY, unit="clipboard_paste")
        pointer = replace(POINTER, unit="prepare_receiver")
        state = facts(paste, pointer, context=InputContext(geometry_current=K.YES))
        attempts = Attempts()
        first = choose(state, Intent("paste", {"text": "body"}), HostPolicy(foreground_assist=True), attempts)
        self.assertEqual(first.primitive.unit, "clipboard_paste")
        attempts.prepared.add((route_key(first), False))
        attempts.background_unavailable.add(route_key(first))
        second = choose(state, Intent("paste", {"text": "body"}), HostPolicy(foreground_assist=True), attempts)
        self.assertEqual(second.primitive.channel, Channel.POINTER)
        self.assertTrue(second.prepare)
        self.assertFalse(second.foreground)

    def test_shared_editor_identity_is_not_proof_of_current_owner(self):
        observation = facts(EDITOR, context=replace(READY, editor_owner="other"))
        decision = choose(observation, Intent("insert"), HostPolicy(), Attempts())
        self.assertTrue(decision.prepare)
        self.assertFalse(decision.foreground)

    def test_selected_target_does_not_substitute_for_keyboard_receiver(self):
        observation = facts(KEY, context=replace(READY, focused_node="other", focus_ancestors=(), editor_owner="other"))
        self.assertTrue(choose(observation, Intent("insert"), HostPolicy(), Attempts()).prepare)
        good = choose(facts(KEY, context=READY), Intent("insert"), HostPolicy(), Attempts())
        self.assertFalse(good.prepare)
        self.assertEqual(good.target, TARGET)

    def test_same_pid_is_not_used_to_approve_another_window(self):
        wrong_window = replace(READY, keyboard_surface="another-document")
        self.assertTrue(choose(facts(KEY, context=wrong_window), Intent("key"), HostPolicy(), Attempts()).prepare)

    def test_window_visual_route_needs_no_fabricated_ax_focus(self):
        window = replace(TARGET, node=None)
        state = facts(KEY, binding=window, context=InputContext(keyboard_surface="surface", window_route=K.YES))
        self.assertFalse(choose(state, Intent("key"), HostPolicy(), Attempts()).prepare)
        self.assertTrue(choose(facts(KEY, context=state.input), Intent("key"), HostPolicy(), Attempts()).prepare)

    def test_foreground_assistance_waits_for_background_unavailability(self):
        policy = HostPolicy(foreground_assist=True)
        state, attempts = facts(KEY), Attempts()
        first = choose(state, Intent("insert"), policy, attempts)
        self.assertTrue(first.prepare)
        self.assertFalse(first.foreground)
        attempts.prepared.add((route_key(first), False))
        attempts.background_unavailable.add(route_key(first))
        self.assertTrue(choose(state, Intent("insert"), policy, attempts).foreground)
        with self.assertRaises(ComputerUseError):
            choose(state, Intent("insert"), HostPolicy(), attempts)

    def test_attempt_identity_ignores_contract_ids_and_preserves_selected_receiver(self):
        state = facts(KEY, actuator=Actuator("editor"))
        first = choose(state, Intent("insert"), HostPolicy(), Attempts())
        changed = replace(
            state, observed="new-observation", primitives=(replace(state.primitives[0], id="fresh-contract"),)
        )
        second = choose(changed, Intent("insert"), HostPolicy(), Attempts())
        self.assertEqual(route_key(first), route_key(second))
        other = choose(
            replace(changed, actuator=Actuator("replacement-editor")), Intent("insert"), HostPolicy(), Attempts()
        )
        self.assertNotEqual(route_key(first), route_key(other))
        attempts = Attempts(prepared={(route_key(first), False)}, background_unavailable={route_key(first)})
        self.assertTrue(choose(changed, Intent("insert"), HostPolicy(foreground_assist=True), attempts).foreground)
        self.assertFalse(
            choose(
                replace(changed, actuator=Actuator("replacement-editor")),
                Intent("insert"),
                HostPolicy(foreground_assist=True),
                attempts,
            ).foreground
        )

    def test_alternate_usable_background_route_precedes_preparation(self):
        decision = choose(
            facts(EDITOR, KEY, context=replace(READY, editor_owner="other")),
            Intent("insert"),
            HostPolicy(foreground_assist=True),
            Attempts(),
        )
        self.assertEqual(decision.primitive, KEY)
        self.assertFalse(decision.prepare)

    def test_pointer_semantics_for_coordinates_right_click_and_double_click(self):
        observation = facts(INVOKE, POINTER, context=InputContext(geometry_current=K.YES, pointer_route=K.YES))
        for parameters in ({"x": 2, "y": 3}, {"button": "right"}, {"count": 2}):
            command = choose(observation, Intent("click", parameters), HostPolicy(), Attempts())
            self.assertEqual(command.primitive, POINTER)
            self.assertFalse(command.prepare)
        self.assertEqual(choose(observation, Intent("click"), HostPolicy(), Attempts()).primitive, INVOKE)

    def test_stale_pixels_are_not_repaired_by_refreshing_input_coordinates(self):
        with self.assertRaises(ComputerUseError):
            choose(facts(POINTER), Intent("click", {"x": 1, "y": 2}), HostPolicy(), Attempts())

    def test_pointer_delivery_is_independent_of_keyboard_receiver(self):
        image = replace(TARGET, node=None, capture="original-image")
        context = InputContext(
            geometry_current=K.YES, pointer_route=K.YES, keyboard_surface="other-window", focused_node="other-editor"
        )
        state = facts(POINTER, binding=image, enabled=K.UNKNOWN, context=context)
        request = Intent("click", dict(x=1, y=2))
        command = choose(state, request, HostPolicy(), Attempts())
        self.assertFalse(command.prepare)
        self.assertEqual(command.target, image)
        for route in (K.NO, K.UNKNOWN):
            unprepared = facts(POINTER, binding=image, enabled=K.UNKNOWN, context=replace(context, pointer_route=route))
            first = choose(unprepared, request, HostPolicy(), Attempts())
            self.assertTrue(first.prepare)
            self.assertFalse(first.foreground)
        with self.assertRaises(ComputerUseError):
            choose(
                facts(POINTER, binding=image, context=replace(context, geometry_current=K.NO)),
                request,
                HostPolicy(foreground_assist=True),
                Attempts(),
            )
        with self.assertRaises(ComputerUseError) as caught:
            choose(replace(state, blocked_by=("modal",)), request, HostPolicy(), Attempts())
        self.assertEqual(caught.exception.code, "blocked_by_modal")

    def test_exclusive_pointer_preparation_requires_exact_foreground_surface(self):
        pointer = replace(POINTER, readiness=K.NO, pending=("foreground",), background_preparation=K.NO)
        state = facts(pointer, context=InputContext(geometry_current=K.YES, pointer_route=K.NO))
        command = choose(state, Intent("drag"), HostPolicy(mode="exclusive"), Attempts())
        self.assertTrue(command.prepare and command.foreground)
        ready = facts(pointer, context=replace(state.input, foreground_surface=TARGET.surface, pointer_route=K.YES))
        self.assertFalse(choose(ready, Intent("drag"), HostPolicy(mode="exclusive"), Attempts()).prepare)

    def test_modal_identity_scope_and_disabled_fail_before_native_dispatch(self):
        for change, code in (
            (dict(blocked_by=("modal",)), "blocked_by_modal"),
            (dict(identity=K.NO), "stale_target"),
            (dict(within_scope=K.NO), "stale_target"),
            (dict(identity=K.UNKNOWN), "input_unavailable"),
            (dict(enabled=K.NO), "input_unavailable"),
        ):
            with self.subTest(change=change), self.assertRaises(ComputerUseError) as caught:
                choose(facts(OBJECT, **change), Intent("insert"), HostPolicy(), Attempts())
            self.assertEqual(caught.exception.code, code)

    def test_paste_contract_cannot_masquerade_as_typing(self):
        paste = Primitive("paste", "clipboard_paste", Channel.KEYBOARD, K.YES, K.YES)
        with self.assertRaises(ComputerUseError):
            choose(facts(paste, context=READY), Intent("insert", {"text": "hello"}), HostPolicy(), Attempts())

    def test_disabled_readiness_cannot_be_repaired_by_foreground_preparation(self):
        disabled = replace(INVOKE, readiness=K.NO, pending=("enabled", "receiver"), background_preparation=K.YES)
        state = replace(facts(INVOKE), primitives=(disabled,), enabled=K.NO)
        for policy in (HostPolicy(), HostPolicy(foreground_assist=True), HostPolicy(mode="exclusive")):
            with self.subTest(policy=policy), self.assertRaises(ComputerUseError) as caught:
                choose(state, Intent("click"), policy, Attempts())
            self.assertIn("disabled", caught.exception.detail)
            self.assertNotIn("desktop mode", caught.exception.detail)

    def test_unavailable_pointer_reason_does_not_disable_a_usable_object_route(self):
        state = replace(facts(INVOKE), unavailable_reason="selected element is off screen")
        self.assertEqual(choose(state, Intent("click"), HostPolicy(), Attempts()).primitive, INVOKE)
        state = replace(state, primitives=())
        with self.assertRaises(ComputerUseError) as caught:
            choose(state, Intent("click"), HostPolicy(), Attempts())
        self.assertEqual(caught.exception.detail, "selected element is off screen")

    def test_menu_context_readiness_is_not_recomputed_from_diagnostic_enabled(self):
        pending = replace(INVOKE, readiness=K.UNKNOWN, pending=("receiver",), background_preparation=K.YES)
        state = replace(facts(INVOKE), primitives=(pending,), enabled=K.NO, native_role="AXMenuItem")
        decision = choose(state, Intent("click"), HostPolicy(), Attempts())
        self.assertTrue(decision.prepare)
        self.assertFalse(decision.foreground)
        # InputContext remains diagnostic; only the unit declares whether the
        # observed disabled state belongs to the requested command context.
        self.assertEqual(decision.primitive, pending)

    def test_unknown_native_support_is_not_available(self):
        with self.assertRaises(ComputerUseError):
            choose(facts(replace(OBJECT, supported=K.UNKNOWN)), Intent("insert"), HostPolicy(), Attempts())

    def test_action_name_prefers_exact_name_but_ambiguous_descriptions_fail(self):
        one = Primitive(
            "one", "native_action", Channel.OBJECT, K.YES, K.YES, action="delete", action_description="Remove"
        )
        two = replace(one, id="two", action="archive")
        self.assertEqual(
            choose(facts(one, two), Intent("action", {"action": "delete"}), HostPolicy(), Attempts()).primitive, one
        )
        with self.assertRaises(ComputerUseError) as caught:
            choose(facts(one, two), Intent("action", {"action": "Remove"}), HostPolicy(), Attempts())
        self.assertEqual(caught.exception.code, "ambiguous_target")

    def test_human_intents_select_declared_actions_without_changing_the_contract(self):
        press = replace(INVOKE, action="AXPress")
        menu = replace(press, id="menu", action="AXShowMenu")
        unrelated = replace(press, id="first", action="Delete", action_description="Press")
        state = facts(unrelated, menu, press, platform="macos", native_role="AXButton", actions_complete=True)
        for kind, expected in (("click", press), ("openMenu", menu)):
            with self.subTest(kind=kind):
                selected = choose(state, Intent(kind), HostPolicy(), Attempts())
                self.assertEqual(selected.primitive, expected)
                self.assertEqual(selected.primitive.action, expected.action)
                self.assertEqual(selected.intent.kind, kind)
                self.assertFalse(selected.prepare or selected.foreground)
        with self.assertRaises(ComputerUseError):
            choose(facts(unrelated, platform="macos", actions_complete=True), Intent("click"), HostPolicy(), Attempts())

    def test_menu_only_click_requires_dropdown_semantics_and_known_action_absence(self):
        menu = replace(INVOKE, action="AXShowMenu")
        for role in ("AXButton", "AXRow", None, "AXPopUpButton", "AXMenuButton", "AXComboBox"):
            for complete in (False, True):
                state = facts(menu, platform="macos", native_role=role, actions_complete=complete)
                with self.subTest(role=role, complete=complete):
                    self.assertEqual(choose(state, Intent("openMenu"), HostPolicy(), Attempts()).primitive, menu)
                    if complete and role in ("AXPopUpButton", "AXMenuButton", "AXComboBox"):
                        self.assertEqual(choose(state, Intent("click"), HostPolicy(), Attempts()).primitive, menu)
                    else:
                        with self.assertRaises(ComputerUseError):
                            choose(state, Intent("click"), HostPolicy(), Attempts())
        with self.assertRaises(ComputerUseError):
            choose(
                facts(menu, platform="linux", native_role="AXComboBox", actions_complete=True),
                Intent("click"),
                HostPolicy(),
                Attempts(),
            )

    def test_unavailable_primary_never_promotes_a_different_secondary_action(self):
        press = replace(INVOKE, action="AXPress", supported=K.NO)
        menu = replace(press, id="menu", action="AXShowMenu", supported=K.YES)
        state = facts(menu, press, platform="macos", native_role="AXComboBox", actions_complete=True)
        with self.assertRaises(ComputerUseError):
            choose(state, Intent("click"), HostPolicy(), Attempts())
        self.assertEqual(choose(state, Intent("openMenu"), HostPolicy(), Attempts()).primitive, menu)

    def test_semantic_actions_do_not_override_pointer_intents_or_hide_ambiguity(self):
        press = replace(INVOKE, action="AXPress")
        state = facts(
            press, POINTER, platform="macos", context=InputContext(geometry_current=K.YES, pointer_route=K.YES)
        )
        for params in ({"x": 1, "y": 3}, {"button": "right"}, {"button": "middle"}, {"count": 2}):
            self.assertEqual(choose(state, Intent("click", params), HostPolicy(), Attempts()).primitive, POINTER)
        duplicate = facts(press, replace(press, id="duplicate"), platform="macos")
        with self.assertRaises(ComputerUseError) as caught:
            choose(duplicate, Intent("click"), HostPolicy(), Attempts())
        self.assertEqual(caught.exception.code, "ambiguous_target")

    def test_diagnostic_focus_does_not_override_native_unit_readiness(self):
        declared = replace(KEY, readiness=K.NO, pending=("receiver",))
        state = replace(facts(KEY, context=READY), primitives=(declared,))
        first = choose(state, Intent("key", {"key": "super+p"}), HostPolicy(), Attempts())
        self.assertTrue(first.prepare)
        self.assertFalse(first.foreground)
        # Missing AX metadata cannot invent new requirements for a native
        # contract that has independently confirmed its entry conditions.
        state = replace(state, primitives=(KEY,), input=InputContext())
        self.assertFalse(choose(state, Intent("key", {"key": "super+p"}), HostPolicy(), Attempts()).prepare)

    def test_unknown_entry_evidence_requires_preparation_and_is_not_false(self):
        declared = replace(KEY, readiness=K.UNKNOWN, pending=("receiver",))
        state = replace(facts(KEY, context=READY), primitives=(declared,))
        self.assertTrue(choose(state, Intent("key", {"key": "super+p"}), HostPolicy(), Attempts()).prepare)
        with self.assertRaises(ValueError):
            replace(KEY, readiness=K.YES, pending=("receiver",))
        with self.assertRaises(ValueError):
            replace(KEY, readiness=K.UNKNOWN, pending=())


class RecordingPort:
    def __init__(self, observations, results, policy=HostPolicy()):
        self.policy, self.observations, self.results = policy, list(observations), list(results)
        self.commands, self.budgets, self.finished = [], [], []
        self.cleanup = Cleanup.COMPLETE
        self.inspections = 0
        self.calls = CallLifecycle(self)

    async def begin(self, intent, target, timeout_ms):
        self.budgets.append(timeout_ms)
        return OpenedOperation("operation", target)

    async def inspect(self, operation, target, timeout_ms):
        self.inspections += 1
        self.budgets.append(timeout_ms)
        if len(self.observations) > 1:
            return self.observations.pop(0)
        return self.observations[0]

    async def execute(self, operation, step, command, timeout_ms):
        self.commands.append((step, command))
        self.budgets.append(timeout_ms)
        result_value = self.results.pop(0)
        if isinstance(result_value, BaseException):
            raise result_value
        return result_value

    async def finish(self, operation):
        self.finished.append(operation)
        return self.cleanup


def result(dispatch=Dispatch.DISPATCHED, **fields):
    return StepResult(TARGET, dispatch, "Observed local result", **fields)


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_background_value_replace_dispatches_once_without_preparing_keys(self):
        for dispatch in (Dispatch.DISPATCHED, Dispatch.UNKNOWN, Dispatch.PARTIAL):
            port = RecordingPort([facts(VALUE, KEY)], [result(dispatch)])
            operation = Engine(port).execute(Intent("replace", {"text": "value"}), TARGET)
            if dispatch == Dispatch.DISPATCHED:
                self.assertEqual((await operation)["status"], "dispatched")
            else:
                with self.assertRaises(ComputerUseError):
                    await operation
            self.assertEqual(
                [(c.primitive.unit, c.prepare, c.foreground) for _, c in port.commands],
                [("native_value_replace", False, False)],
            )

    async def test_property_replacement_is_single_dispatch_after_unavailable_preparation(self):
        cold = facts(VALUE, KEY, context=InputContext(foreground_surface=TARGET.surface))
        for dispatch in (Dispatch.DISPATCHED, Dispatch.UNKNOWN, Dispatch.PARTIAL):
            port = RecordingPort(
                [cold],
                [
                    result(
                        Dispatch.NOT_DISPATCHED, error_code="input_unavailable", retry_reason="preparationUnavailable"
                    ),
                    result(dispatch, confirmation=Confirmation.UNAVAILABLE),
                ],
                policy=HostPolicy(foreground_assist=True),
            )
            operation = Engine(port).execute(Intent("replace", {"text": "value"}), TARGET)
            if dispatch == Dispatch.DISPATCHED:
                self.assertEqual((await operation)["status"], "dispatched")
            else:
                with self.assertRaises(ComputerUseError):
                    await operation
            self.assertEqual(
                [(c.primitive.unit, c.prepare, c.foreground) for _, c in port.commands],
                [("key_sequence", True, False), ("native_value_replace", False, False)],
            )

    async def test_property_replacement_never_replays_partially_dispatched_keys(self):
        port = RecordingPort(
            [facts(VALUE, KEY, context=replace(READY, foreground_surface=TARGET.surface))],
            [result(Dispatch.PARTIAL)]
        )
        with self.assertRaises(ComputerUseError):
            await Engine(port).execute(Intent("replace", {"text": "value"}), TARGET)
        self.assertEqual([c.primitive.unit for _, c in port.commands], ["key_sequence"])

    async def test_contextual_typing_prepares_keys_before_any_text_and_never_replays(self):
        cold = facts(EDITOR, KEY, context=replace(READY, window_route=K.NO, keyboard_surface=None))
        for dispatch in (Dispatch.DISPATCHED, Dispatch.UNKNOWN, Dispatch.PARTIAL):
            port = RecordingPort(
                [cold, facts(EDITOR, KEY, context=READY)],
                [result(Dispatch.NOT_DISPATCHED, preparation=("background window preparation",)), result(dispatch)],
            )
            operation = Engine(port).execute(Intent("insert", {"text": "value"}), TARGET)
            if dispatch == Dispatch.DISPATCHED:
                self.assertEqual((await operation)["status"], "dispatched")
            else:
                with self.assertRaises(ComputerUseError) as caught:
                    await operation
                self.assertNotEqual(caught.exception.effect, "none")
            self.assertEqual(
                [(c.primitive.unit, c.prepare, c.foreground) for _, c in port.commands],
                [("key_sequence", True, False), ("key_sequence", False, False)],
            )

    async def test_unavailable_background_keyboard_retains_native_edit_without_foreground(self):
        cold = facts(EDITOR, KEY, context=replace(READY, window_route=K.NO, keyboard_surface=None))
        port = RecordingPort(
            [cold],
            [
                result(Dispatch.NOT_DISPATCHED, error_code="input_unavailable", retry_reason="preparationUnavailable"),
                result(),
            ],
            policy=HostPolicy(foreground_assist=True),
        )
        outcome = await Engine(port).execute(Intent("insert", {"text": "native value"}), TARGET)
        self.assertEqual(outcome["status"], "dispatched")
        self.assertEqual(
            [(c.primitive.unit, c.prepare, c.foreground) for _, c in port.commands],
            [("key_sequence", True, False), ("native_text_edit", False, False)],
        )

    async def test_contextual_keyboard_preparation_rechecks_modal_before_any_text(self):
        cold = facts(EDITOR, KEY, context=replace(READY, window_route=K.NO, keyboard_surface=None))
        port = RecordingPort(
            [cold, facts(EDITOR, KEY, context=READY, blocked_by=("new-modal",))],
            [result(Dispatch.NOT_DISPATCHED, preparation=("background window preparation",))],
        )
        with self.assertRaises(ComputerUseError) as caught:
            await Engine(port).execute(Intent("insert", {"text": "not sent"}), TARGET)
        self.assertEqual(caught.exception.code, "blocked_by_modal")
        self.assertEqual(len(port.commands), 1)
        self.assertTrue(port.commands[0][1].prepare)

    async def test_incomplete_native_action_name_does_not_request_foreground_preparation(self):
        expand = replace(INVOKE, action="ExpandCollapse.Expand", action_description="ExpandCollapse.Expand")
        port = RecordingPort(
            [facts(expand, KEY, platform="windows", native_role="ControlType.ComboBox")],
            [],
            HostPolicy(mode="exclusive"),
        )
        with self.assertRaises(ComputerUseError) as caught:
            await Engine(port).execute(Intent("action", {"action": "Expand"}), TARGET)
        self.assertEqual(caught.exception.effect, "none")
        self.assertIn("ExpandCollapse.Expand", caught.exception.detail)
        self.assertNotIn("foreground assistance", caught.exception.detail)
        self.assertEqual(port.commands, [])
        self.assertEqual(port.finished, ["operation"])

    async def test_modal_refusal_guides_reading_without_exposing_native_ids_as_references(self):
        port = RecordingPort([facts(OBJECT, blocked_by=("native-surface:3043",))], [])
        with self.assertRaises(ComputerUseError) as caught:
            await Engine(port).execute(Intent("replace", {"text": "value"}), TARGET)
        error = caught.exception
        self.assertEqual(error.code, "blocked_by_modal")
        self.assertEqual(error.effect, "none")
        self.assertIn("modal", error.detail)
        self.assertNotIn("native-surface:3043", str(error))
        self.assertIn("app.read()", error.next)
        self.assertIn("returned", error.next)
        self.assertEqual(port.commands, [])
        self.assertEqual(port.finished, ["operation"])

    async def test_preparation_effect_keeps_observation_before_recovery(self):
        port = RecordingPort(
            [facts(KEY), facts(KEY, blocked_by=("native-surface:3043",))],
            [result(Dispatch.NOT_DISPATCHED, preparation=("focus",))],
        )
        with self.assertRaises(ComputerUseError) as caught:
            await Engine(port).execute(Intent("insert", {"text": "value"}), TARGET)
        self.assertEqual(caught.exception.code, "blocked_by_modal")
        self.assertEqual(caught.exception.effect, "possible")
        self.assertIn("do not replay", caught.exception.next)
        self.assertEqual(len(port.commands), 1)
        self.assertTrue(port.commands[0][1].prepare)

    async def test_uncertain_effect_does_not_preserve_a_no_dispatch_retry_hint(self):
        for effect in ("possible", "partial"):
            refusal = ComputerUseError(
                dict(
                    code="execution_unknown",
                    effect=effect,
                    target="field",
                    detail="The native outcome is uncertain.",
                    next="correct the parameters and try again",
                )
            )
            port = RecordingPort([facts(OBJECT)], [refusal])
            with self.subTest(effect=effect), self.assertRaises(ComputerUseError) as caught:
                await Engine(port).execute(Intent("replace", {"text": "value"}), TARGET)
            self.assertEqual(caught.exception.effect, effect)
            self.assertIn("do not replay", caught.exception.next)
            self.assertNotIn("try again", caught.exception.next)
            self.assertEqual(len(port.commands), 1)

    async def test_ready_keyboard_text_is_not_replayed_through_contextual_setter(self):
        for kind in ("insert", "replace"):
            for dispatch in (Dispatch.DISPATCHED, Dispatch.UNKNOWN, Dispatch.PARTIAL):
                with self.subTest(kind=kind, dispatch=dispatch):
                    port = RecordingPort(
                        [facts(EDITOR, KEY, context=READY)], [result(dispatch, confirmation=Confirmation.UNAVAILABLE)]
                    )
                    operation = Engine(port).execute(Intent(kind, {"text": "value"}), TARGET)
                    if dispatch == Dispatch.DISPATCHED:
                        outcome = await operation
                        self.assertEqual(outcome["status"], "dispatched")
                    else:
                        with self.assertRaises(ComputerUseError) as caught:
                            await operation
                        self.assertNotEqual(caught.exception.effect, "none")
                    self.assertEqual(len(port.commands), 1)
                    self.assertEqual(port.commands[0][1].primitive, KEY)
                    self.assertFalse(port.commands[0][1].prepare or port.commands[0][1].foreground)
                    self.assertEqual(port.finished, ["operation"])

    async def test_cold_editor_is_prepared_then_reinspected_before_native_edit(self):
        for kind in ("insert", "replace"):
            port = RecordingPort(
                [
                    facts(EDITOR_CONTEXT, KEY),
                    facts(EDITOR, context=replace(READY, window_route=K.NO, keyboard_surface=None)),
                ],
                [
                    result(Dispatch.NOT_DISPATCHED, preparation=("AX focus",), confirmation=Confirmation.CONFIRMED),
                    result(),
                ],
            )
            outcome = await Engine(port).execute(Intent(kind, {"text": "value"}), TARGET)
            self.assertEqual(outcome["status"], "dispatched")
            self.assertEqual(
                [(c.primitive.unit, c.prepare) for _, c in port.commands],
                [("prepare_receiver", True), ("native_text_edit", False)],
            )
            self.assertEqual(port.inspections, 2)

    async def test_editor_preparation_does_not_invent_a_missing_editing_route(self):
        port = RecordingPort(
            [facts(EDITOR_CONTEXT), facts()],
            [result(Dispatch.NOT_DISPATCHED, preparation=("AX focus",), confirmation=Confirmation.CONFIRMED)],
        )
        with self.assertRaises(ComputerUseError) as caught:
            await Engine(port).execute(Intent("insert", {"text": "not sent"}), TARGET)
        self.assertEqual(caught.exception.code, "input_unavailable")
        self.assertEqual(len(port.commands), 1)
        self.assertTrue(port.commands[0][1].prepare)

    async def test_failed_editor_preparation_uses_background_keyboard_before_foreground(self):
        port = RecordingPort(
            [facts(EDITOR_CONTEXT, KEY), facts(EDITOR_CONTEXT, KEY), facts(KEY, context=READY)],
            [
                result(Dispatch.NOT_DISPATCHED, error_code="input_unavailable", retry_reason="preparationUnavailable"),
                result(Dispatch.NOT_DISPATCHED, preparation=("keyboard context",), confirmation=Confirmation.CONFIRMED),
                result(),
            ],
            policy=HostPolicy(foreground_assist=True),
        )
        await Engine(port).execute(Intent("insert", {"text": "value"}), TARGET)
        self.assertEqual(
            [(c.primitive.unit, c.prepare, c.foreground) for _, c in port.commands],
            [("prepare_receiver", True, False), ("key_sequence", True, False), ("key_sequence", False, False)],
        )

    async def test_failed_foreground_preparation_can_try_another_preparation_without_replaying_input(self):
        pointer = replace(POINTER, unit="prepare_receiver")
        cold = facts(EDITOR_CONTEXT, pointer, context=InputContext(geometry_current=K.YES))
        unavailable = result(
            Dispatch.NOT_DISPATCHED,
            error_code="input_unavailable",
            retry_reason="preparationUnavailable",
            preparation=("context attempt",),
        )
        port = RecordingPort(
            [cold, cold, cold, cold, facts(OBJECT)],
            [
                unavailable,
                unavailable,
                unavailable,
                result(Dispatch.NOT_DISPATCHED, confirmation=Confirmation.CONFIRMED),
                result(),
            ],
            policy=HostPolicy(foreground_assist=True),
        )
        await Engine(port).execute(Intent("insert", {"text": "value"}), TARGET)
        self.assertEqual(
            [(c.primitive.channel, c.prepare, c.foreground) for _, c in port.commands],
            [
                (Channel.EDITOR, True, False),
                (Channel.POINTER, True, False),
                (Channel.EDITOR, True, True),
                (Channel.POINTER, True, True),
                (Channel.OBJECT, False, False),
            ],
        )
        self.assertEqual(sum(not c.prepare for _, c in port.commands), 1)

    async def test_new_preparation_id_does_not_allow_endless_refocusing(self):
        port = RecordingPort(
            [facts(EDITOR_CONTEXT), facts(replace(EDITOR_CONTEXT, id="new-id"))],
            [result(Dispatch.NOT_DISPATCHED, preparation=("AX focus",), confirmation=Confirmation.CONFIRMED)],
        )
        with self.assertRaises(ComputerUseError):
            await Engine(port).execute(Intent("insert", {"text": "not sent"}), TARGET)
        self.assertEqual(len(port.commands), 1)

    async def test_menu_preparation_rechecks_enabled_and_keeps_the_original_document(self):
        waiting = replace(INVOKE, readiness=K.UNKNOWN, pending=("receiver",), background_preparation=K.YES)
        before = replace(facts(INVOKE), primitives=(waiting,), enabled=K.NO, native_role="AXMenuItem")
        for enabled in (True, False):
            after = facts(INVOKE, enabled=K.YES if enabled else K.NO)
            steps = [
                result(
                    Dispatch.NOT_DISPATCHED, preparation=("menu window context",), confirmation=Confirmation.CONFIRMED
                )
            ]
            if enabled:
                steps.append(result())
            port = RecordingPort([before, after], steps, policy=HostPolicy(foreground_assist=True))
            if enabled:
                self.assertEqual((await Engine(port).execute(Intent("click"), TARGET))["status"], "dispatched")
            else:
                with self.assertRaises(ComputerUseError) as error:
                    await Engine(port).execute(Intent("click"), TARGET)
                self.assertEqual(error.exception.effect, "possible", "Preparation must not disappear from the error")
            self.assertEqual(len(port.commands), 2 if enabled else 1)
            self.assertTrue(port.commands[0][1].prepare)
            self.assertTrue(all(command.target == TARGET and not command.foreground for _, command in port.commands))
            self.assertEqual(port.finished, ["operation"])

    async def test_begin_can_fill_only_an_initially_unknown_root(self):
        class CanonicalPort(RecordingPort):
            async def begin(self, intent, target, timeout_ms):
                return OpenedOperation("operation", TARGET)

        port = CanonicalPort([facts(INVOKE)], [result()])
        await Engine(port).execute(Intent("click"), replace(TARGET, root_window=None))
        self.assertEqual(port.commands[0][1].target, TARGET)
        for field in ("session", "instance", "root_window", "surface", "node", "capture"):
            with self.subTest(field=field):
                port = CanonicalPort([facts(INVOKE)], [])
                engine = Engine(port)
                with self.assertRaisesRegex(ComputerUseError, "changed an already bound target"):
                    await engine.execute(Intent("click"), replace(TARGET, **{field: "different"}))
                self.assertEqual(port.commands, [])
                self.assertEqual(port.finished, ["operation"])

    async def test_inspection_cannot_replace_capture_under_coordinates(self):
        original = replace(TARGET, node=None, capture="pixels-one")
        state = facts(
            POINTER, binding=replace(original, capture="pixels-two"), context=InputContext(geometry_current=K.YES)
        )
        port = RecordingPort([state], [])
        with self.assertRaises(ComputerUseError) as caught:
            await Engine(port).execute(Intent("click", dict(x=4, y=8)), original)
        self.assertEqual(caught.exception.effect, "none")
        self.assertEqual(port.commands, [])
        self.assertEqual(port.finished, ["operation"])

    async def test_focus_completes_on_confirmed_preparation_without_requested_input(self):
        focus = Primitive("focus", "prepare_receiver", Channel.EDITOR, K.YES, K.YES, background_preparation=K.YES)
        port = RecordingPort(
            [facts(focus)],
            [result(Dispatch.NOT_DISPATCHED, preparation=("focus",), confirmation=Confirmation.CONFIRMED)],
        )
        self.assertEqual((await Engine(port).execute(Intent("focus"), TARGET))["status"], "verified")
        self.assertEqual(len(port.commands), 1)
        self.assertTrue(port.commands[0][1].prepare)

    async def test_unconfirmed_focus_has_no_infinite_retry(self):
        focus = Primitive("focus", "prepare_receiver", Channel.KEYBOARD, K.YES, K.YES, background_preparation=K.YES)
        port = RecordingPort([facts(focus)], [result(Dispatch.NOT_DISPATCHED, preparation=("focus",))])
        with self.assertRaises(ComputerUseError):
            await Engine(port).execute(Intent("focus"), TARGET)
        self.assertEqual(len(port.commands), 1)

    async def test_empty_insertion_is_noop_but_empty_replacement_is_real_edit(self):
        for kind in ("insert", "paste"):
            port = RecordingPort([facts()], [])
            self.assertEqual((await Engine(port).execute(Intent(kind, {"text": ""}), TARGET))["status"], "verified")
            self.assertEqual(port.commands, [])
            self.assertEqual(port.finished, ["operation"])
        port = RecordingPort([facts(OBJECT)], [result()])
        await Engine(port).execute(Intent("replace", {"text": ""}), TARGET)
        self.assertEqual(len(port.commands), 1)

    async def test_lost_begin_reply_prevents_a_second_operation(self):
        port = RecordingPort([facts(OBJECT)], [])
        count = 0

        async def lost_begin(*args):
            nonlocal count
            count += 1
            raise OSError("lost begin reply")

        port.begin = lost_begin
        engine = Engine(port)
        with self.assertRaises(ComputerUseError):
            await engine.execute(Intent("insert"), TARGET)
        with self.assertRaisesRegex(ComputerUseError, "retired"):
            await engine.execute(Intent("insert"), TARGET)
        self.assertEqual(count, 1)

    async def test_one_intent_prepares_reobserves_and_dispatches_once(self):
        port = RecordingPort(
            [facts(KEY), facts(KEY, context=READY)],
            [result(Dispatch.NOT_DISPATCHED, preparation=("focus",)), result(confirmation=Confirmation.CONFIRMED)],
        )
        answer = await Engine(port).execute(Intent("insert", {"text": "a"}), TARGET)
        self.assertEqual(answer["status"], "verified")
        self.assertEqual([command.prepare for _, command in port.commands], [True, False])
        self.assertEqual(port.finished, ["operation"])
        self.assertEqual(port.inspections, 2)
        self.assertTrue(all(a >= b for a, b in zip(port.budgets, port.budgets[1:])))

    async def test_only_explicit_no_dispatch_evidence_permits_an_alternate(self):
        port = RecordingPort(
            [facts(OBJECT, KEY, context=READY)],
            [
                result(Dispatch.NOT_DISPATCHED, error_code="input_unavailable", retry_reason="routeUnavailable"),
                result(),
            ],
        )
        await Engine(port).execute(Intent("insert"), TARGET)
        self.assertEqual([command.primitive.id for _, command in port.commands], [OBJECT.id, KEY.id])

    async def test_exhausted_routes_keep_the_specific_native_failure(self):
        failure = result(Dispatch.NOT_DISPATCHED, error_code="input_unavailable", retry_reason="routeUnavailable")
        failure = replace(failure, detail="Clipboard data could not be preserved; no paste sent.")
        port = RecordingPort([facts(OBJECT, context=READY)], [failure])
        with self.assertRaises(ComputerUseError) as caught:
            await Engine(port).execute(Intent("insert"), TARGET)
        self.assertIn("Clipboard data could not be preserved", caught.exception.detail)
        self.assertEqual(caught.exception.effect, "none")
        self.assertEqual(len(port.commands), 1)

    async def test_partial_unknown_and_failed_dispatched_input_never_replay(self):
        for outcome in (result(Dispatch.PARTIAL), result(Dispatch.UNKNOWN), result(error_code="execution_failed")):
            port = RecordingPort([facts(OBJECT, KEY, context=READY)], [outcome])
            with self.subTest(outcome=outcome), self.assertRaises(ComputerUseError) as caught:
                await Engine(port).execute(Intent("insert"), TARGET)
            self.assertNotEqual(caught.exception.effect, "none")
            self.assertEqual(len(port.commands), 1)
            self.assertEqual(port.finished, ["operation"])

    async def test_autocomplete_is_evidence_not_an_excuse_for_replay(self):
        port = RecordingPort([facts(OBJECT, KEY, context=READY)], [result(confirmation=Confirmation.CONTRADICTED)])
        self.assertEqual((await Engine(port).execute(Intent("replace"), TARGET))["status"], "dispatched")
        self.assertEqual(len(port.commands), 1)

    async def test_preparation_effect_survives_later_disabled_failure(self):
        port = RecordingPort(
            [facts(KEY), facts(KEY, enabled=K.NO)], [result(Dispatch.NOT_DISPATCHED, preparation=("focus",))]
        )
        with self.assertRaises(ComputerUseError) as caught:
            await Engine(port).execute(Intent("insert"), TARGET)
        self.assertEqual(caught.exception.effect, "possible")
        self.assertEqual(len(port.commands), 1)

    async def test_exhausted_preparation_retains_native_rejection_and_effect(self):
        primitive = replace(KEY, readiness=K.NO, background_preparation=K.NO, pending=("foreground",))
        state = facts(primitive)
        port = RecordingPort(
            [state, state],
            [
                replace(
                    result(
                        Dispatch.NOT_DISPATCHED,
                        error_code="input_unavailable",
                        retry_reason="preparationUnavailable",
                        preparation=("foreground",),
                    ),
                    detail="captured point is not reachable",
                )
            ],
        )
        port.policy = HostPolicy(mode="exclusive")
        with self.assertRaises(ComputerUseError) as caught:
            await Engine(port).execute(Intent("insert"), TARGET)
        self.assertEqual(caught.exception.detail, "captured point is not reachable")
        self.assertEqual(caught.exception.effect, "possible")
        self.assertEqual(len(port.commands), 1)

    async def test_foreground_fallback_requires_no_requested_input_and_native_evidence(self):
        port = RecordingPort(
            [facts(KEY), facts(KEY), facts(KEY, context=READY)],
            [
                result(Dispatch.NOT_DISPATCHED, error_code="input_unavailable", retry_reason="preparationUnavailable"),
                result(Dispatch.NOT_DISPATCHED, preparation=("foreground", "focus")),
                result(),
            ],
            HostPolicy(foreground_assist=True),
        )
        await Engine(port).execute(Intent("insert"), TARGET)
        self.assertEqual(
            [(c.prepare, c.foreground) for _, c in port.commands], [(True, False), (True, True), (False, False)]
        )

    async def test_preparation_cannot_loop_forever_on_false_focus_reports(self):
        port = RecordingPort([facts(KEY)], [result(Dispatch.NOT_DISPATCHED, preparation=("focus",))])
        with self.assertRaises(ComputerUseError):
            await Engine(port).execute(Intent("insert"), TARGET)
        self.assertEqual(len(port.commands), 1)

    async def test_new_contract_id_cannot_repeat_a_preparation(self):
        port = RecordingPort(
            [facts(KEY), facts(replace(KEY, id="fresh-contract"), observed="fresh-observation")],
            [result(Dispatch.NOT_DISPATCHED, preparation=("focus",))],
        )
        with self.assertRaises(ComputerUseError) as caught:
            await Engine(port).execute(Intent("insert"), TARGET)
        self.assertEqual(caught.exception.effect, "possible")
        self.assertEqual(len(port.commands), 1)

    async def test_unavailable_route_is_not_restored_by_reissuing_its_contract(self):
        port = RecordingPort(
            [facts(OBJECT, KEY, context=READY), facts(replace(OBJECT, id="fresh-object"), KEY, context=READY)],
            [
                result(Dispatch.NOT_DISPATCHED, error_code="input_unavailable", retry_reason="routeUnavailable"),
                result(),
            ],
        )
        await Engine(port).execute(Intent("insert"), TARGET)
        self.assertEqual([command.primitive.unit for _, command in port.commands], ["native_text_edit", "key_sequence"])

    async def test_precondition_replan_allowance_is_shared_across_contracts(self):
        port = RecordingPort(
            [facts(KEY, context=READY), facts(replace(KEY, id="new-key"), context=READY)],
            [result(Dispatch.NOT_DISPATCHED, error_code="input_unavailable", retry_reason="preconditionChanged")] * 2,
        )
        with self.assertRaises(ComputerUseError) as caught:
            await Engine(port).execute(Intent("key", dict(key="Return")), TARGET)
        self.assertEqual(caught.exception.effect, "none")
        self.assertEqual(len(port.commands), 2)

    async def test_recreated_editor_can_be_prepared_but_unbounded_editor_churn_stops(self):
        port = RecordingPort(
            [facts(KEY, actuator=Actuator(node), observed=node) for node in ("a", "b", "c")],
            [result(Dispatch.NOT_DISPATCHED, preparation=("focus",))] * 2,
        )
        with self.assertRaisesRegex(ComputerUseError, "receiver kept changing"):
            await Engine(port).execute(Intent("insert"), TARGET)
        self.assertEqual([command.actuator.node for _, command in port.commands], ["a", "b"])
        self.assertTrue(all(command.prepare for _, command in port.commands))

    async def test_explicit_admission_refusal_does_not_claim_dispatch_or_retire_engine(self):
        refusal = ComputerUseError(
            dict(
                code="invalid_request",
                effect="none",
                target="field",
                detail="The command was rejected before dispatch.",
                next="correct the parameters",
            )
        )
        port = RecordingPort([facts(OBJECT)], [refusal, result()])
        engine = Engine(port)
        with self.assertRaises(ComputerUseError) as caught:
            await engine.execute(Intent("replace", dict(text="hello")), TARGET)
        self.assertEqual(caught.exception.effect, "none")
        self.assertEqual(caught.exception.next, "correct the parameters")
        self.assertEqual(len(port.commands), 1)
        self.assertEqual(port.finished, ["operation"])
        self.assertEqual(
            (await engine.execute(Intent("replace", dict(text="corrected")), TARGET))["status"], "dispatched"
        )

    async def test_native_target_change_stops_even_if_an_alternate_exists(self):
        port = RecordingPort(
            [facts(OBJECT, KEY, context=READY)], [result(Dispatch.NOT_DISPATCHED, error_code="stale_target")]
        )
        with self.assertRaises(ComputerUseError) as caught:
            await Engine(port).execute(Intent("insert"), TARGET)
        self.assertEqual(caught.exception.code, "stale_target")
        self.assertEqual(len(port.commands), 1)

    async def test_transport_failure_is_unknown_and_retires_engine(self):
        transport_error = OSError("connection lost")
        transport_error.add_note("native reply was lost after entering dispatch")
        port = RecordingPort([facts(OBJECT, KEY, context=READY)], [transport_error])
        engine = Engine(port)
        with self.assertRaises(ComputerUseError) as caught:
            await engine.execute(Intent("insert"), TARGET)
        self.assertEqual(caught.exception.effect, "possible")
        self.assertIs(caught.exception.__cause__, transport_error)
        self.assertIn("native reply was lost after entering dispatch", caught.exception.__notes__)
        with self.assertRaisesRegex(ComputerUseError, "retired"):
            await engine.execute(Intent("insert"), TARGET)
        with self.assertRaisesRegex(ComputerUseError, "retired"):
            await Engine(port).execute(Intent("insert"), TARGET)
        self.assertTrue(port.calls.retired)
        self.assertEqual(len(port.commands), 1)

    async def test_cleanup_failure_does_not_erase_dispatched_effect(self):
        port = RecordingPort([facts(OBJECT)], [result()])
        port.cleanup = Cleanup.FAILED
        with self.assertRaises(ComputerUseError) as caught:
            await Engine(port).execute(Intent("insert"), TARGET)
        self.assertEqual(caught.exception.effect, "possible")
        self.assertEqual(caught.exception.code, "execution_unknown")

    async def test_reported_step_cannot_authorize_replaying_unknown_effect(self):
        with self.assertRaises(ValueError):
            result(Dispatch.UNKNOWN, retry_reason="routeUnavailable")

    async def test_cancelled_unit_still_finishes_and_does_not_replay(self):
        cancellation = asyncio.CancelledError()
        cancellation.add_note("caller cancelled")
        port = RecordingPort([facts(OBJECT)], [cancellation])
        with self.assertRaises(asyncio.CancelledError) as caught:
            await Engine(port).execute(Intent("insert"), TARGET)
        self.assertIs(caught.exception, cancellation)
        self.assertIsNone(caught.exception.__cause__)
        self.assertEqual(caught.exception.__notes__, ["caller cancelled"])
        self.assertEqual(port.finished, ["operation"])
        self.assertEqual(len(port.commands), 1)


if __name__ == "__main__":
    unittest.main()
