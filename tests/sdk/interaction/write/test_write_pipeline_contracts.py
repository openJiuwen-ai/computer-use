"""Write identity, Gate ownership, native call boundaries and trace metadata."""

import asyncio
import io
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

from sdk.interaction.write.test_execution_engine import KEY, OBJECT, READY, TARGET, facts, result

from computer_use.diagnostics import pipelineDiagnostic, setDiagnosticHandler
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.model import (
    AttemptOutcome,
    BoundWrite,
    OperationSpec,
    ResolutionMemory,
    ResolvedTarget,
    ResolvedWrite,
    RoutePlan,
)
from computer_use.interaction.write.passes.parse_write_intent import parse_request
from computer_use.interaction.write.progress import WriteRunState, classify_write_progress
from computer_use.interaction.write.routes.pipeline import RouteExecutionPipeline, RouteSelectionPipeline
from computer_use.native.contracts import (
    Actuator,
    Cleanup,
    Dispatch,
    FocusMember,
    HostPolicy,
    Intent,
    OpenedOperation,
    SelectionDependency,
)
from computer_use.native.lifecycle import CallLifecycle
from computer_use.pipeline import Continue, Contract, Control, Done, Effect, Pass, Repeat, Scope, Select, Sequence


def resolved(state=None, *, intent=None):
    state = state or facts(OBJECT)
    spec = OperationSpec(intent or Intent("replace", {"text": "requested literal"}), TARGET, ("outer",), HostPolicy())
    bound = BoundWrite(spec, "operation", lambda: 1000)
    target = ResolvedTarget(spec, state, (SelectionDependency("observed", TARGET.node),), False)
    return ResolvedWrite(bound, target, ResolutionMemory(within=spec.within))


class WriteContractTests(unittest.IsolatedAsyncioTestCase):
    def test_normalized_query_path_is_frozen_and_independent_of_caller(self):
        source = [{"name": "Original"}]
        request = parse_request((Intent("click"), TARGET, source, 0), None)
        source[0]["name"] = "Caller changed"
        self.assertEqual(request.path[0]["name"], "Original")
        with self.assertRaises(TypeError):
            request.path[0]["name"] = "A later Pass changed it"

    def test_original_owner_and_intent_remain_separate_from_internal_actuator_and_receiver(self):
        state = facts(KEY, context=READY, actuator=Actuator("internal-editor"), receiver=FocusMember("live-focus"))
        source = resolved(state, intent=Intent("insert", {"text": "requested literal"}))
        command = RoutePlan(source, state.primitives[0], False, False).command()
        self.assertEqual(command.target, TARGET)
        self.assertIs(command.intent, source.bound.spec.intent)
        self.assertEqual(command.actuator.node, "internal-editor")
        self.assertEqual(command.receiver.node, "live-focus")
        self.assertNotIn(command.target.node, (command.actuator.node, command.receiver.node))
        with self.assertRaises(FrozenInstanceError):
            source.bound.spec.target = replace(TARGET, node="other")
        with self.assertRaises(TypeError):
            command.intent.parameters["text"] = "changed"

    def test_receiver_refresh_replaces_facts_without_rebinding_original_target(self):
        old = resolved(facts(KEY, context=READY, actuator=Actuator("first-editor")))
        fresh_facts = replace(old.target.facts, observed="new-sample", actuator=Actuator("new-editor"))
        fresh = replace(old, target=replace(old.target, facts=fresh_facts))
        command = RoutePlan(fresh, fresh_facts.primitives[0], False, False).command()
        self.assertEqual(command.target, TARGET)
        self.assertEqual(command.actuator.node, "new-editor")
        self.assertEqual(old.target.facts.actuator.node, "first-editor")
        self.assertIs(fresh.bound.spec, old.bound.spec)

    def test_gate_is_pure_until_one_state_owner_consumes_its_decision(self):
        source, owner, sibling = resolved(), WriteRunState(), WriteRunState()
        command = RoutePlan(source, OBJECT, False, False).command()
        outcome = AttemptOutcome(
            source,
            command,
            result(Dispatch.NOT_DISPATCHED, error_code="input_unavailable", retry_reason="preconditionChanged"),
        )
        before = owner.view()
        decision = classify_write_progress(outcome, before)
        self.assertEqual(decision.kind, "reinspect")
        self.assertEqual(owner.view(), before)
        owner.consume(outcome, decision)
        self.assertEqual(owner.attempts.precondition_changes, 1)
        self.assertEqual(sibling.attempts.precondition_changes, 0)
        self.assertEqual(before.history.precondition_changes, 0)

    def test_unknown_partial_and_dispatched_steps_cannot_become_replans(self):
        source = resolved()
        command = RoutePlan(source, OBJECT, False, False).command()
        for dispatch, expected in (
            (Dispatch.UNKNOWN, "stop"),
            (Dispatch.PARTIAL, "stop"),
            (Dispatch.DISPATCHED, "complete"),
        ):
            owner = WriteRunState(dispatched=dispatch)
            decision = classify_write_progress(AttemptOutcome(source, command, result(dispatch)), owner.view())
            self.assertEqual(decision.kind, expected)
            self.assertNotIn(decision.kind, ("reinspect", "next_route"))

    async def test_native_unit_marks_inflight_before_await_and_preserves_uncertain_dispatch(self):
        for preparing in (False, True):
            owner, trace = WriteRunState(), []
            native = SimpleNamespace(calls=SimpleNamespace(retired=False))

            async def execute(operation, step, command, budget, *, owner=owner, trace=trace):
                self.assertTrue(owner.in_flight)
                self.assertEqual((operation, step, budget), ("operation", 1, 1000))
                trace.append(command)
                raise OSError("native reply missing")

            native.execute = execute
            source = resolved()
            plan = RoutePlan(source, OBJECT, preparing, False)
            with self.assertRaisesRegex(OSError, "native reply missing"):
                await RouteExecutionPipeline(native, owner).arun(plan)
            self.assertFalse(owner.in_flight)
            self.assertTrue(native.calls.retired)
            self.assertEqual(len(trace), 1)
            self.assertEqual(owner.dispatched, Dispatch.NOT_DISPATCHED if preparing else Dispatch.UNKNOWN)
            self.assertEqual(owner.preparation, ["unknown"] if preparing else [])

    async def test_known_native_admission_refusal_does_not_retire_or_claim_dispatch(self):
        owner = WriteRunState()
        native = SimpleNamespace(calls=SimpleNamespace(retired=False))
        refusal = ComputerUseError(
            dict(code="invalid_request", effect="none", target="field", detail="refused", next="correct")
        )

        async def execute(*_):
            self.assertTrue(owner.in_flight)
            raise refusal

        native.execute = execute
        with self.assertRaises(ComputerUseError) as caught:
            await RouteExecutionPipeline(native, owner).arun(RoutePlan(resolved(), OBJECT, False, False))
        self.assertIs(caught.exception, refusal)
        self.assertFalse(owner.in_flight or native.calls.retired)
        self.assertEqual(owner.dispatched, Dispatch.NOT_DISPATCHED)
        self.assertFalse(owner.preparation)

    def test_route_choice_is_pure_and_does_not_enter_execution_or_change_history(self):
        native = SimpleNamespace(policy=HostPolicy())
        owner, trace = WriteRunState(), []
        before = owner.view()
        request = resolved()
        selected = RouteSelectionPipeline(native, owner).run(request, Control(diagnostic=trace.append))
        self.assertIs(selected.primitive, request.target.facts.primitives[0])
        self.assertEqual(owner.view(), before)
        self.assertTrue(all(event.effects == frozenset({Effect.PURE}) for event in trace))
        self.assertFalse(any("dispatch" in event.path for event in trace))


class PipelineDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        setDiagnosticHandler(None)

    async def test_one_call_id_spans_stages_and_next_call_gets_another(self):
        events = []

        class Port:
            pipeline_diagnostic = staticmethod(events.append)

            async def begin(self, intent, target, budget):
                return OpenedOperation("write", target)

            async def begin_read(self, target, budget):
                return OpenedOperation("read", target)

            async def finish(self, operation):
                return Cleanup.COMPLETE

        calls = CallLifecycle(Port())
        with calls.claim(2000) as remaining:
            control = calls.control(remaining)
            self.assertIs(control.remaining, remaining)
            self.assertIsNotNone(control.call_id)
            async with calls.operation(TARGET, remaining):
                control.child("read").emit("observed", frozenset({Effect.QUERY}))
            async with calls.operation(TARGET, remaining, intent=Intent("replace", {"text": "secret"})):
                control.child("write").emit("sent", frozenset({Effect.DISPATCH}), dispatch="dispatched")
        ids = {event.call_id for event in events}
        self.assertEqual(ids, {control.call_id})
        self.assertEqual([event.cleanup for event in events if event.phase == "cleanup"], ["complete", "complete"])
        with calls.claim(2000) as remaining:
            self.assertNotIn(calls.control(remaining).call_id, ids)
        self.assertNotIn("secret", repr(events))

    async def test_select_repeat_and_sub_budget_preserve_identity_and_record_current_branch_attempt(self):
        events, budget_reads = [], []
        control = Control(
            remaining=lambda: budget_reads.append("read") or 123, diagnostic=events.append, call_id="one-call"
        )
        child = control.child("request").with_remaining(lambda: 50)
        body = Select(
            "route",
            Pass("choose", lambda value, _: "main", Contract(int, str)),
            {
                "main": Pass("value", lambda value, _: value + 1, Contract(int, int)),
                "unused": Pass("unused", lambda *_: self.fail("unselected"), Contract(int, int)),
            },
        )
        flow = Repeat(
            "attempts",
            body,
            Pass(
                "gate", lambda value, _: Done(value) if value == 2 else Continue(value), Contract(int, Continue | Done)
            ),
            max_iterations=2,
        )
        self.assertEqual(await flow.arun(0, child), 2)
        self.assertFalse(budget_reads, "metadata propagation must not add deadline checks")
        leaf = [event for event in events if event.path[-1] == "value" and event.phase == "enter"]
        self.assertEqual(
            [(event.call_id, event.attempt, event.branch) for event in leaf],
            [("one-call", 1, "main"), ("one-call", 2, "main")],
        )
        self.assertTrue(all(event.cleanup is None for event in events))

    def test_generic_scope_never_invents_a_native_cleanup_receipt(self):
        events = []

        @contextmanager
        def lease(value, _):
            yield value

        body = Pass("value", lambda value, _: value, Contract(int, int))
        self.assertEqual(Scope("scope", lease, body).run(5, Control(diagnostic=events.append)), 5)
        self.assertTrue(events)
        self.assertTrue(all(event.cleanup is None for event in events))

    async def test_optional_pipeline_diagnostics_cannot_cancel_or_change_an_operation(self):
        def broken(_):
            raise asyncio.CancelledError("broken optional sink")

        flow = Sequence("flow", Pass("value", lambda value, _: value + 1, Contract(int, int)))
        self.assertEqual(await flow.arun(3, Control(diagnostic=broken)), 4)

    async def test_cleanup_failure_keeps_primary_error_and_never_fabricates_complete(self):
        events = []

        class Port:
            pipeline_diagnostic = staticmethod(events.append)

            async def begin_read(self, target, budget):
                return OpenedOperation("read", target)

            async def finish(self, operation):
                raise OSError("finish reply lost")

        calls = CallLifecycle(Port())
        primary = ValueError("primary")
        with self.assertRaises(ValueError) as caught:
            with calls.claim(1000) as remaining:
                async with calls.operation(TARGET, remaining):
                    raise primary
        self.assertIs(caught.exception, primary)
        self.assertTrue(calls.retired)
        self.assertTrue(any(event.phase == "cleanup_error" and event.error_type == "OSError" for event in events))
        self.assertFalse(any(event.cleanup is not None for event in events))
        self.assertIn("cleanup unconfirmed", primary.__notes__[0])

    def test_connection_sink_uses_existing_handler_and_contains_only_control_metadata(self):
        events = []
        setDiagnosticHandler(events.append)
        control = Control(call_id="call", attempt=2, branch="text", diagnostic=pipelineDiagnostic)
        control.child("native").emit("receipt", frozenset({Effect.RESOURCE}), cleanup="complete", dispatch="dispatched")
        self.assertEqual(events[0]["kind"], "pipeline")
        self.assertEqual((events[0]["callId"], events[0]["attempt"], events[0]["branch"]), ("call", 2, "text"))
        self.assertEqual((events[0]["cleanup"], events[0]["dispatch"]), ("complete", "dispatched"))
        self.assertNotIn("payload", events[0])
        setDiagnosticHandler(None)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            control.emit("receipt", frozenset({Effect.RESOURCE}), cleanup="complete")
        self.assertEqual((stdout.getvalue(), stderr.getvalue()), ("", ""))


if __name__ == "__main__":
    unittest.main()
