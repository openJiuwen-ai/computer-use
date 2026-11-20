# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import gc
from weakref import ref

from computer_use.session.operations import OperationBudget


def test_budget_callbacks_can_update_the_existing_state():
    budget = OperationBudget(2000, clock=lambda: 0.0)

    def stop():
        budget.deadline = 3.0
        return False

    def now():
        budget.deadline = 4.0
        return 1.0

    budget.cancelled, budget._clock = stop, now
    assert budget.remaining() == 2000
    assert budget.deadline == 4.0


def test_budget_preserves_host_clock_comparison_result():
    sentinel = object()

    class Tick:
        def __ge__(self, other):
            assert other == 1.0
            return sentinel

    budget = OperationBudget(1000, clock=lambda: 0.0)
    budget._clock = Tick
    assert budget.expired is sentinel
    assert budget.target_exceeded is sentinel


def test_budget_does_not_retain_a_host_callback_cycle():
    budget = OperationBudget(clock=lambda: 0.0)
    budget.cancelled = lambda captured=budget: captured is None
    handle = ref(budget)
    del budget
    gc.collect()
    assert handle() is None
