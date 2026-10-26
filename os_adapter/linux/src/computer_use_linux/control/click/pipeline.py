"""One selected native click: preparation, final boundary and dispatch."""

from computer_use_linux.contracts.protocol import Failure, require
from computer_use_linux.control.action.pipeline import NativeActionPipeline

from .diagnostics import ClickDiagnostics
from .model import ClickAttemptLedger
from .passes.build_click_receipt import build_click_receipt
from .passes.dispatch_pointer_click import dispatch_pointer_click
from .passes.prepare_pointer_exposure import prepare_pointer_exposure
from .passes.resolve_click_plan import resolve_click_plan


class NativeClickPipeline:
    def __init__(self, action, pointer, element_pointer):
        self.action, self.pointer, self.element_pointer = action, pointer, element_pointer

    def run(self, operation, command, ctx):
        diagnostics = ClickDiagnostics(operation)
        plan = receipt = None
        try:
            with diagnostics.stage("resolveClickPlan"):
                plan = resolve_click_plan(operation, command, self.action, self.pointer, self.element_pointer)
            ledger = ClickAttemptLedger(
                expected_requests=(
                    1 if plan.delivery == "object_action" else 1 + 2 * operation.intent["parameters"].get("count", 1)
                )
            )
            operation.click_attempt = ledger
            result = dict(
                target=dict(plan.contract["target"]),
                dispatch="notDispatched",
                detail="Click input was not dispatched.",
                preparation=[],
                confirmation="unavailable",
                cleanup="complete",
                errorCode=None,
                retryReason=None,
                context=None,
            )
            try:
                if plan.delivery == "object_action":
                    action = self.action
                    result = NativeActionPipeline(action.ax, action.target, action.facts).run(
                        operation, command, ctx, ledger, diagnostics
                    )
                else:
                    require(
                        plan.delivery == "desktop_pointer" and plan.contract.get("supported"),
                        "The selected XTest pointer mechanism is unavailable.",
                        "input_unavailable",
                    )
                    with diagnostics.stage("prepareAndObserveExposure"):
                        ready = prepare_pointer_exposure(operation, plan, ledger, ctx)
                    if ready:
                        dispatch_pointer_click(operation, plan, ledger, ctx, diagnostics)
                        result["detail"] = "Pointer sequence sent; read to verify its effect."
                    else:
                        result.update(
                            errorCode="input_unavailable",
                            retryReason="preparationUnavailable",
                            detail="The selected point is covered; no requested pointer input was sent.",
                        )
            except Exception as error:
                result.update(
                    detail=str(error),
                    errorCode=(
                        error.code
                        if isinstance(error, Failure)
                        else "input_unavailable"
                        if ledger.dispatch == "notDispatched"
                        else "execution_unknown"
                    ),
                )
            with diagnostics.stage("collectCleanupOutcome"):
                if plan.delivery != "object_action":
                    desktop = plan.unit.desktop
                    if desktop.keys or desktop.buttons:
                        result["cleanup"] = operation.cleanup = "failed"
                    if plan.contract["target"]["surface"] is None and (
                        result["errorCode"] is not None or result["cleanup"] != "complete"
                    ):
                        plan.unit.captures.input_failed()
            with diagnostics.stage("buildClickReceipt"):
                receipt = build_click_receipt(operation, plan, ledger, result)
            return receipt
        finally:
            diagnostics.publish(plan, receipt)
