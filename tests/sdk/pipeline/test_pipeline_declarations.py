"""New declaration forms retain the existing runner's observable behavior."""

import asyncio
import unittest
from contextlib import asynccontextmanager, contextmanager
from dataclasses import FrozenInstanceError
from inspect import signature

from computer_use.pipeline import (
    Continue,
    Contract,
    Control,
    Done,
    Effect,
    Pass,
    PassDefinition,
    Pipeline,
    Repeat,
    Scope,
    Select,
    Sequence,
)


class PipelineDeclarationTests(unittest.IsolatedAsyncioTestCase):
    async def test_pipeline_builds_once_after_initialization_and_is_transparent(self):
        builds, calls, events = [], [], []

        class Increment(Pipeline[int, int]):
            def __init__(self, increment):
                self.increment = increment
                super().__init__(root=self.build())

            def build(self):
                builds.append(self.increment)
                return Sequence(
                    name="increment",
                    steps=[
                        Pass("add", lambda value, _: calls.append(value) or value + self.increment, Contract(int, int))
                    ],
                )

        flow = Increment(2)
        self.assertEqual(builds, [2])
        self.assertEqual(calls, [])
        root = flow.root
        self.assertEqual(flow.name, root.name)
        self.assertIs(flow.contract, root.contract)
        control = Control(diagnostic=events.append, call_id="same-call")
        self.assertEqual(flow.run(3, control), 5)
        actual = events[:]
        events.clear()
        self.assertEqual(root.run(3, control), 5)
        self.assertEqual(events, actual)
        events.clear()
        self.assertEqual(await flow.arun(3, control), 5)
        self.assertEqual(events, actual)
        self.assertEqual(builds, [2])
        self.assertIs(flow.root, root)
        self.assertEqual(
            [event.path for event in actual],
            [("increment",), ("increment", "add"), ("increment", "add"), ("increment",)],
        )

    def test_base_constructor_does_not_call_build(self):
        class ExplicitRoot(Pipeline):
            def build(self):
                self.fail("base constructor must not call overridable build")

        root = Sequence("empty")
        flow = ExplicitRoot(root=root)
        self.assertIs(flow.root, root)
        self.assertEqual(flow.run(4), 4)

    async def test_pass_definitions_bind_once_without_executing_and_preserve_signature(self):
        bindings, calls = [], []

        def create(*, increment: int = 2):
            bindings.append(increment)

            async def add(value, _):
                calls.append(value)
                return value + increment

            return add

        contract = Contract(int, int, {Effect.QUERY}, requires=("facts",))
        definition = Pass.factory(name="add", factory=create, contract=contract)
        self.assertIsInstance(definition, PassDefinition)
        self.assertEqual(signature(definition), signature(create))
        self.assertEqual(bindings, [])
        self.assertEqual(calls, [])
        first, second = definition(increment=3), definition()
        self.assertEqual(bindings, [3, 2])
        self.assertEqual(calls, [])
        self.assertIs(first.contract, contract)
        self.assertEqual(first.name, "add")
        self.assertIsNot(first, second)
        self.assertEqual(await first.arun(4), 7)
        self.assertEqual(await second.arun(4), 6)
        self.assertEqual(calls, [4, 4])
        self.assertEqual(bindings, [3, 2])
        with self.assertRaises(FrozenInstanceError):
            definition.name = "other"
        with self.assertRaises(TypeError):
            definition(unexpected=1)
        self.assertEqual(bindings, [3, 2])

    async def test_dependency_free_definition_keeps_original_callback(self):
        calls = []

        def compute(value, _):
            calls.append(value)
            return value + 1

        definition = Pass.define(name="compute", function=compute, contract=Contract(int, int))
        self.assertEqual(str(signature(definition)), "()")
        first, second = definition(), definition()
        self.assertEqual(calls, [])
        self.assertIs(first.function, compute)
        self.assertIs(second.function, compute)
        self.assertIsNot(first, second)
        self.assertEqual(first.run(2), 3)
        self.assertEqual(await second.arun(3), 4)
        self.assertEqual(calls, [2, 3])

    async def test_keyword_composition_retains_results_order_gates_and_full_events(self):
        def assemble(modern, calls):
            def leaf(name, function, effects=frozenset({Effect.PURE})):
                contract = Contract(object, object, effects)
                return (
                    Pass.define(name=name, function=function, contract=contract)()
                    if modern
                    else Pass(name, function, contract)
                )

            def observe(value, control):
                control.remaining()
                calls.append(("observe", value, control.attempt, control.call_id))
                return value + 1

            body = Select(
                name="route",
                selector=leaf("choose", lambda *_: "query"),
                branches={
                    "query": leaf("observe", observe, {Effect.QUERY}),
                    "unused": leaf("send", lambda *_: self.fail("unselected dispatch"), {Effect.DISPATCH}),
                },
            )
            gate = leaf("gate", lambda value, _: Continue(value) if value < 3 else Done(value))

            def consume(value, decision, control):
                calls.append(("consume", value, type(decision).__name__, control.attempt))

            loop = (
                Repeat(name="refresh", body=body, gate=gate, on_decision=consume, bounded_by="existing deadline")
                if modern
                else Repeat("refresh", body, gate, consume, bounded_by="existing deadline")
            )

            @contextmanager
            def lease(value, control):
                calls.append(("acquire", control.call_id))
                try:
                    yield value
                finally:
                    calls.append(("release", control.call_id))

            scoped = Scope(name="lease", using=lease, body=loop) if modern else Scope("lease", lease, loop)
            format_result = leaf("format", lambda value, _: str(value))
            flow = (
                Sequence(name="root", steps=[scoped, format_result])
                if modern
                else Sequence("root", scoped, format_result)
            )
            self.assertEqual(calls, [], "declaration cannot execute or acquire resources")
            return flow

        for asynchronous in (False, True):
            results = []
            for modern in (False, True):
                calls, events, checks = [], [], []
                flow = assemble(modern, calls)
                control = Control(
                    remaining=lambda *, checks=checks: checks.append("budget") or 100,
                    diagnostic=events.append,
                    call_id="request",
                )
                value = await flow.arun(0, control) if asynchronous else flow.run(0, control)
                results.append((value, calls, events, checks, flow.contract))
            self.assertEqual(results[0], results[1])
            self.assertEqual(results[1][0], "3")
            self.assertEqual(results[1][3], ["budget"] * 3)
            self.assertEqual([call[1] for call in results[1][1] if call[0] == "consume"], [1, 2, 3])

    async def test_keyword_scope_preserves_cancellation_cleanup_and_no_dispatch_replay(self):
        results = []
        for modern in (False, True):
            calls, events = [], []

            @asynccontextmanager
            async def lease(value, _, *, calls=calls):
                calls.append("acquire")
                try:
                    yield value
                finally:
                    calls.append("release")

            async def dispatch(value, _, *, calls=calls):
                calls.append("send")
                raise asyncio.CancelledError()

            contract = Contract(object, object, {Effect.DISPATCH})
            body = (
                Pass.define(name="send", function=dispatch, contract=contract)()
                if modern
                else Pass("send", dispatch, contract)
            )
            flow = Scope(name="native", using=lease, body=body) if modern else Scope("native", lease, body)
            with self.assertRaises(asyncio.CancelledError):
                await flow.arun(None, Control(diagnostic=events.append))
            results.append((calls, events, flow.contract))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1][0], ["acquire", "send", "release"])

    async def test_keyword_scope_preserves_cleanup_error_and_error_events(self):
        for asynchronous in (False, True):
            results = []
            for modern in (False, True):
                events = []

                @contextmanager
                def lease(value, _):
                    try:
                        yield value
                    finally:
                        raise LookupError("cleanup")

                leaf = Pass("value", lambda value, _: value, Contract(int, int))
                flow = Scope(name="scope", using=lease, body=leaf) if modern else Scope("scope", lease, leaf)
                with self.assertRaisesRegex(LookupError, "cleanup"):
                    if asynchronous:
                        await flow.arun(1, Control(diagnostic=events.append))
                    else:
                        flow.run(1, Control(diagnostic=events.append))
                results.append(events)
            self.assertEqual(results[0], results[1])

    def test_keyword_repeat_keeps_decision_consumption_before_limit_error(self):
        results = []
        for modern in (False, True):
            calls, events = [], []
            body = Pass("value", lambda value, _: value, Contract(int, int))
            gate = Pass("gate", lambda value, _: Continue(value), Contract(int, Continue))

            def consume(value, decision, _, *, calls=calls):
                calls.append((value, decision))

            flow = (
                Repeat(name="loop", body=body, gate=gate, on_decision=consume, max_iterations=1)
                if modern
                else Repeat("loop", body, gate, consume, max_iterations=1)
            )
            with self.assertRaisesRegex(RuntimeError, "Pipeline repetition limit exhausted"):
                flow.run(4, Control(diagnostic=events.append))
            results.append((calls, events, flow.contract))
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(results[1][0]), 1)

    def test_ambiguous_declaration_arguments_fail_before_execution(self):
        leaf = Pass("value", lambda value, _: value, Contract(int, int))
        gate = Pass("gate", lambda value, _: Done(value), Contract(int, Done))
        with self.assertRaisesRegex(TypeError, "positional steps"):
            Sequence("flow", leaf, steps=[leaf])
        with self.assertRaisesRegex(TypeError, "on_decision"):
            Repeat("loop", leaf, gate, lambda *_: None, on_decision=lambda *_: None, max_iterations=1)
        with self.assertRaisesRegex(TypeError, "using"):
            Scope("scope", lambda *_: None, leaf, using=lambda *_: None)


if __name__ == "__main__":
    unittest.main()
