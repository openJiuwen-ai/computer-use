"""One native text edit; guarded deletion/insertion retain partial receipts."""

from computer_use_linux.contracts.protocol import Failure, integer, require
from computer_use_linux.control.native_selection import Changed

from .passes.check_dispatch_conditions import check_dispatch_conditions
from .passes.mark_dispatch_in_flight import mark_dispatch_in_flight
from .passes.resolve_mutation_recipient import resolve_mutation_recipient
from .passes.resolve_native_target import resolve_native_target
from .passes.send_native_unit import send_native_unit


class NativeTextPipeline:
    def __init__(self, ax, target, guard, selection, native_call):
        self.ax, self.target, self.guard, self.selection, self.call = ax, target, guard, selection, native_call

    def run(self, operation, command, ctx):
        contract = resolve_native_target(operation, command)
        target, kind = contract["target"], contract["kind"]
        retry = None
        dispatch, detail, error_code = "notDispatched", "Native text operation was not dispatched.", None

        def check():
            return check_dispatch_conditions(contract, ctx, self.guard, self.ax)

        def mutate(method, obj, *args):
            nonlocal dispatch
            resolve_mutation_recipient(contract, ctx, self.target)
            dispatch = mark_dispatch_in_flight(operation, ctx, dispatch)
            send_native_unit(method, obj, args, self.call)

        try:
            self.ax.begin(ctx)
            obj = check()
            text = operation.intent["parameters"]["text"]
            if kind == "replace":
                mutate("set_text_contents", obj, text)
            elif text:
                start, end = self.selection(obj)
                obj = check()
                if self.selection(obj) != (start, end):
                    raise Failure("The insertion selection changed before text dispatch.", "input_unavailable")
                if start != end:
                    mutate("delete_text", obj, start, end)
                    dispatch, ctx.effect = "partial", "partial"
                    obj = check()
                    caret, selection_end = self.selection(obj)
                    if caret != selection_end:
                        raise Failure(
                            "A new selection appeared after deletion; insertion stopped.", "input_unavailable"
                        )
                    # Deletion may reset a toolkit caret; insertion stays at
                    # the start of the originally selected range.
                    count = self.call("Text", "get_character_count", obj)
                    integer(count, 0, 2147483647)
                    require(
                        start <= count,
                        "The original insertion offset is no longer present after deletion.",
                        "input_unavailable",
                    )
                mutate("insert_text", obj, start, text, contract["codec"].insertion_length(text))
            else:
                return dict(
                    target=dict(target),
                    dispatch="notDispatched",
                    detail="Empty insertion requested; no text sent.",
                    preparation=[],
                    confirmation="confirmed",
                    cleanup="complete",
                    errorCode=None,
                    retryReason=None,
                    context=None,
                )
            dispatch = "dispatched"
            detail = (
                "Native text "
                + ("replacement" if kind == "replace" else "insertion")
                + " accepted; read the control to verify the result."
            )
            ctx.check()
        except (Failure, self.ax.glib.Error) as error:
            if isinstance(error, Changed) and dispatch == "notDispatched":
                retry = "preconditionChanged"
            detail = str(error)
            error_code = (
                error.code
                if isinstance(error, Failure)
                else "input_unavailable"
                if dispatch == "notDispatched"
                else "execution_unknown"
            )
        return dict(
            target=dict(target),
            dispatch=dispatch,
            detail=detail,
            preparation=[],
            confirmation="unavailable",
            cleanup="complete",
            errorCode=error_code,
            retryReason=retry,
            context=None,
        )
