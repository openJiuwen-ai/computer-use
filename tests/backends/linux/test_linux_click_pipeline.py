"""Click v1 contracts through production native passes and XTest sequencing.

Only AT-SPI/X11 native effects are substituted. In particular expose(), native
final guards, single-operation ownership, wire decoding and release paths run.
"""

import asyncio
import io
import json
import os
import unittest
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

from linux_native_provider import NativeError

import backends.linux.test_linux_native as base
import backends.linux.test_linux_native_element_pointer as element_tests
import backends.linux.test_linux_native_pointer as image_tests
from computer_use.protocol.execution import decodeExecutionResult
from computer_use_linux.contracts.protocol import Failure


class ClickHarness:
    def begin_click(self, target, parameters=None, *, version="v1"):
        self.selected = target
        request = dict(mode="write", target=target, intent=dict(kind="click", parameters=parameters or {}))
        if version is not None:
            request["clickContract"] = version
        self.opened = self.driver.perform("begin", request, base.context())
        self.operation = self.opened["operation"]
        return self.inspect_click()

    def inspect_click(self):
        return decodeExecutionResult(
            "inspect",
            self.driver.perform(
                "inspect",
                dict(operation=self.operation, target=self.selected, query=dict(kind="inputContext", within=[])),
                base.context(),
            ),
        )

    def click_command(self, facts, *, unit="pointer_sequence", foreground=False):
        offer = next(p for p in facts["primitives"] if p["unit"] == unit)
        return dict(kind=unit, primitiveId=offer["id"], target=self.selected, foreground=foreground)

    def send_click(self, command, *, step=1, ctx=None):
        return decodeExecutionResult(
            "execute",
            self.driver.perform(
                "execute", dict(operation=self.operation, step=step, command=command), ctx or base.context()
            ),
        )

    def assert_no_request(self, receipt, continuation="stop"):
        self.assertEqual(receipt["dispatch"], "notDispatched")
        self.assertEqual(
            receipt["click"]["transportEvidence"],
            dict(kind="none", callEntered=False, callReturned=False, submitted=None, expected=None),
        )
        self.assertEqual(receipt["click"]["continuation"]["kind"], continuation)
        self.assertEqual(receipt["click"]["continuation"]["target"], self.selected)


class LinuxClickElementTests(ClickHarness, unittest.TestCase):
    setUp = element_tests.LinuxNativeElementPointerTests.setUp
    tearDown = element_tests.LinuxNativeElementPointerTests.tearDown
    node = element_tests.LinuxNativeElementPointerTests.node

    def setup_click(self, *, obj=None, parameters=None, version="v1"):
        return self.begin_click(dict(self.target, node=self.node(obj or self.b.canvas)), parameters, version=version)

    def test_opt_in_echo_and_legacy_contract_are_separate(self):
        self.assertIn("nativeClickPipelineV1", self.driver.capabilities)
        legacy = self.setup_click(version=None)
        self.assertNotIn("clickContract", self.opened)
        self.assertTrue(all("click" not in p for p in legacy["primitives"]))
        old_result = self.send_click(self.click_command(legacy))
        self.assertNotIn("click", old_result)
        self.driver.finish(self.operation)
        current = self.setup_click()
        self.assertEqual(self.opened["clickContract"], "v1")
        self.assertTrue(all("click" in p for p in current["primitives"]))

    def test_invalid_versions_and_nonclick_opt_in_fail_before_resources(self):
        selected = dict(self.target, node=self.node(self.b.canvas))
        for kind, mode, version in [
            ("click", "write", "v2"),
            ("click", "write", None),
            ("read", "read", "v1"),
            ("scroll", "write", "v1"),
        ]:
            with self.subTest(kind=kind, version=version), self.assertRaises(Failure):
                self.driver.perform(
                    "begin",
                    dict(mode=mode, target=selected, clickContract=version, intent=dict(kind=kind, parameters={})),
                    base.context(),
                )
            self.assertIsNone(self.driver.current)
            self.assertEqual(self.b.leases, 0)
        self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(self.b.desktop.activations, [])

    def test_ready_background_xtest_has_fixed_delivery_and_no_keyboard_requirement(self):
        facts = self.setup_click()
        offer = facts["primitives"][0]["click"]
        self.assertEqual(
            offer,
            dict(
                routeFamily="linux.x11.xtest",
                interaction="pointer",
                delivery="desktop_pointer",
                admission="ready",
                reason=None,
                preparationOptions=[],
                preferenceProfile="default",
            ),
        )
        result = self.send_click(self.click_command(facts))
        self.assertEqual(result["dispatch"], "dispatched")
        self.assertEqual(result["click"]["preparationEvidence"], [])
        self.assertEqual(
            result["click"]["transportEvidence"],
            dict(kind="xtest", callEntered=True, callReturned=True, submitted=3, expected=3),
        )
        self.assertEqual(result["click"]["continuation"]["kind"], "observe")
        self.assertEqual((self.b.desktop.foreground, self.b.desktop.keyboard_window), (22, 22))
        self.assertEqual(self.b.desktop.activations, [])
        self.assertEqual(self.b.canvas.text, "1")

    def test_semantic_offer_and_native_action_receipt_do_not_move_pointer(self):
        facts = self.setup_click(obj=self.b.button)
        offer = next(p["click"] for p in facts["primitives"] if p["unit"] == "native_action")
        self.assertEqual(
            (offer["interaction"], offer["delivery"], offer["routeFamily"]),
            ("semantic", "object_action", "linux.atspi.action"),
        )
        result = self.send_click(self.click_command(facts, unit="native_action"))
        self.assertEqual(result["dispatch"], "dispatched")
        self.assertEqual(
            result["click"]["transportEvidence"],
            dict(kind="nativeAction", callEntered=True, callReturned=True, submitted=1, expected=1),
        )
        self.assertEqual(self.b.button.action_history, ["click"])
        self.assertEqual(self.b.desktop.events, [])

    def test_action_false_after_effect_is_unknown_without_pointer_replay(self):
        facts = self.setup_click(obj=self.b.button)
        self.b.ax.hooks["do_action"] = lambda obj, index: obj.action_history.append("effect")
        self.b.button.errors["do_action"] = False
        result = self.send_click(self.click_command(facts, unit="native_action"))
        self.assertEqual(result["dispatch"], "unknown")
        self.assertEqual(
            result["click"]["transportEvidence"],
            dict(kind="nativeAction", callEntered=True, callReturned=True, submitted=None, expected=1),
        )
        self.assertEqual(result["click"]["continuation"]["kind"], "observe")
        self.assertEqual(self.b.button.action_history, ["effect"])
        with self.assertRaises(Failure):
            self.send_click(self.click_command(facts), step=2)
        self.assertEqual(self.b.desktop.events, [])

    def test_action_call_exception_retains_entered_without_return(self):
        facts = self.setup_click(obj=self.b.button)
        self.b.button.errors["do_action"] = NativeError("lost native reply")
        result = self.send_click(self.click_command(facts, unit="native_action"))
        self.assertEqual(result["dispatch"], "unknown")
        self.assertEqual(
            result["click"]["transportEvidence"],
            dict(kind="nativeAction", callEntered=True, callReturned=False, submitted=None, expected=1),
        )
        self.assertEqual(self.b.desktop.events, [])

    def test_disabled_action_does_not_enter_native_call(self):
        facts = self.setup_click(obj=self.b.button)
        self.b.button.states.difference_update(("ENABLED", "SENSITIVE"))
        result = self.send_click(self.click_command(facts, unit="native_action"))
        self.assert_no_request(result)
        self.assertEqual(self.b.button.action_history, [])

    def test_covered_point_without_preparation_can_reselect_before_any_request(self):
        self.b.desktop.covered = True
        facts = self.setup_click()
        self.assertEqual(facts["primitives"][0]["click"]["preparationOptions"], ["foreground"])
        result = self.send_click(self.click_command(facts))
        self.assert_no_request(result, "reselectWithinScope")
        self.assertEqual(result["retryReason"], "preparationUnavailable")
        self.assertEqual(result["click"]["preparationEvidence"], [])
        self.assertEqual(self.b.desktop.events, [])

    def test_exposure_and_dispatch_are_one_execute_without_keyboard_focus(self):
        self.b.desktop.covered = self.b.desktop.assist = True
        facts = self.setup_click()
        result = self.send_click(self.click_command(facts, foreground=True))
        self.assertEqual(result["dispatch"], "dispatched")
        self.assertEqual(self.b.desktop.activations, [11])
        self.assertEqual(self.b.desktop.keyboard_window, 22)
        self.assertEqual(self.b.canvas.text, "1")
        (row,) = result["click"]["preparationEvidence"]
        self.assertEqual(
            (row["kind"], row["dispatch"], row["effect"], row["continuation"], row["wait"]),
            ("activation", "dispatched", "contextOnly", "withinScope", "satisfied"),
        )
        self.assertEqual(row["scope"], self.selected)
        self.assertTrue(row["observations"])

    def test_covered_then_self_exposed_target_uses_no_authorized_preparation(self):
        self.b.desktop.covered = True
        facts = self.setup_click()
        self.b.desktop.covered = False
        result = self.send_click(self.click_command(facts, foreground=True))
        self.assertEqual(result["dispatch"], "dispatched")
        self.assertEqual(result["click"]["preparationEvidence"], [])
        self.assertEqual(self.b.desktop.activations, [])

    def test_host_permission_is_enforced_before_activation(self):
        self.b.desktop.covered = True
        facts = self.setup_click()
        result = self.send_click(self.click_command(facts, foreground=True))
        self.assert_no_request(result)
        self.assertEqual(result["errorCode"], "mode_unsupported")
        self.assertEqual(result["click"]["preparationEvidence"], [])
        self.assertEqual(self.b.desktop.activations, [])

    def test_element_can_move_with_its_window_during_exposure(self):
        self.b.desktop.covered = self.b.desktop.assist = True
        facts = self.setup_click()

        def move():
            self.b.desktop.items[0].rect = (400, 200, 640, 480)
            for obj in (self.b.window, self.b.group, self.b.canvas, self.b.button):
                x, y, w, h = obj.rect
                obj.rect = x + 400, y + 200, w, h

        self.b.desktop.activate_hook = move
        result = self.send_click(self.click_command(facts, foreground=True))
        self.assertEqual(result["dispatch"], "dispatched")
        self.assertEqual(self.b.desktop.events[0], (6, 0, 490, 280))
        self.assertEqual(self.b.canvas.text, "1")

    def test_unknown_activation_effect_cannot_allow_reselection(self):
        self.b.desktop.covered = self.b.desktop.assist = True
        facts = self.setup_click()

        def disappear():
            self.b.desktop.items[0] = replace(self.b.desktop.items[0], process_start="replacement")

        self.b.desktop.activate_hook = disappear
        result = self.send_click(self.click_command(facts, foreground=True))
        self.assert_no_request(result, "observe")
        (row,) = result["click"]["preparationEvidence"]
        self.assertEqual((row["dispatch"], row["effect"], row["continuation"]), ("unknown", "unknown", "observe"))
        self.assertIsNone(result["retryReason"])
        self.assertEqual(self.b.desktop.events, [])

    def test_exposure_timeout_reports_preparation_without_requested_input(self):
        self.b.desktop.covered = self.b.desktop.assist = True
        facts = self.setup_click()
        self.b.desktop.activate_hook = lambda: setattr(self.b.desktop, "covered", True)
        result = self.send_click(self.click_command(facts, foreground=True), ctx=base.context(0.08))
        self.assert_no_request(result, "observe")
        self.assertEqual(result["click"]["preparationEvidence"][0]["wait"], "exhausted")
        self.assertEqual(self.b.desktop.events, [])

    def test_first_motion_is_partial_request_even_without_any_press(self):
        facts = self.setup_click()
        self.b.desktop.event_hook = lambda kind, *_: setattr(self.b.desktop, "covered", True) if kind == 6 else None
        result = self.send_click(self.click_command(facts))
        self.assertEqual(result["dispatch"], "partial")
        self.assertEqual(
            result["click"]["transportEvidence"],
            dict(kind="xtest", callEntered=True, callReturned=True, submitted=1, expected=3),
        )
        self.assertEqual(result["click"]["continuation"]["kind"], "observe")
        self.assertEqual([e[0] for e in self.b.desktop.events], [6])
        with self.assertRaises(Failure):
            self.send_click(self.click_command(facts), step=2)

    def test_lost_press_reply_releases_owned_button_without_replay(self):
        facts = self.setup_click()

        def lose(kind, *_):
            if kind == 4:
                raise NativeError("press reply lost")

        self.b.desktop.event_hook = lose
        result = self.send_click(self.click_command(facts))
        self.assertEqual((result["dispatch"], result["cleanup"]), ("unknown", "complete"))
        self.assertEqual(
            result["click"]["transportEvidence"],
            dict(kind="xtest", callEntered=True, callReturned=False, submitted=1, expected=3),
        )
        self.assertEqual([e[0] for e in self.b.desktop.events], [6, 4, 5])
        self.assertEqual(self.b.desktop.buttons, set())

    def test_cancel_after_press_releases_without_second_double_click(self):
        facts = self.setup_click(parameters={"count": 2})
        ctx = base.context()
        self.b.desktop.event_hook = lambda kind, *_: ctx.cancelled.set() if kind == 4 else None
        result = self.send_click(self.click_command(facts), ctx=ctx)
        self.assertEqual(
            (result["dispatch"], result["cleanup"], result["errorCode"]), ("partial", "complete", "cancelled")
        )
        self.assertEqual(result["click"]["transportEvidence"]["submitted"], 3)
        self.assertEqual(result["click"]["transportEvidence"]["expected"], 5)
        self.assertEqual([e[0] for e in self.b.desktop.events], [6, 4, 5])
        self.assertEqual(self.b.desktop.buttons, set())

    def test_complete_request_does_not_regress_when_postdispatch_check_fails(self):
        facts = self.setup_click()
        ctx = base.context()
        self.b.desktop.event_hook = lambda kind, *_: ctx.cancelled.set() if kind == 5 else None
        result = self.send_click(self.click_command(facts), ctx=ctx)
        self.assertEqual((result["dispatch"], result["errorCode"]), ("dispatched", "cancelled"))
        self.assertEqual(result["click"]["transportEvidence"]["submitted"], 3)
        self.assertEqual(result["click"]["continuation"]["kind"], "observe")

    def test_cleanup_failure_preserves_complete_requested_event_count(self):
        facts = self.setup_click()
        original = self.b.desktop.release

        def fail_cleanup(**kwargs):
            original(**kwargs)
            self.b.desktop.buttons.add(1)
            raise NativeError("cleanup state unavailable")

        with patch.object(self.b.desktop, "release", side_effect=fail_cleanup):
            result = self.send_click(self.click_command(facts))
        self.assertEqual((result["dispatch"], result["cleanup"]), ("dispatched", "failed"))
        self.assertEqual(result["click"]["transportEvidence"]["submitted"], 3)
        self.assertEqual(result["click"]["continuation"]["kind"], "observe")
        self.assertEqual(self.b.canvas.text, "1")
        self.b.desktop.buttons.clear()

    def test_step_retransmission_returns_receipt_without_dispatching_again(self):
        facts = self.setup_click()
        command = self.click_command(facts)
        result = self.send_click(command)
        self.assertEqual(self.send_click(command), result)
        self.driver.finish(self.operation)
        self.assertEqual(self.send_click(command), result)
        self.assertEqual(self.b.canvas.text, "1")
        self.assertEqual(len(self.b.desktop.events), 3)

    def test_prepare_receiver_and_modified_target_are_rejected_without_input(self):
        facts = self.setup_click()
        command = self.click_command(facts)
        for number, change in enumerate(
            (
                {"kind": "prepare_receiver"},
                {"target": dict(self.selected, node=self.node(self.b.button))},
                {"foreground": True},
            ),
            1,
        ):
            with self.subTest(change=change), self.assertRaises(Failure):
                self.send_click(dict(command, **change), step=number)
        self.assertEqual(self.b.desktop.events, [])
        self.assertEqual(self.b.desktop.activations, [])

    def test_pointer_unavailable_does_not_claim_wayland_or_surface_delivery(self):
        self.b.desktop.can_input = False
        facts = self.setup_click(obj=self.b.button)
        self.assertTrue(facts["primitives"])
        self.assertTrue(any(p["click"]["delivery"] == "object_action" for p in facts["primitives"]))
        for primitive in facts["primitives"]:
            if primitive["unit"] == "pointer_sequence":
                self.assertEqual(
                    (primitive["click"]["delivery"], primitive["click"]["admission"]),
                    ("desktop_pointer", "unavailable"),
                )
        self.assertFalse(any(p["click"]["delivery"] == "surface_pointer" for p in facts["primitives"]))
        self.assertEqual(self.b.desktop.events, [])

    def test_diagnostic_switch_and_failed_sink_preserve_queries_input_and_receipt(self):
        class BrokenSink:
            def __init__(self, error):
                self.error = error

            def write(self, value):
                raise self.error("diagnostic sink unavailable")

            def flush(self):
                raise self.error("diagnostic sink unavailable")

        def comparable(receipt):
            value = deepcopy(receipt)
            value["target"] = {}
            value["click"]["continuation"]["target"] = {}
            for row in value["click"]["preparationEvidence"]:
                row["scope"] = {}
            return value

        for scenario in ("pointer", "exposure", "partial", "action", "action-unknown"):
            baseline = None
            for mode in ("off", "on", "broken-sink", "cancelled-sink"):
                with self.subTest(scenario=scenario, mode=mode):
                    self.tearDown()
                    self.setUp()
                    semantic = scenario.startswith("action")
                    if scenario == "exposure":
                        self.b.desktop.covered = self.b.desktop.assist = True
                    if scenario == "partial":
                        self.b.desktop.event_hook = lambda kind, *_: (
                            setattr(self.b.desktop, "covered", True) if kind == 6 else None
                        )
                    if scenario == "action-unknown":
                        self.b.button.errors["do_action"] = False
                    sink = (
                        BrokenSink(OSError)
                        if mode == "broken-sink"
                        else BrokenSink(asyncio.CancelledError)
                        if mode == "cancelled-sink"
                        else io.StringIO()
                    )
                    facts = self.setup_click(obj=self.b.button if semantic else None)
                    self.b.ax.calls.clear()
                    with (
                        patch.dict(os.environ, {"COMPUTER_USE_TRACE_NATIVE_INPUT": "0" if mode == "off" else "1"}),
                        patch("computer_use_linux.control.click.diagnostics.sys.stderr", sink),
                    ):
                        result = self.send_click(
                            self.click_command(
                                facts,
                                unit="native_action" if semantic else "pointer_sequence",
                                foreground=scenario == "exposure",
                            )
                        )
                    observed = (
                        comparable(result),
                        list(self.b.desktop.events),
                        list(self.b.desktop.activations),
                        [(name, args) for name, _, args in self.b.ax.calls],
                    )
                    if baseline is None:
                        baseline = observed
                    else:
                        self.assertEqual(observed, baseline, "Diagnostics must not alter native work or query order.")
                    if mode == "off":
                        self.assertEqual(sink.getvalue(), "")
                    if mode == "on":
                        row = json.loads(sink.getvalue())["message"]
                        self.assertEqual(
                            (row["kind"], row["operation"], row["step"]), ("native_click_timing", self.operation, 1)
                        )
                        self.assertEqual(row["target"], self.selected)
                        self.assertEqual(row["click"], result["click"])
                        self.assertEqual(row["dispatch"], result["dispatch"])
                        self.assertEqual(row["coordinateSource"], "object" if semantic else "atspiComponent")
                        names = [stage["name"] for stage in row["stages"]]
                        self.assertEqual(names[0], "resolveClickPlan")
                        self.assertEqual(names[-1], "buildClickReceipt")
                        self.assertIn("dispatchNativeAction" if semantic else "xtestSequenceAndOwnedRelease", names)
                        self.assertTrue(all(stage["durationMs"] >= 0 for stage in row["stages"]))
                        self.assertGreaterEqual(row["durationMs"], 0)
                        if scenario in ("partial", "action-unknown"):
                            self.assertTrue(any(stage["outcome"] == "error" for stage in row["stages"]))

    def test_diagnostic_clock_failure_and_step_retransmit_do_not_affect_dispatch(self):
        facts = self.setup_click()
        command = self.click_command(facts)
        sink = io.StringIO()
        with (
            patch.dict(os.environ, {"COMPUTER_USE_TRACE_NATIVE_INPUT": "1"}),
            patch("computer_use_linux.control.click.diagnostics.sys.stderr", sink),
            patch(
                "computer_use_linux.control.click.diagnostics.time.perf_counter_ns",
                side_effect=asyncio.CancelledError("clock unavailable"),
            ),
        ):
            result = self.send_click(command)
            self.assertEqual(self.send_click(command), result)
        self.assertEqual(result["dispatch"], "dispatched")
        self.assertEqual(self.b.canvas.text, "1")
        rows = sink.getvalue().splitlines()
        self.assertEqual(len(rows), 1, "Reusing a retained step must not re-enter the native pipeline.")
        diagnostic = json.loads(rows[0])["message"]
        self.assertIsNone(diagnostic["durationMs"])
        self.assertTrue(all(stage["durationMs"] is None for stage in diagnostic["stages"]))


class LinuxClickImageTests(ClickHarness, unittest.TestCase):
    setUp = image_tests.LinuxNativePointerTests.setUp
    tearDown = image_tests.LinuxNativePointerTests.tearDown
    begin = image_tests.LinuxNativePointerTests.begin
    read = image_tests.LinuxNativePointerTests.read
    image = image_tests.LinuxNativePointerTests.image

    def test_no_ax_captured_background_point_uses_xtest_without_focus(self):
        selected = self.image()
        with patch.object(self.b.ax, "call", side_effect=AssertionError("No AX requirement for image input")):
            facts = self.begin_click(selected, dict(x=10, y=20))
            result = self.send_click(self.click_command(facts))
        self.assertEqual(result["dispatch"], "dispatched")
        self.assertEqual(self.b.desktop.events, [(6, 0, 25, 50), (4, 1, 0, 0), (5, 1, 0, 0)])
        self.assertEqual(self.b.desktop.keyboard_window, 22)
        self.assertEqual(self.b.desktop.activations, [])

    def test_capture_geometry_changed_during_preparation_is_not_reprojected(self):
        self.b.desktop.covered = self.b.desktop.assist = True
        facts = self.begin_click(self.image(), dict(x=10, y=20))
        self.b.desktop.activate_hook = lambda: setattr(self.b.desktop.items[0], "rect", (30, 20, 640, 480))
        result = self.send_click(self.click_command(facts, foreground=True))
        self.assert_no_request(result, "observe")
        self.assertEqual(result["errorCode"], "stale_target")
        self.assertEqual(self.b.desktop.events, [])


if __name__ == "__main__":
    unittest.main()
