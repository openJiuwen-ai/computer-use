"""Integrated click regressions exercise selection, progress and actual command boundaries."""

import asyncio
import unittest
from dataclasses import replace

from sdk.interaction.write.test_execution_engine import TARGET, RecordingPort

from computer_use.diagnostics import setDiagnosticHandler
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.interaction.write.routes.policy import Attempts, choose, route_key
from computer_use.native.contracts import (
    Channel,
    ClickContinuation,
    ClickOffer,
    ClickPreparationEvidence,
    ClickReceipt,
    ClickTransportEvidence,
    Dispatch,
    Facts,
    HostPolicy,
    Intent,
    Knowledge as K,
    Primitive,
    StepResult,
)


def pointer(family="pid", admission="ready", options=(), delivery="surface_pointer", **changes):
    p = Primitive(
        family,
        "pointer_sequence",
        Channel.POINTER,
        K.YES,
        K.YES if admission == "ready" else K.NO,
        () if admission == "ready" else ("receiver",),
        K.UNKNOWN,
        click=ClickOffer(family, "pointer", delivery, admission, preparation_options=options),
    )
    return replace(p, **changes)


def semantic():
    return Primitive(
        "press",
        "native_action",
        Channel.OBJECT,
        K.YES,
        K.YES,
        action="AXPress",
        click=ClickOffer("ax", "semantic", "object_action", "ready"),
    )


def row_selection():
    return Primitive(
        "row-selection",
        "native_selection",
        Channel.OBJECT,
        K.YES,
        K.YES,
        click=ClickOffer("macos.axSelection.replaceRows.v1", "semantic", "object_selection", "ready"),
    )


def observation(*offers, **changes):
    return replace(
        Facts(
            TARGET,
            "observation",
            "button",
            K.YES,
            K.YES,
            K.UNKNOWN,
            (),
            offers,
            platform="macos",
            native_role="AXButton",
            actions_complete=True,
        ),
        **changes,
    )


def receipt(family="pid", dispatch=Dispatch.DISPATCHED, retry=None, preparation=(), continuation=None):
    return StepResult(
        TARGET,
        dispatch,
        "request submission evidence",
        error_code="input_unavailable" if retry else None,
        retry_reason=retry,
        click=ClickReceipt(
            preparation,
            ClickTransportEvidence("posted", True, True, 3, 3)
            if dispatch == Dispatch.DISPATCHED
            else ClickTransportEvidence(),
            ClickContinuation(continuation or ("reselectWithinScope" if retry else "stop"), TARGET, family),
        ),
    )


class ClickDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        setDiagnosticHandler(None)

    async def test_unsupported_click_keeps_native_reason_before_any_dispatch(self):
        p = pointer("macos.hid.v1", "insufficientEvidence", supported=K.NO)
        reason = "the selected element is outside its window or known scrolling bounds"
        p = replace(p, click=replace(p.click, reason=reason))
        port = RecordingPort([observation(p)], [])
        with self.assertRaises(ComputerUseError) as caught:
            await Engine(port).execute(Intent("click"), TARGET)
        self.assertIn(reason, caught.exception.detail)
        self.assertEqual(caught.exception.effect, "none")
        self.assertFalse(port.commands)

    async def test_unavailable_click_keeps_compatible_offer_reason_without_input(self):
        physical = pointer("windows.sendInput", "unavailable", delivery="desktop_pointer")
        physical = replace(
            physical,
            click=replace(physical.click, reason="The selected point hits another UIA control in the original window."),
        )
        invoke = Primitive(
            "open",
            "native_action",
            Channel.OBJECT,
            K.YES,
            K.NO,
            ("receiver",),
            action="Invoke.Invoke",
            click=ClickOffer(
                "windows.uia.Invoke.Invoke",
                "semantic",
                "object_action",
                "unavailable",
                reason="An inapplicable opening action is unavailable.",
            ),
        )
        facts = observation(invoke, physical, platform="windows", native_role="ControlType.ListItem")
        port = RecordingPort([facts], [])
        with self.assertRaises(ComputerUseError) as caught:
            await Engine(port).execute(Intent("click"), TARGET)
        self.assertIn("hits another UIA control", str(caught.exception))
        self.assertNotIn("inapplicable opening action", str(caught.exception))
        self.assertEqual(caught.exception.effect, "none")
        self.assertFalse(port.commands)

    async def test_windows_item_without_select_keeps_pointer_instead_of_invoking_open(self):
        invoke = Primitive(
            "open",
            "native_action",
            Channel.OBJECT,
            K.YES,
            K.YES,
            action="Invoke.Invoke",
            click=ClickOffer("windows.uia.Invoke.Invoke", "semantic", "object_action", "ready"),
        )
        physical = pointer("windows.sendInput", delivery="desktop_pointer")
        for role in ("ListItem", "TreeItem", "DataItem"):
            facts = observation(invoke, physical, platform="windows", native_role="ControlType." + role)
            port = RecordingPort([facts], [receipt("windows.sendInput")])
            await Engine(port).execute(Intent("click"), TARGET)
            self.assertEqual([c.primitive.unit for _, c in port.commands], ["pointer_sequence"])

    async def test_foreground_selection_explains_prior_failure_and_gate_transition(self):
        events = []
        setDiagnosticHandler(events.append)
        p = pointer("pid", "preparationRequired", ("background", "foreground"))
        observed = observation(p, target_label="private control text")
        port = RecordingPort(
            [observed, observed],
            [receipt(dispatch=Dispatch.NOT_DISPATCHED, retry="preparationUnavailable"), receipt()],
            HostPolicy(foreground_assist=True),
        )
        await Engine(port).execute(Intent("click"), TARGET)
        selections = [e for e in events if e["kind"] == "click_selection"]
        decisions = [e for e in events if e["kind"] == "click_decision"]
        self.assertEqual([e["selected"]["foreground"] for e in selections], [False, True])
        self.assertEqual(
            selections[1]["exclusions"],
            [dict(routeFamily="pid", reason="priorFailure", observed="observation", step=1)],
        )
        self.assertEqual(
            [(e["decision"], e["action"], e["step"]) for e in decisions],
            [("next_route", "preparation_unavailable", 1), ("complete", None, 2)],
        )
        self.assertEqual(len({e["callId"] for e in selections + decisions}), 1)
        self.assertTrue(selections[0]["callId"])
        self.assertNotIn("private control text", repr(selections + decisions))
        self.assertEqual(len(port.commands), 2)

    async def test_unsafe_preparation_stop_retains_the_evidence_without_replay(self):
        events = []
        setDiagnosticHandler(events.append)
        prep = ClickPreparationEvidence(
            "pointerPreparation", TARGET, Dispatch.DISPATCHED, "contentPossible", "sameCandidate"
        )
        port = RecordingPort(
            [observation(pointer(), pointer("z_other"))],
            [receipt(dispatch=Dispatch.NOT_DISPATCHED, retry="routeUnavailable", preparation=(prep,))],
        )
        with self.assertRaises(ComputerUseError):
            await Engine(port).execute(Intent("click"), TARGET)
        (decision,) = [e for e in events if e["kind"] == "click_decision"]
        self.assertEqual(
            (decision["decision"], decision["dispatch"], decision["retryReason"]),
            ("stop", "notDispatched", "routeUnavailable"),
        )
        self.assertFalse(decision["preparationAllowsReplan"])
        self.assertEqual(decision["preparation"][0]["effect"], "contentPossible")
        self.assertEqual(len(port.commands), 1)

    async def test_unavailable_policy_records_refusal_without_native_input(self):
        events = []
        setDiagnosticHandler(events.append)
        port = RecordingPort([observation(pointer("hid", "preparationRequired", ("foreground",)))], [])
        with self.assertRaises(ComputerUseError):
            await Engine(port).execute(Intent("click"), TARGET)
        (selection,) = [e for e in events if e["kind"] == "click_selection"]
        self.assertIsNone(selection["selected"])
        self.assertFalse(selection["foregroundAllowed"])
        self.assertEqual(selection["exclusions"][0]["reason"], "noBackgroundPreparation")
        self.assertFalse(port.commands)

    async def test_broken_optional_sink_cannot_cancel_or_repeat_click(self):
        for error in (RuntimeError("sink failed"), asyncio.CancelledError("sink cancelled")):

            def broken(_, *, error=error):
                raise error

            setDiagnosticHandler(broken)
            port = RecordingPort([observation(pointer())], [receipt()])
            result = await Engine(port).execute(Intent("click"), TARGET)
            self.assertEqual(result["status"], "dispatched")
            self.assertEqual(len(port.commands), 1)


class ClickSelectionTests(unittest.TestCase):
    def test_row_selection_is_independent_of_pointer_geometry_and_frontmost_state(self):
        physical = pointer("hid", "insufficientEvidence", delivery="desktop_pointer")
        state = observation(row_selection(), physical, native_role="AXRow", actions_complete=True, enabled=K.UNKNOWN)
        for policy in (HostPolicy(), HostPolicy("exclusive")):
            command = choose(state, Intent("click"), policy, Attempts())
            self.assertEqual(command.primitive.unit, "native_selection")
            self.assertFalse(command.prepare or command.foreground)

    def test_row_selection_not_available_keeps_pointer_and_never_substitutes_explicit_action(self):
        unavailable = row_selection()
        unavailable = replace(
            unavailable, supported=K.NO, click=replace(unavailable.click, admission="insufficientEvidence")
        )
        state = observation(unavailable, pointer(), native_role="AXRow")
        self.assertEqual(choose(state, Intent("click"), HostPolicy(), Attempts()).primitive.channel, Channel.POINTER)
        for params in ({"x": 10, "y": 20}, {"button": "right"}, {"count": 2}):
            command = choose(
                observation(row_selection(), pointer(), native_role="AXRow"),
                Intent("click", params),
                HostPolicy(),
                Attempts(),
            )
            self.assertEqual(command.primitive.channel, Channel.POINTER)
        with self.assertRaises(ComputerUseError):
            choose(
                observation(row_selection(), native_role="AXRow"),
                Intent("action", {"action": "AXPress"}),
                HostPolicy(),
                Attempts(),
            )

    def test_semantic_order_is_explicit_not_family_spelling(self):
        selection = row_selection()
        selection = replace(selection, click=replace(selection.click, route_family="aaa"))
        press = semantic()
        press = replace(press, click=replace(press.click, route_family="zzz"))
        command = choose(observation(selection, press, pointer()), Intent("click"), HostPolicy(), Attempts())
        self.assertEqual(command.primitive, press)

    def test_ready_click_does_not_inherit_unknown_focus_enabled_or_legacy_projection(self):
        p = pointer(readiness=K.NO, pending=("receiver", "enabled"))
        command = choose(observation(p), Intent("click"), HostPolicy(), Attempts())
        self.assertEqual(command.primitive, p)
        self.assertFalse(command.prepare or command.foreground)

    def test_delivery_family_and_activation_are_stable_history_keys(self):
        state = observation(pointer(), pointer("hid", delivery="desktop_pointer"))
        command = choose(state, Intent("click"), HostPolicy(), Attempts())
        self.assertEqual(command.primitive.click.route_family, "hid")
        self.assertEqual(
            route_key(command), route_key(replace(command, primitive=replace(command.primitive, id="fresh")))
        )
        self.assertNotEqual(route_key(command), route_key(replace(command, foreground=True)))
        self.assertNotEqual(route_key(command), route_key(replace(command, primitive=state.primitives[0])))

    def test_background_semantic_action_precedes_foreground_physical(self):
        state = observation(pointer("sendinput", "preparationRequired", ("foreground",), "desktop_pointer"), semantic())
        command = choose(state, Intent("click"), HostPolicy(foreground_assist=True), Attempts())
        self.assertEqual(command.primitive.click.route_family, "ax")
        self.assertFalse(command.foreground)

    def test_all_background_candidates_precede_foreground_assistance(self):
        state = observation(
            pointer("one", "preparationRequired", ("background", "foreground")),
            pointer("two", "preparationRequired", ("background", "foreground")),
        )
        history = Attempts()
        first = choose(state, Intent("click"), HostPolicy(foreground_assist=True), history)
        history.unavailable.add(route_key(first))
        history.background_unavailable.add(route_key(first))
        history.failed_steps[route_key(first)] = 1
        second = choose(state, Intent("click"), HostPolicy(foreground_assist=True), history)
        self.assertFalse(second.foreground)
        self.assertNotEqual(first.primitive.click.route_family, second.primitive.click.route_family)
        history.unavailable.add(route_key(second))
        history.background_unavailable.add(route_key(second))
        history.failed_steps[route_key(second)] = 2
        last = choose(state, Intent("click"), HostPolicy(foreground_assist=True), history)
        self.assertTrue(last.foreground)
        self.assertEqual({e.step for e in last.click_selection.exclusions}, {1, 2})

    def test_physical_profile_excludes_semantic_fallback_without_platform_name(self):
        p = pointer("physical", "preparationRequired", ("foreground",), "desktop_pointer")
        p = replace(p, click=replace(p.click, preference_profile="physicalWhenAvailableInExclusive"))
        state = observation(p, semantic())
        history = Attempts()
        first = choose(state, Intent("click"), HostPolicy("exclusive"), history)
        self.assertEqual(first.primitive, p)
        history.unavailable.add(route_key(first))
        with self.assertRaises(ComputerUseError):
            choose(state, Intent("click"), HostPolicy("exclusive"), history)
        # Missing geometry never excludes an independently applicable object action.
        missing = replace(p, pending=("geometry",), click=replace(p.click, admission="insufficientEvidence"))
        self.assertEqual(
            choose(
                observation(missing, semantic()), Intent("click"), HostPolicy("exclusive"), Attempts()
            ).primitive.action,
            "AXPress",
        )

    def test_coordinate_right_and_double_click_cannot_become_object_actions(self):
        for params in ({"x": 10, "y": 20}, {"button": "right"}, {"count": 2}):
            command = choose(observation(semantic(), pointer()), Intent("click", params), HostPolicy(), Attempts())
            self.assertEqual(command.primitive.channel, Channel.POINTER)

    def test_shared_does_not_acquire_front_without_host_authorization(self):
        with self.assertRaises(ComputerUseError):
            choose(
                observation(pointer("hid", "preparationRequired", ("foreground",))),
                Intent("click"),
                HostPolicy(),
                Attempts(),
            )


class ClickExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_selection_readback_confirms_only_local_effect_and_never_replays(self):
        selected = row_selection()
        for confirmation in ("confirmed", "unavailable"):
            step = StepResult(
                TARGET,
                Dispatch.DISPATCHED,
                "Row selected; navigation unconfirmed.",
                confirmation=confirmation,
                click=ClickReceipt(
                    (),
                    ClickTransportEvidence("nativeSelection", True, True),
                    ClickContinuation("stop", TARGET, selected.click.route_family),
                ),
            )
            port = RecordingPort([observation(selected, pointer(), native_role="AXRow")], [step])
            result = await Engine(port).execute(Intent("click"), TARGET)
            self.assertEqual(result["status"], "verified" if confirmation == "confirmed" else "dispatched")
            self.assertEqual(len(port.commands), 1)
            self.assertEqual(port.commands[0][1].primitive.unit, "native_selection")
        failed = replace(step, dispatch=Dispatch.UNKNOWN, error_code="execution_unknown")
        port = RecordingPort([observation(selected, pointer(), native_role="AXRow")], [failed])
        with self.assertRaises(ComputerUseError):
            await Engine(port).execute(Intent("click"), TARGET)
        self.assertEqual(len(port.commands), 1)

    async def test_prepare_and_dispatch_are_one_command_without_success_reinspection(self):
        port = RecordingPort([observation(pointer("pid", "preparationRequired", ("background",)))], [receipt()])
        result = await Engine(port).execute(Intent("click"), TARGET)
        self.assertEqual(result["status"], "dispatched")
        self.assertEqual(port.inspections, 1)
        self.assertEqual(len(port.commands), 1)
        self.assertFalse(port.commands[0][1].prepare)
        self.assertIsNotNone(port.commands[0][1].click_selection)

    async def test_background_failure_allows_foreground_with_fresh_opaque_id(self):
        p = pointer("pid", "preparationRequired", ("background", "foreground"))
        port = RecordingPort(
            [observation(p), observation(replace(p, id="new-id"))],
            [receipt(retry="preparationUnavailable", dispatch=Dispatch.NOT_DISPATCHED), receipt()],
            HostPolicy(foreground_assist=True),
        )
        await Engine(port).execute(Intent("click"), TARGET)
        self.assertEqual([(c.prepare, c.foreground) for _, c in port.commands], [(False, False), (False, True)])

    async def test_partial_unknown_and_dispatched_error_never_replay(self):
        for dispatch in (Dispatch.PARTIAL, Dispatch.UNKNOWN, Dispatch.DISPATCHED):
            step = replace(receipt(dispatch=dispatch), error_code="input_unavailable")
            port = RecordingPort([observation(pointer(), pointer("z_other"))], [step, receipt("z_other")])
            with self.subTest(dispatch=dispatch), self.assertRaises(ComputerUseError):
                await Engine(port).execute(Intent("click"), TARGET)
            self.assertEqual(len(port.commands), 1)

    async def test_zero_submission_is_not_itself_retry_authority(self):
        step = replace(receipt(dispatch=Dispatch.NOT_DISPATCHED), error_code="input_unavailable")
        step = replace(
            step, click=replace(step.click, transport_evidence=ClickTransportEvidence("inserted", True, True, 0, 3))
        )
        port = RecordingPort([observation(pointer(), pointer("z_other"))], [step])
        with self.assertRaises(ComputerUseError):
            await Engine(port).execute(Intent("click"), TARGET)
        self.assertEqual(len(port.commands), 1)

    async def test_preparation_content_effect_blocks_replanning_even_with_clean_release(self):
        for effect, continuation in (("contentPossible", "sameCandidate"), ("unknown", "observe")):
            prep = ClickPreparationEvidence("pointerPreparation", TARGET, Dispatch.DISPATCHED, effect, continuation)
            port = RecordingPort(
                [observation(pointer(), pointer("z_other"))],
                [receipt(dispatch=Dispatch.NOT_DISPATCHED, retry="routeUnavailable", preparation=(prep,))],
            )
            with self.subTest(effect=effect), self.assertRaises(ComputerUseError) as error:
                await Engine(port).execute(Intent("click"), TARGET)
            self.assertEqual(error.exception.effect, "possible")
            self.assertEqual(len(port.commands), 1)

    async def test_same_candidate_refresh_is_bounded_and_cannot_select_new_ready_family(self):
        initial = observation(pointer())
        refreshed = observation(pointer("hid", delivery="desktop_pointer"), pointer(id="new-id"))
        pending = receipt(
            dispatch=Dispatch.NOT_DISPATCHED, retry="preconditionChanged", continuation="sameCandidateRefresh"
        )
        port = RecordingPort([initial, refreshed], [pending, pending])
        with self.assertRaises(ComputerUseError):
            await Engine(port).execute(Intent("click"), TARGET)
        self.assertEqual([c.primitive.click.route_family for _, c in port.commands], ["pid", "pid"])
        self.assertEqual(len(port.commands), 2)

    async def test_foreground_refresh_can_use_ready_same_family_without_reactivation(self):
        p = pointer("hid", "preparationRequired", ("foreground",), "desktop_pointer")
        ready = replace(pointer("hid", delivery="desktop_pointer"), id="fresh")
        port = RecordingPort(
            [observation(p), observation(ready)],
            [
                receipt("hid", Dispatch.NOT_DISPATCHED, "preconditionChanged", continuation="sameCandidateRefresh"),
                receipt("hid"),
            ],
            HostPolicy(foreground_assist=True),
        )
        await Engine(port).execute(Intent("click"), TARGET)
        self.assertEqual([(c.prepare, c.foreground) for _, c in port.commands], [(False, True), (False, False)])

    async def test_background_refresh_cannot_upgrade_to_foreground_authority(self):
        p = pointer("pid", "preparationRequired", ("background", "foreground"))
        foreground = replace(p, id="fresh", click=replace(p.click, preparation_options=("foreground",)))
        port = RecordingPort(
            [observation(p), observation(foreground)],
            [
                receipt(
                    dispatch=Dispatch.NOT_DISPATCHED, retry="preconditionChanged", continuation="sameCandidateRefresh"
                )
            ],
            HostPolicy(foreground_assist=True),
        )
        with self.assertRaises(ComputerUseError):
            await Engine(port).execute(Intent("click"), TARGET)
        self.assertEqual(len(port.commands), 1)

    async def test_refresh_does_not_reopen_consumed_foreground_attempt(self):
        p = pointer("pid", "preparationRequired", ("foreground",))
        ready = pointer("pid", options=("foreground",))
        port = RecordingPort(
            [observation(p), observation(ready), observation(p)],
            [
                receipt(
                    dispatch=Dispatch.NOT_DISPATCHED, retry="preconditionChanged", continuation="sameCandidateRefresh"
                ),
                receipt(dispatch=Dispatch.NOT_DISPATCHED, retry="preparationUnavailable"),
            ],
            HostPolicy(foreground_assist=True),
        )
        with self.assertRaises(ComputerUseError):
            await Engine(port).execute(Intent("click"), TARGET)
        self.assertEqual([c.foreground for _, c in port.commands], [True, False])

    async def test_delivery_mismatch_retires_call_instead_of_changing_route(self):
        port = RecordingPort([observation(pointer())], [receipt("wrong")])
        with self.assertRaises(ComputerUseError):
            await Engine(port).execute(Intent("click"), TARGET)
        self.assertTrue(port.calls.retired)
        self.assertEqual(len(port.commands), 1)
