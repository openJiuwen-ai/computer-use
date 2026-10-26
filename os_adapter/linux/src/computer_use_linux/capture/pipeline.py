"""Capture scope/check/read/cleanup order, separate from accessibility."""

from computer_use_linux.contracts.protocol import Failure

from .passes.collect_pixels import collect_pixels
from .passes.resolve_capture_scope import resolve_capture_scope


class NativeCapturePipeline:
    def __init__(self, *, session, current, captures, finish, expire, decode_binding):
        self.session, self.current, self.captures = session, current, captures
        self.finish, self.expire, self.decode_binding = finish, expire, decode_binding

    def run(self, params, ctx):
        operation, target = resolve_capture_scope(params, self.session, self.current(), self.decode_binding)
        ctx.deadline = min(ctx.deadline, operation.deadline)
        try:
            return collect_pixels(self.captures, target, params, ctx)
        except Failure as error:
            if error.code in ("cancelled", "deadline_exceeded"):
                self.finish(operation.token)
            raise
        finally:
            self.expire()
