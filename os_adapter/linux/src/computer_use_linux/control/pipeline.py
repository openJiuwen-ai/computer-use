"""Native write entry: resolve -> select -> one deduplicated unit -> expire."""

from .passes.resolve_native_step import resolve_native_step
from .passes.select_native_unit import select_native_unit


class NativeInputPipeline:
    def __init__(self, *, session, current, finished, units, expire, decode_binding, click=None):
        self.session, self.current, self.finished = session, current, finished
        self.units, self.expire, self.decode_binding = units, expire, decode_binding
        self.click = click

    def run(self, params, ctx):
        step = resolve_native_step(params, self.session, self.current(), self.finished, self.decode_binding)
        ctx.deadline = min(ctx.deadline, step.operation.deadline)
        try:
            if step.operation.click_contract == "v1":
                return step.operation.step(
                    step.step, step.command, lambda: self.click.run(step.operation, step.command, ctx)
                )
            unit = select_native_unit(step.operation, step.command, self.units)
            # Runtime's original step Owner is the resource scope around the
            # explicitly selected child unit. It may return a retained receipt
            # before entering that child, including unknown/partial outcomes.
            return step.operation.step(step.step, step.command, lambda: unit.execute(step.operation, step.command, ctx))
        finally:
            self.expire()
