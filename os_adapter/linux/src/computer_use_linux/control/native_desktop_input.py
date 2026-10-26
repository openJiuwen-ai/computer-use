"""Exclusive keyboard input bound to an observed desktop's native window.

No activation, receiver preparation or retry is performed. Existing optional
AX information can pin the receiver; it is never required to start a visual
route. X11 sequences own key mapping, literal text planning and paired release.
"""

from computer_use_linux.contracts.protocol import Failure, key_parts, require
from computer_use_linux.control.native_sequence_receiver import NativeSequenceReceiver
from computer_use_linux.control.native_target import NativeTarget


class NativeDesktopInput:
    def __init__(self, backend, store, captures, *, recipients):
        self.b, self.store, self.captures = backend, store, captures
        self.desktop = backend.desktop
        self.recipients = recipients

    def guard(self, operation, target, ctx):
        ctx.check()
        require(self.b.mode == "exclusive", "Desktop-wide keyboard input requires exclusive mode.", "mode_unsupported")
        require(
            getattr(self.desktop, "can_input", False), "This desktop has no native keyboard route.", "mode_unsupported"
        )
        require(
            operation.intent["kind"] in ("key", "insert")
            and target["capture"] is not None
            and all(target[k] is None for k in ("instance", "rootWindow", "surface", "node")),
            "Desktop keyboard input requires the original captured desktop scope.",
        )
        self.b.ensure_unlocked()
        return self.captures.keyboard_destination(target, ctx)

    def preflight(self, operation):
        kind, parameters = operation.intent["kind"], operation.intent["parameters"]
        if kind == "insert":
            self.desktop.text_preflight(parameters["text"])
        else:
            for name in key_parts(parameters["key"]):
                self.desktop.keycode(name)

    def inspect(self, operation, target, within, ctx):
        require(not within, "Desktop input cannot select an accessibility ancestor.")
        self.guard(operation, target, ctx)
        self.preflight(operation)
        self.guard(operation, target, ctx)
        operation.bind(target, (), self.store)
        identifier = operation.contract("key_sequence", target, (), desktopKeyboard=True)
        primitive = dict(
            id=identifier,
            unit="key_sequence",
            channel="keyboard",
            supported="yes",
            readiness="yes",
            pending=[],
            backgroundPreparation="no",
        )
        result = NativeTarget.context(target, (), [primitive], True, None)
        result.update(targetLabel="Observed desktop keyboard window", enabled="unknown")
        result["input"]["windowRoute"] = "yes"
        return result

    def execute(self, operation, command, ctx):
        contract = operation.contracts.get(command["primitiveId"])
        require(
            contract is not None
            and contract.get("desktopKeyboard") is True
            and contract["unit"] == command["kind"] == "key_sequence"
            and command["target"] == contract["target"]
            and command["foreground"] is False,
            "The desktop keyboard contract expired or changed.",
            "stale_target",
        )
        target = contract["target"]
        dispatch, error_code, cleanup, confirmation = "notDispatched", None, "complete", "unavailable"
        detail = "Desktop keyboard input was not dispatched."
        try:
            destination = self.guard(operation, target, ctx)
            self.preflight(operation)  # Entire payload, before any key event.
            self.guard(operation, target, ctx)

            def barrier():
                nonlocal dispatch
                operation.dispatched, dispatch = True, "unknown"

            def sent():
                nonlocal dispatch, detail
                dispatch, detail = "dispatched", "Desktop keyboard input sent; read to verify its effect."

            kind, parameters = operation.intent["kind"], operation.intent["parameters"]
            if kind != "key" and parameters["text"] == "":
                confirmation, detail = "confirmed", "Empty desktop text; no input was needed."
            else:
                window = self.desktop.validate(destination.instance)
                sequence = NativeSequenceReceiver(self.b, window, ctx, recipients=self.recipients)

                def guard():
                    self.guard(operation, target, ctx)
                    sequence.validate()

                if kind == "insert":
                    self.desktop.type_text(parameters["text"], ctx, validate=guard, before_dispatch=barrier)
                else:
                    self.desktop.press_key(parameters["key"], ctx, validate=guard, before_dispatch=barrier)
                sent()
            ctx.check()
            if operation.dispatched:
                self.captures.input_finished(target, ctx)
        except Exception as error:
            detail = str(error)
            error_code = (
                error.code
                if isinstance(error, Failure)
                else "input_unavailable"
                if dispatch == "notDispatched"
                else "execution_unknown"
            )
        if self.desktop.keys or self.desktop.buttons:
            cleanup = "failed"
        if cleanup != "complete":
            operation.cleanup = cleanup
            error_code = error_code or "execution_unknown"
            detail += " Held-input cleanup could not be confirmed; reconnect before more input."
        if error_code is not None or cleanup != "complete":
            self.captures.input_failed()
        return dict(
            target=dict(target),
            dispatch=dispatch,
            detail=detail,
            preparation=[],
            confirmation=confirmation,
            cleanup=cleanup,
            errorCode=error_code,
            retryReason=None,
            context=None,
        )
