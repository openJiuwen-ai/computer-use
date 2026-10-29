# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A bound keyboard paste: receiver preparation, clipboard lease, one shortcut.

The Python engine selects preparation and authorization. No clipboard is
changed during preparation. After the first possible key event, this unit
never substitutes text insertion or retries the paste.
"""

from copy import deepcopy

from computer_use_linux.contracts.protocol import Failure, fields, require, string
from computer_use_linux.control.native_keyboard import NativeKeyboard
from computer_use_linux.control.native_selection import Changed
from computer_use_linux.control.native_sequence_receiver import NativeSequenceReceiver


def paste_intent(value):
    fields(value, ("kind", "parameters"))
    require(value["kind"] == "paste", "Invalid paste intent.")
    text = string(fields(value["parameters"], ("text",))["text"], empty=True)
    require(
        len(text.encode("utf-8")) <= 65536,
        "X11 clipboard paste supports at most 64 KiB per operation.",
        "input_unavailable",
    )
    require("\x00" not in text, "Clipboard text cannot contain NUL.", "invalid_request")
    return deepcopy(value)


class NativePaste(NativeKeyboard):
    requires_editable = True

    @staticmethod
    def unit(operation):
        return "clipboard_paste"

    def preflight(self, operation):
        super().preflight(operation)
        # Validate the complete chord before any focus or clipboard mutation.
        for name in ("ctrl", "v"):
            self.desktop.keycode(name)

    @staticmethod
    def supported(receiver, target):
        return receiver["editable"] and NativeKeyboard.supported(receiver, target)

    def guard(self, contract, ctx):
        receiver = super().guard(contract, ctx)
        require(
            receiver["editable"],
            "The addressed control no longer declares editable text; paste was not sent.",
            "input_unavailable",
        )
        return receiver

    def execute(self, operation, command, ctx):
        contract = operation.contracts.get(command["primitiveId"])
        require(
            contract is not None
            and contract["unit"] == "clipboard_paste"
            and contract["supported"]
            and command["target"] == contract["target"],
            "The native paste contract expired or changed target.",
            "stale_target",
        )
        preparing = command["kind"] == "prepare_receiver"
        require(preparing or command["kind"] == "clipboard_paste", "Invalid paste execution unit.")
        require(preparing or command["foreground"] is False, "Paste dispatch cannot activate a window.")
        target, preparation = contract["target"], []
        dispatch, confirmation, cleanup = "notDispatched", "unavailable", "complete"
        error_code, retry = None, None
        detail = "Paste was not dispatched."
        try:
            self.ax.begin(ctx)
            self.preflight(operation)
            if operation.intent["parameters"]["text"] == "":
                self.guard(contract, ctx)
                confirmation, detail = "confirmed", "Empty paste; no input or preparation was needed."
            elif preparing:
                if self.prepare(operation, contract, command["foreground"], ctx, preparation):
                    confirmation, detail = "confirmed", "The selected receiver is ready for paste."
                else:
                    error_code, retry = "input_unavailable", "preparationUnavailable"
                    detail = "Preparation did not establish the selected receiver; no paste was sent."
            else:
                sequence = None

                def receiver_guard():
                    if sequence is not None:
                        return self.continuous_guard(contract, ctx, sequence)
                    receiver = self.guard(contract, ctx)
                    require(
                        receiver["ready"],
                        "The selected control or working window is not the keyboard receiver.",
                        "input_unavailable",
                    )
                    return receiver

                receiver = receiver_guard()
                self.desktop.require_idle_input()
                sequence = NativeSequenceReceiver(
                    self.b, receiver["window"], ctx, recipients=self.recipients, obj=receiver["obj"]
                )
                clipboard = self.b.get_clipboard()
                consumer = self.desktop.clipboard_consumer(receiver["selected"])

                def send(clipboard_guard):
                    nonlocal detail, dispatch

                    def guard():
                        receiver_guard()
                        clipboard_guard()

                    def barrier():
                        nonlocal dispatch
                        operation.dispatched, dispatch = True, "unknown"

                    self.desktop.press_key("ctrl+v", ctx, validate=guard, before_dispatch=barrier)
                    dispatch, detail = "dispatched", "Paste shortcut sent; read to verify its effect."

                receipt = clipboard.paste(operation.intent["parameters"]["text"], ctx, send, consumer)
                cleanup = receipt["cleanup"]
                if receipt["prepared"]:
                    preparation.append("clipboard")
                if receipt["error"] is not None:
                    raise receipt["error"]
                if receipt["transferred"]:
                    detail = (
                        "Paste shortcut sent and clipboard data transferred to the target process; "
                        "read to verify its "
                        "effect."
                    )
                # A process consuming the clipboard is not proof of a particular
                # control value. Business confirmation remains unavailable.
                ctx.check()
        except Exception as error:
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
        if self.desktop.keys or self.desktop.buttons:
            cleanup = "failed"
        if cleanup != "complete":
            operation.cleanup = cleanup
            retry = None
            error_code = error_code or "execution_unknown"
            detail += " Clipboard or held-input cleanup could not be confirmed; reconnect before more input."
        return dict(
            target=dict(target),
            dispatch=dispatch,
            detail=detail,
            preparation=preparation,
            confirmation=confirmation,
            cleanup=cleanup,
            errorCode=error_code,
            retryReason=retry,
            context=None,
        )
