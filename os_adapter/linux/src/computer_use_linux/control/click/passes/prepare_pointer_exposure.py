# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Prepare pointer reachability without imposing keyboard-focus readiness."""

from computer_use_linux.contracts.protocol import Failure, require


def prepare_pointer_exposure(operation, plan, ledger, ctx):
    unit, contract = plan.unit, plan.contract
    points, selected, native, ready = unit.click_state(operation, contract, ctx)
    if ready:
        return True
    if not plan.foreground:
        return False
    require(
        selected is not None and native is not None and not native.override_redirect,
        "An unmanaged popup cannot be activated to repair a pointer route.",
        "input_unavailable",
    )
    unit.desktop.require_idle_input()
    ctx.check()
    evidence = dict(
        kind="activation",
        scope=dict(contract["target"]),
        dispatch="unknown",
        effect="contextOnly",
        continuation="withinScope",
        observations=[],
        wait="notNeeded",
    )
    ledger.preparation.append(evidence)
    try:
        unit.desktop.expose(
            selected, points[0], ctx, is_reachable=lambda: unit.click_state(operation, contract, ctx)[3]
        )
        # This query preserves the original binding. Element geometry may move
        # during preparation; capture coordinates cannot be silently rebound.
        points, selected, _, ready = unit.click_state(operation, contract, ctx)
        require(ready, "The selected point remains covered after preparation.", "input_unavailable")
        evidence.update(
            dispatch="dispatched",
            wait="satisfied",
            observations=["The original target is reachable by pointer; keyboard focus is not a prerequisite."],
        )
        return True
    except Exception as error:
        evidence.update(
            effect="unknown",
            continuation="observe",
            wait="exhausted"
            if isinstance(error, Failure) and error.code in ("deadline_exceeded", "input_unavailable")
            else "unavailable",
            observations=[str(error)[:4096]],
        )
        raise


CONTRACT = {
    "input": ("operation", "plan", "ledger", "ctx"),
    "output": "pointer readiness",
    "effects": ("QUERY", "PREPARE"),
    "borrows": ("operation", "plan", "ledger", "ctx"),
    "writes": ("operation click preparation ledger",),
    "failure_boundary": "no requested input; retain preparation influence; never switch strategy",
}
