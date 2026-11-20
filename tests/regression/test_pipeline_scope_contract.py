# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compatibility of the generic scope's interpreter context boundary."""

from unittest import IsolatedAsyncioTestCase

from computer_use.pipeline import Contract, Effect, Pass, Scope, Select, Sequence


class PipelineScopeContractTests(IsolatedAsyncioTestCase):
    async def test_async_suppression_proceeds_to_the_same_managers_sync_context(self):
        events = []

        class DualManager:
            async def __aenter__(self):
                events.append("async enter")
                return "async resource"

            async def __aexit__(self, error_type, _error, _traceback):
                events.append(("async exit", error_type))
                return True

            def __enter__(self):
                events.append("sync enter")
                return "sync resource"

            def __exit__(self, error_type, _error, _traceback):
                events.append(("sync exit", error_type))

        async def execute(resource, _control):
            events.append(resource)
            if resource == "async resource":
                raise ValueError("suppressed by the first context")
            return resource

        scope = Scope("Resource", using=lambda *_: DualManager(), body=Pass("Body", execute, Contract(object, str)))
        self.assertEqual(await scope.arun(None), "sync resource")
        self.assertEqual(events, ["async enter", "async resource", ("async exit", ValueError),
                                  "sync enter", "sync resource", ("sync exit", None)])


    def test_select_rejects_impure_selector_before_materializing_branches(self):
        class UnusedBranches:
            def __iter__(self):
                raise AssertionError("branch mapping evaluated before selector admission")

        selector = Pass("Select", lambda _value, _control: 0, Contract(object, int, {Effect.DISPATCH}))
        try:
            raise LookupError("existing failure")
        except LookupError as active:
            with self.assertRaisesRegex(ValueError, "A branch selector must be pure") as rejected:
                Select("Rejected", selector, UnusedBranches())
            self.assertIs(rejected.exception.__context__, active)

    def test_composite_contract_keeps_generator_failure_cause(self):
        class BrokenStep:
            @property
            def contract(self):
                raise StopIteration("declared contract ended")

        with self.assertRaisesRegex(RuntimeError, "generator raised StopIteration") as rejected:
            Sequence("Broken", BrokenStep())
        self.assertIsInstance(rejected.exception.__cause__, StopIteration)
        self.assertEqual(str(rejected.exception.__cause__), "declared contract ended")
