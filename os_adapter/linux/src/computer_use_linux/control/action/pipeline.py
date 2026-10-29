# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""One action route; final guard, journal and send stay adjacent."""

from contextlib import nullcontext

from computer_use_linux.contracts.protocol import Failure

from .passes.build_dispatch_receipt import build_dispatch_receipt
from .passes.check_dispatch_conditions import check_dispatch_conditions
from .passes.collect_action_index import collect_action_index
from .passes.mark_dispatch_in_flight import mark_dispatch_in_flight
from .passes.resolve_native_target import resolve_native_target
from .passes.send_native_unit import send_native_unit


class NativeActionPipeline:
    def __init__(self, ax, target, facts):
        self.ax, self.target, self.facts = ax, target, facts

    def run(self, operation, command, ctx, click_ledger=None, diagnostics=None):
        measure = diagnostics.stage if diagnostics is not None else lambda _: nullcontext()
        with measure("resolveActionTarget"):
            contract, selected, target, within = resolve_native_target(operation, command)
        dispatch, error_code = "notDispatched", None
        detail = "Native action was not dispatched."
        try:
            self.ax.begin(ctx)
            with measure("collectActionIndex"):
                index = collect_action_index(contract, selected, target, within, ctx, self.target, self.facts)
            with measure("checkActionBoundary"):
                obj = check_dispatch_conditions(contract, selected, target, within, index, ctx, self.target)
            with measure("dispatchNativeAction"):
                mark_dispatch_in_flight(operation, ctx)
                dispatch = "unknown"
                send_native_unit(obj, index, self.target, click_ledger)
                dispatch = "dispatched"
                detail = "Native action " + selected["name"] + " accepted; read to verify its effect."
                ctx.check()
        except (Failure, self.ax.glib.Error) as error:
            detail = str(error)
            error_code = (
                error.code
                if isinstance(error, Failure)
                else "input_unavailable"
                if dispatch == "notDispatched"
                else "execution_unknown"
            )
        return build_dispatch_receipt(target, dispatch, detail, error_code)
