"""Control flow, branch isolation and cleanup contracts of the shared runner."""

import asyncio
import unittest
from contextlib import asynccontextmanager, contextmanager

from computer_use.pipeline import Continue, Contract, Control, Done, Effect, Pass, Repeat, Scope, Select, Sequence


def pure(name, function):
    return Pass(name, function, Contract(object, object))


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_sequence_and_unselected_branch_preserve_sync_async_order(self):
        calls = []
        selected = Pass(
            "query", lambda value, _: calls.append("selected") or value + 1, Contract(int, int, {Effect.QUERY})
        )
        unused = Pass(
            "input", lambda *_: self.fail("unselected input dispatched"), Contract(int, int, {Effect.DISPATCH})
        )
        flow = Sequence(
            "read",
            Select("component", pure("select", lambda *_: "tree"), {"tree": selected, "input": unused}),
            pure("format", lambda value, _: str(value)),
        )
        self.assertEqual(flow.run(4), "5")
        self.assertEqual(await flow.arun(4), "5")
        self.assertEqual(calls, ["selected", "selected"])

    async def test_gate_has_one_state_consumer_and_one_shared_control_budget(self):
        progress, checks = [], []
        control = Control(remaining=lambda: checks.append("budget") or 100)
        body = pure("observe", lambda value, c: (c.remaining(), value + 1)[1])
        gate = pure("gate", lambda value, _: Continue(value) if value < 3 else Done(value))
        flow = Repeat(
            "refresh",
            body,
            gate,
            lambda value, decision, _: progress.append((value, type(decision))),
            bounded_by="existing shared deadline and refresh allowance",
        )
        self.assertEqual(await flow.arun(0, control), 3)
        self.assertEqual(progress, [(1, Continue), (2, Continue), (3, Done)])
        self.assertEqual(len(checks), 3)

    async def test_scope_releases_on_cancel_without_retrying_dispatch(self):
        events = []

        @asynccontextmanager
        async def lease(value, _):
            events.append("begin")
            try:
                yield value
            finally:
                events.append("finish")

        async def send(value, _):
            events.append("send")
            raise asyncio.CancelledError()

        flow = Scope("native", lease, Pass("dispatch", send, Contract(object, object, {Effect.DISPATCH})))
        with self.assertRaises(asyncio.CancelledError):
            await flow.arun(None)
        self.assertEqual(events, ["begin", "send", "finish"])

    async def test_scope_preserves_cleanup_exception_and_sync_manager_semantics(self):
        @contextmanager
        def lease(value, _):
            try:
                yield value
            finally:
                raise LookupError("cleanup")

        flow = Scope("scope", lease, pure("value", lambda value, _: value))
        for run in (lambda: flow.run(1),):
            with self.assertRaisesRegex(LookupError, "cleanup"):
                run()
        with self.assertRaisesRegex(LookupError, "cleanup"):
            await flow.arun(1)

    async def test_diagnostics_have_paths_and_no_payload_or_effect_on_failure(self):
        events = []
        flow = Sequence("main", pure("a", lambda value, _: value))
        self.assertEqual(flow.run("secret", Control(diagnostic=events.append)), "secret")
        self.assertEqual([e.path for e in events], [("main",), ("main", "a"), ("main", "a"), ("main",)])
        self.assertNotIn("secret", repr(events))

        def broken(_):
            raise ValueError("diagnostics unavailable")

        self.assertEqual(await flow.arun("result", Control(diagnostic=broken)), "result")

    def test_only_pure_selectors_and_gates_can_control_flow(self):
        query = Pass("query", lambda *_: "a", Contract(object, object, {Effect.QUERY}))
        with self.assertRaises(ValueError):
            Select("branch", query, {})
        with self.assertRaises(ValueError):
            Repeat("loop", query, query, max_iterations=1)
        with self.assertRaises(ValueError):
            Repeat("loop", pure("body", lambda *_: 1), pure("gate", lambda *_: Done(1)))
        bounded = Repeat(
            "loop", pure("body", lambda *_: 1), pure("gate", lambda value, _: Continue(value)), max_iterations=1
        )
        with self.assertRaises(RuntimeError):
            bounded.run(0)

    def test_sync_runner_does_not_silently_drop_an_async_stage(self):
        async def compute(value, _):
            return value

        with self.assertRaises(TypeError):
            Pass("async", compute, Contract(object, object)).run(1)
