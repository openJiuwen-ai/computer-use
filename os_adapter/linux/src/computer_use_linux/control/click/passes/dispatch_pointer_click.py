"""One fixed XTest train, with native final guards and owned-input release."""

from computer_use_linux.system.x11_pointer import PointerCallbacks


def dispatch_pointer_click(operation, plan, ledger, ctx, diagnostics):
    unit, contract = plan.unit, plan.contract
    with diagnostics.stage("guardAndFreezePointer"):
        points, _, _ = unit.guard(operation, contract, ctx)
        desktop_target = contract["target"]["surface"] is None
        expected = unit.captures.pointer_destination(contract["target"], points[0], ctx) if desktop_target else None

    def barrier():
        operation.dispatched = True

    with diagnostics.stage("xtestSequenceAndOwnedRelease"):
        unit.desktop.pointer_sequence(
            "click",
            points,
            ctx,
            **unit.options(operation.intent),
            callbacks=PointerCallbacks(
                before_dispatch=barrier,
                validate=lambda: unit.guard(operation, contract, ctx),
                event_progress=ledger.event,
            ),
        )
        ledger.requested_complete = True
    with diagnostics.stage("postDispatchState"):
        ctx.check()
        if desktop_target:
            unit.captures.input_finished(contract["target"], ctx, expected=expected, pointer=True)


CONTRACT = {
    "input": ("operation", "plan", "ledger", "ctx", "diagnostics"),
    "output": "completed requested train",
    "effects": ("QUERY", "DISPATCH", "RESOURCE"),
    "borrows": ("operation", "plan", "ledger", "ctx", "diagnostics"),
    "writes": ("operation input journal", "owned pointer state"),
    "failure_boundary": "never replay; the sequencer releases owned input even after target loss",
}
