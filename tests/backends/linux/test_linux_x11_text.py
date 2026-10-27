"""XKB plan/dispatch tests; native XKB compatibility is checked in the VM."""

import unittest
from dataclasses import replace
from types import SimpleNamespace

from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.system.x11 import X11
from computer_use_linux.system.x11_text import KeyboardState, XkbSnapshot, plan_text


class Keyboard:
    def __init__(self, desktop):
        self.desktop = desktop
        self.state = KeyboardState()
        self.generation = 1
        self.symbols = {(38, False): ord("a"), (38, True): ord("A"), (56, False): ord("b"), (56, True): ord("B")}

    def snapshot(self):
        shift = 1 if 50 in self.desktop.keys else 0
        return XkbSnapshot(
            self.generation,
            replace(
                self.state,
                mods=self.state.mods | shift,
                base_mods=self.state.base_mods | shift,
                lookup_mods=self.state.lookup_mods | shift,
            ),
            dict(self.symbols),
            50,
        )


class Desktop:
    X = SimpleNamespace(KeyPress=2, KeyRelease=3, ButtonRelease=5)
    can_input = True
    text_preflight, type_text, release = X11.text_preflight, X11.type_text, X11.release

    def __init__(self):
        self.keys, self.buttons, self.events = set(), set(), []
        self.text_keyboard = Keyboard(self)
        self.event_hook = lambda kind, code: None

    def require_idle_input(self):
        if self.keys or self.buttons:
            raise Failure("Held input", "input_unavailable")

    def _event(self, kind, code):
        self.events.append((kind, code))
        self.event_hook(kind, code)


class Context:
    def __init__(self):
        self.cancelled, self.sent_count = False, 0

    def check(self):
        if self.cancelled:
            raise Failure("Cancelled", "cancelled")

    def sent(self):
        self.check()
        self.sent_count += 1


class XkbTextTests(unittest.TestCase):
    def setUp(self):
        self.d, self.ctx = Desktop(), Context()

    def test_full_payload_preflight_rejects_before_prefix(self):
        with self.assertRaises(Failure):
            self.d.type_text("a😀", self.ctx)
        self.assertEqual(self.d.events, [])
        self.assertEqual(self.ctx.sent_count, 0)

    def test_controls_are_not_silently_sent_as_submit_or_navigation(self):
        for text in ("a\n", "\t", "\r", "\0", "\x7f", "\ud800"):
            with self.subTest(text=repr(text)), self.assertRaises(Failure):
                self.d.text_preflight(text)

    def test_empty_preflight_still_rejects_latched_or_held_modifiers(self):
        for field in ("base_mods", "latched_mods", "base_group", "latched_group"):
            self.d.text_keyboard.state = KeyboardState(**{field: 1})
            with self.subTest(field=field), self.assertRaises(Failure):
                self.d.text_preflight("")

    def test_caps_lock_uses_live_translated_symbols_not_case_assumption(self):
        self.d.text_keyboard.state = KeyboardState(mods=2, locked_mods=2, lookup_mods=2)
        self.d.text_keyboard.symbols = {(38, False): ord("A"), (38, True): ord("a")}
        plan = self.d.text_preflight("Aa")
        self.assertEqual(plan.keys, ((38, False), (38, True)))
        self.d.type_text("Aa", self.ctx)
        self.assertEqual(self.d.events, [(2, 38), (3, 38), (2, 50), (2, 38), (3, 38), (3, 50)])

    def test_non_latin_unicode_uses_translated_scalar(self):
        self.d.text_keyboard.symbols = {(52, False): ord("я"), (52, True): ord("Я")}
        self.assertEqual(self.d.text_preflight("яЯ").keys, ((52, False), (52, True)))

    def test_plain_mapping_preferred_and_shift_requires_proven_shift_key(self):
        snap = XkbSnapshot(1, KeyboardState(), {(45, True): 97, (90, False): 97}, None)
        self.assertEqual(plan_text("a", snap).keys, ((90, False),))
        with self.assertRaises(Failure):
            plan_text("a", replace(snap, symbols={(45, True): 97}))

    def test_first_dispatch_barrier_precedes_shift_and_characters(self):
        seen = []
        self.d.type_text("Ab", self.ctx, before_dispatch=lambda: seen.append(self.d.events[:]))
        self.assertEqual(seen, [[]])
        self.assertFalse(self.d.keys)

    def test_rejected_dispatch_barrier_sends_nothing(self):
        def reject():
            raise Failure("Cannot record dispatch")

        with self.assertRaises(Failure):
            self.d.type_text("A", self.ctx, before_dispatch=reject)
        self.assertEqual(self.d.events, [])

    def test_character_callback_follows_modifier_guard_before_actual_character(self):
        seen = []
        self.d.type_text("Ab", self.ctx, before_character=lambda: seen.append(self.d.events[:]))
        self.assertEqual(seen[0], [(2, 50)])
        self.assertEqual(seen[1], [(2, 50), (2, 38), (3, 38), (3, 50)])

    def test_target_change_after_shift_never_sends_character(self):
        ready = True

        def event(kind, code):
            nonlocal ready
            if kind == 2:
                ready = False

        def guard():
            if not ready:
                raise Failure("Receiver changed", "stale_target")

        self.d.event_hook = event
        with self.assertRaises(Failure):
            self.d.type_text("A", self.ctx, validate=guard)
        self.assertEqual(self.d.events, [(2, 50), (3, 50)])

    def test_cancel_after_shift_unconditionally_releases(self):
        self.d.event_hook = lambda kind, code: setattr(self.ctx, "cancelled", True) if kind == 2 else None
        with self.assertRaises(Failure):
            self.d.type_text("A", self.ctx)
        self.assertEqual(self.d.events, [(2, 50), (3, 50)])
        self.assertFalse(self.d.keys)

    def test_change_after_first_character_never_sends_remaining_payload(self):
        self.d.event_hook = lambda kind, code: setattr(self.d.text_keyboard, "generation", 2) if kind == 2 else None
        with self.assertRaises(Failure):
            self.d.type_text("ab", self.ctx)
        self.assertEqual(self.d.events, [(2, 38), (3, 38)])

    def test_changes_after_shift_are_rejected_before_character(self):
        def generation():
            self.d.text_keyboard.generation += 1

        def symbols():
            self.d.text_keyboard.symbols[38, True] = ord("Z")

        def group():
            self.d.text_keyboard.state = KeyboardState(group=1, locked_group=1)

        def lock():
            self.d.text_keyboard.state = KeyboardState(mods=2, locked_mods=2, lookup_mods=2)

        for change in (generation, symbols, group, lock):
            self.d = Desktop()
            self.d.event_hook = lambda kind, code, *, change=change: change() if kind == 2 else None
            with self.subTest(change=change.__name__), self.assertRaises(Failure):
                self.d.type_text("A", self.ctx)
            self.assertEqual(self.d.events, [(2, 50), (3, 50)])

    def test_remapping_in_character_callback_is_rechecked(self):
        def change():
            self.d.text_keyboard.generation += 1

        with self.assertRaises(Failure):
            self.d.type_text("a", self.ctx, before_character=change)
        self.assertEqual(self.d.events, [])

    def test_failed_press_reply_still_owns_cleanup_release(self):
        def fail(kind, code):
            if kind == 2:
                raise RuntimeError("Lost reply")

        self.d.event_hook = fail
        with self.assertRaises(RuntimeError):
            self.d.type_text("a", self.ctx)
        self.assertEqual(self.d.events, [(2, 38), (3, 38)])
        self.assertFalse(self.d.keys)

    def test_failed_release_keeps_owned_key_for_session_retirement(self):
        def fail(kind, code):
            if kind == 3:
                raise RuntimeError("Rejected release")

        self.d.event_hook = fail
        with self.assertRaises(Failure) as error:
            self.d.type_text("a", self.ctx)
        self.assertEqual(error.exception.effect, "possible")
        self.assertEqual(self.d.keys, {38})

    def test_v3_signature_remains_callable(self):
        self.d.type_text("a", self.ctx, lambda: None)
        self.assertEqual(self.d.events, [(2, 38), (3, 38)])

    def test_no_keys_sent_when_xtest_unavailable(self):
        self.d.can_input = False
        with self.assertRaises(Failure):
            self.d.type_text("a", self.ctx)
        self.assertEqual(self.d.events, [])


if __name__ == "__main__":
    unittest.main()
