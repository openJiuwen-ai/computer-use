"""XTEST pointer plans and event ownership, independent of application names.

The real X11 sequencer and Request cancellation run against a fake X transport.
NativePointer/SDK routing and actual desktop effects are tested separately.
"""

import sys
import threading
import time
import unittest
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import patch

import backends.linux.test_linux_native as test_linux_native  # noqa: F401 - Platform package path.
from computer_use_linux.contracts.protocol import Failure, Request
from computer_use_linux.system.x11 import X11


class PointerEventsTests(unittest.TestCase):
    def setUp(self):
        self.x = X11.__new__(X11)
        self.x.can_input, self.x.mode, self.x.assist = True, "shared", False
        self.x.keys, self.x.buttons = set(), set()
        self.x.X = NS(
            KeyPress=2,
            KeyRelease=3,
            ButtonPress=4,
            ButtonRelease=5,
            MotionNotify=6,
            Button1Mask=256,
            Button2Mask=512,
            Button3Mask=1024,
            Button4Mask=2048,
            Button5Mask=4096,
            CurrentTime=0,
        )
        self.geometry = NS(width=1920, height=1080)
        self.mask, self.keymap = 0, [0] * 32
        self.x.root = NS(id=1, get_geometry=lambda: self.geometry, query_pointer=lambda: NS(mask=self.mask))
        self.x.d = NS(query_keymap=lambda: self.keymap)
        self.events, self.calls, self.hook = [], [], None
        self.x._event = self.event
        self.x.prepare = lambda *_a, **_k: self.fail("Low-level events must never prepare a window")
        self.ctx = Request(time.monotonic() + 30, threading.Event())
        self.ctx.pause = lambda _seconds=0.02: self.ctx.check()
        self.barriers = 0

    def event(self, kind, detail=0, x=0, y=0):
        event = (kind, detail, x, y)
        self.events.append(event)
        self.calls.append(("event", kind))
        if self.hook:
            self.hook(*event)

    def barrier(self):
        self.barriers += 1
        self.calls.append(("barrier",))

    def validate(self):
        self.calls.append(("validate",))

    def send(self, kind="click", points=((10, 20),), **kwargs):
        return self.x.pointer_sequence(
            kind, points, self.ctx, before_dispatch=self.barrier, validate=self.validate, **kwargs
        )

    def assert_failure(self, code, function):
        with self.assertRaises(Failure) as caught:
            function()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_preflight_resolves_the_entire_plan_without_effects(self):
        plan = self.x.pointer_preflight("drag", ((10.2, 20.4), (250.1, 260.2)))
        self.assertEqual(plan["points"], ((10, 20), (250, 260)))
        self.assertEqual(len(plan["motion"]), 24)
        self.assertEqual(plan["motion"][-1], (250, 260))
        self.assertEqual(self.events, [])
        self.assertEqual(self.ctx.effect, "none")

    def test_invalid_intent_or_point_never_moves_pointer_or_crosses_barrier(self):
        invalid = [
            ("other", [(1, 2)], {}),
            ("click", [], {}),
            ("click", [(1, 2), (3, 4)], {}),
            ("drag", [(1, 2)], {}),
            ("click", [(1,)], {}),
            ("click", [(True, 2)], {}),
            ("click", [(float("nan"), 2)], {}),
            ("click", [(float("inf"), 2)], {}),
            ("click", [(10**500, 2)], {}),
            ("click", [(-1, 2)], {}),
            ("click", [(1920, 2)], {}),
            ("click", [(1919.9, 2)], {}),
            ("click", [(1, 1080)], {}),
            ("drag", [(1, 2), (1920, 3)], {}),
            ("click", [(1, 2)], {"button": "back"}),
            ("click", [(1, 2)], {"count": 0}),
            ("click", [(1, 2)], {"count": 3}),
            ("click", [(1, 2)], {"count": True}),
            ("click", [(1, 2)], {"count": 1.0}),
            ("scroll", [(1, 2)], {}),
            ("scroll", [(1, 2)], {"direction": "bad"}),
            ("scroll", [(1, 2)], {"direction": "up", "pages": 0}),
            ("scroll", [(1, 2)], {"direction": "up", "pages": 101}),
            ("scroll", [(1, 2)], {"direction": "up", "pages": float("nan")}),
            ("scroll", [(1, 2)], {"direction": "up", "pages": float("inf")}),
            ("scroll", [(1, 2)], {"direction": "up", "pages": True}),
            ("click", [(1, 2)], {"direction": "up"}),
        ]
        for kind, points, parameters in invalid:
            with self.subTest(kind=kind, points=points, parameters=parameters):
                self.assert_failure(
                    "invalid_request",
                    lambda *, kind=kind, parameters=parameters, points=points: self.send(kind, points, **parameters),
                )
                self.assertEqual(self.events, [])
                self.assertEqual(self.barriers, 0)
                self.assertEqual(self.ctx.effect, "none")

    def test_drag_endpoints_may_resolve_to_same_pixel(self):
        self.send("drag", [(10.1, 20.1), (10.2, 20.2)])
        self.assertEqual(len(self.events), 27)
        self.assertEqual(set(e[2:] for e in self.events if e[0] == 6), {(10, 20)})
        self.assertEqual([e[0] for e in self.events if e[0] != 6], [4, 5])
        self.assertFalse(self.x.buttons)

    def test_signed_x_protocol_coordinate_limit_is_checked_before_motion(self):
        self.geometry.width = 40000
        self.assert_failure("invalid_request", lambda: self.send(points=[(32768, 10)]))
        self.assertEqual(self.events, [])
        self.send(points=[(32767, 10)])
        self.assertEqual(self.events[0], (6, 0, 32767, 10))

    def test_barrier_is_once_immediately_before_first_motion(self):
        self.send(count=2, button="right")
        self.assertEqual(self.calls[:3], [("validate",), ("barrier",), ("event", 6)])
        self.assertEqual(self.events, [(6, 0, 10, 20), (4, 3, 0, 0), (5, 3, 0, 0), (4, 3, 0, 0), (5, 3, 0, 0)])
        self.assertEqual(self.barriers, 1)
        self.assertFalse(self.x.buttons)

    def test_v5_sequence_does_not_inherit_legacy_shared_pointer_refusal(self):
        self.assert_failure("mode_unsupported", lambda: self.x.pointer("click", [(10, 20)], self.ctx))
        self.assertEqual(self.events, [])
        self.send()
        self.assertEqual(len(self.events), 3)

    def test_legacy_positional_signature_remains_compatible(self):
        self.x.mode = "exclusive"
        self.x.pointer("click", [(10, 20)], self.ctx, "middle", 2, None, 1, self.validate)
        self.assertEqual([e[1] for e in self.events if e[0] == 4], [2, 2])

    def test_missing_xtest_held_keys_or_held_buttons_stop_before_motion(self):
        for condition in ("extension", "key", "button"):
            with self.subTest(condition=condition):
                self.x.can_input = condition != "extension"
                self.keymap[0] = 1 if condition == "key" else 0
                self.mask = self.x.X.Button1Mask if condition == "button" else 0
                self.assert_failure("mode_unsupported" if condition == "extension" else "input_unavailable", self.send)
                self.assertEqual(self.events, [])
                self.assertEqual(self.barriers, 0)

    def test_cancellation_before_first_event_does_not_cross_barrier(self):
        self.ctx.cancelled.set()
        self.assert_failure("cancelled", self.send)
        self.assertEqual(self.events, [])
        self.assertEqual(self.barriers, 0)

    def test_expired_request_before_first_event_does_not_cross_barrier(self):
        self.ctx.deadline = time.monotonic() - 1
        self.assert_failure("deadline_exceeded", self.send)
        self.assertEqual(self.events, [])
        self.assertEqual(self.barriers, 0)

    def test_cancel_during_validation_is_checked_before_barrier(self):
        self.validate = self.ctx.cancelled.set
        self.assert_failure("cancelled", self.send)
        self.assertEqual(self.events, [])
        self.assertEqual(self.barriers, 0)

    def test_hover_changed_target_is_rechecked_before_button_press(self):
        def guard():
            if self.events:
                raise Failure("Hover changed the hit target", "stale_target")

        self.validate = guard
        self.assert_failure("stale_target", self.send)
        self.assertEqual(self.events, [(6, 0, 10, 20)])
        self.assertEqual(self.barriers, 1)

    def test_cancel_after_initial_motion_never_presses_button(self):
        self.hook = lambda *_: self.ctx.cancelled.set()
        self.assert_failure("cancelled", self.send)
        self.assertEqual(self.events, [(6, 0, 10, 20)])
        self.assertFalse(self.x.buttons)

    def test_drag_switches_to_scope_guard_after_button_press(self):
        changed, drag_checks = [], []

        def guard():
            self.assertFalse(changed, "The old hit/layout guard cannot run during drag")

        self.validate = guard
        self.hook = lambda kind, *_: changed.append(True) if kind == 4 else None
        self.send("drag", [(10, 20), (250, 260)], validate_drag=lambda: drag_checks.append(True))
        self.assertEqual(len(drag_checks), 24)
        self.assertEqual(len(self.events), 27)
        self.assertEqual(self.events[-2:], [(6, 0, 250, 260), (5, 1, 0, 0)])
        self.assertEqual(self.barriers, 1)
        self.assertFalse(self.x.buttons)

    def test_drag_scope_failure_stops_motion_and_releases(self):
        def scope():
            raise Failure("Target process changed", "stale_target")

        self.assert_failure("stale_target", lambda: self.send("drag", [(10, 20), (250, 260)], validate_drag=scope))
        self.assertEqual(self.events, [(6, 0, 10, 20), (4, 1, 0, 0), (5, 1, 0, 0)])
        self.assertFalse(self.x.buttons)

    def test_drag_cancellation_after_press_releases_without_another_guard(self):
        self.hook = lambda kind, *_: self.ctx.cancelled.set() if kind == 4 else None

        def scope():
            self.fail("Cancelled request must not query the target for release")

        self.assert_failure("cancelled", lambda: self.send("drag", [(10, 20), (250, 260)], validate_drag=scope))
        self.assertEqual(self.events, [(6, 0, 10, 20), (4, 1, 0, 0), (5, 1, 0, 0)])
        self.assertFalse(self.x.buttons)

    def test_drag_legacy_default_guard_still_runs_during_motion(self):
        self.send("drag", [(10, 20), (250, 260)])
        self.assertEqual(self.calls.count(("validate",)), 26)

    def test_failed_press_is_not_replayed_but_button_is_released(self):
        def fail(kind, *_):
            if kind == 4:
                raise Failure("Press reply lost", "execution_unknown", effect="possible")

        self.hook = fail
        self.assert_failure("execution_unknown", self.send)
        self.assertEqual(self.events, [(6, 0, 10, 20), (4, 1, 0, 0), (5, 1, 0, 0)])
        self.assertFalse(self.x.buttons)

    def test_click_may_close_target_and_release_never_checks_it(self):
        closed = []

        def guard():
            if closed:
                raise Failure("Window closed", "stale_target")

        self.validate = guard
        self.hook = lambda kind, *_: closed.append(True) if kind == 4 else None
        self.send()
        self.assertEqual(self.events[-1][0], 5)
        self.assertFalse(self.x.buttons)

    def test_second_click_requires_a_fresh_guard(self):
        closed = []

        def guard():
            if closed:
                raise Failure("First click closed window", "stale_target")

        self.validate = guard
        self.hook = lambda kind, *_: closed.append(True) if kind == 4 else None
        self.assert_failure("stale_target", lambda: self.send(count=2))
        self.assertEqual([e[0] for e in self.events], [6, 4, 5])
        self.assertFalse(self.x.buttons)

    def test_scroll_event_budget_and_each_wheel_direction(self):
        for direction, number in (("up", 4), ("down", 5), ("left", 6), ("right", 7)):
            with self.subTest(direction=direction):
                self.events.clear()
                self.send("scroll", direction=direction, pages=0.1)
                self.assertEqual(self.events, [(6, 0, 10, 20), (4, number, 0, 0), (5, number, 0, 0)])
        self.events.clear()
        self.send("scroll", direction="down", pages=100)
        self.assertEqual(len(self.events), 1601)
        self.assertFalse(self.x.buttons)

    def test_scroll_failure_between_pulses_never_sends_remaining_pulses(self):
        self.hook = lambda kind, *_: self.ctx.cancelled.set() if kind == 4 else None
        self.assert_failure("cancelled", lambda: self.send("scroll", direction="down", pages=3))
        self.assertEqual([e[0] for e in self.events], [6, 4, 5])
        self.assertFalse(self.x.buttons)

    def test_release_failure_retains_ownership_and_reports_failed_cleanup(self):
        def fail(kind, *_):
            if kind == 5:
                raise Failure("Server release rejected", "execution_unknown", effect="possible")

        self.hook = fail
        self.assert_failure("execution_unknown", self.send)
        self.assertEqual([e[0] for e in self.events], [6, 4, 5, 5])
        self.assertEqual(self.x.buttons, {1})
        self.hook = None
        self.x.release()
        self.assertFalse(self.x.buttons)

    def test_checked_async_xtest_error_is_not_mistaken_for_a_successful_release(self):
        requests, pending = [], []

        def fake_input(**request):
            requests.append(request)
            if request["event_type"] == 5:
                pending.append(request)

        def sync():
            while pending:
                request = pending.pop(0)
                request["onerror"](RuntimeError("asynchronous BadAccess"), request)

        ext = ModuleType("Xlib.ext")
        ext.xtest = NS(extname="XTEST", FakeInput=fake_input)
        self.x.d.display = NS(get_extension_major=lambda _: 132)
        self.x.d.sync = sync
        self.x._event = lambda *args, **kw: X11._event(self.x, *args, **kw)
        with patch.dict(sys.modules, {"Xlib": ModuleType("Xlib"), "Xlib.ext": ext}):
            self.assert_failure("execution_unknown", self.send)
        self.assertEqual([r["event_type"] for r in requests], [6, 4, 5, 5])
        self.assertEqual(self.x.buttons, {1})


if __name__ == "__main__":
    unittest.main()
