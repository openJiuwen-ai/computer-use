"""Bound AT-SPI receiver preparation and X11 key sequences.

Python selects the route and authorizes preparation. The driver rechecks the
actual native recipient at dispatch; an AT-SPI FOCUSED flag alone never routes
an XTEST event to a window. Preparation cannot replay requested input.
"""

from copy import deepcopy

from computer_use_linux.contracts.protocol import Failure, fields, key_parts, require
from computer_use_linux.control.native_selection import Changed
from computer_use_linux.control.native_sequence_receiver import NativeSequenceReceiver
from computer_use_linux.control.native_target import NativeTarget


def keyboard_intent(value):
    fields(value, ("kind", "parameters"))
    if value["kind"] == "focus":
        fields(value["parameters"], ())
    elif value["kind"] == "key":
        p = fields(value["parameters"], ("key",))
        key_parts(p["key"])
    else:
        raise Failure("Unsupported native keyboard intent.", "mode_unsupported")
    return deepcopy(value)


class NativeKeyboard:
    requires_editable = False

    def __init__(self, backend, store, directory, *, recipients):
        self.b, self.ax, self.store, self.directory = backend, backend.ax, store, directory
        self.desktop = backend.desktop
        self.recipients = recipients
        self.target = NativeTarget(backend, store, directory)

    def prepare(self, operation, contract, foreground, ctx, preparation):
        from .keyboard.pipeline import NativePreparePipeline

        return NativePreparePipeline(self.guard, self.route_key, self.desktop, self.target, self.b.mode).run(
            operation, contract, foreground, ctx, preparation
        )

    def preflight(self, operation):
        require(
            getattr(self.desktop, "can_input", False),
            "This desktop has no implemented native keyboard route.",
            "mode_unsupported",
        )
        if operation.intent["kind"] == "key":
            # Mapping failures must not move focus or dispatch part of a chord.
            for name in key_parts(operation.intent["parameters"]["key"]):
                self.desktop.keycode(name)

    def receiver(self, target, within, ctx, selection=None, *, check=False):
        target, within = self.target.selected(
            target, within, selection, ctx, check=check, require_editable=self.requires_editable
        )
        if target["node"] is None:
            selected, native = self.target.window(target, ctx)
            obj, role, enabled, focused, can_grab = None, None, True, True, False
            interfaces = ()
        else:
            obj, states, interfaces, role, enabled = self.target.read(target, within, ctx)
            selected = self.directory.surfaces[target["surface"]]
            native = self.desktop.validate(selected)
            focused = states.contains(self.ax.A.StateType.FOCUSED)
            can_grab = "Component" in interfaces and states.contains(self.ax.A.StateType.FOCUSABLE)
        route = bool(native.mapped and self.desktop.active() == native.key and self.desktop.focus_belongs(native.key))
        if obj is not None:
            # Parent/interface/geometry queries can run application callbacks.
            # Refresh the addressed recipient after them, not a prior snapshot.
            states = self.target.state(obj)
            focused = states.contains(self.ax.A.StateType.FOCUSED)
            enabled = any(states.contains(getattr(self.ax.A.StateType, key)) for key in ("ENABLED", "SENSITIVE"))
            can_grab = "Component" in interfaces and states.contains(self.ax.A.StateType.FOCUSABLE)
            self.target.validate_scope(target, within, ctx)
            route = bool(route and self.desktop.active() == native.key and self.desktop.focus_belongs(native.key))
        editable = obj is None or (states.contains(self.ax.A.StateType.EDITABLE) and not self.target.read_only(states))
        return dict(
            recipient=target,
            selected=selected,
            window=native,
            obj=obj,
            role=role,
            interfaces=interfaces,
            enabled=enabled,
            editable=editable,
            focused=focused,
            can_grab=can_grab,
            route=route,
            ready=route and focused and enabled,
        )

    @staticmethod
    def route_key(target, within, role, selection=None):
        recipient = None if selection is None else selection.signature
        return tuple(sorted(target.items())), tuple(within), role, recipient, "keyboard"

    @staticmethod
    def unit(operation):
        return "prepare_receiver" if operation.intent["kind"] == "focus" else "key_sequence"

    @staticmethod
    def supported(receiver, target):
        return target["node"] is None or receiver["can_grab"] or receiver["focused"]

    def inspect(self, operation, target, within, ctx, selection=None):
        self.preflight(operation)
        r = self.receiver(target, within, ctx, selection)
        operation.bind(target, within, self.store)
        unit = self.unit(operation)
        supported = self.supported(r, target)
        pending = [] if r["enabled"] else ["enabled"]
        if not r["ready"]:
            pending.append("receiver" if r["can_grab"] or r["route"] else "foreground")
        identifier = operation.contract(unit, target, within, role=r["role"], supported=supported, selection=selection)
        primitive = dict(
            id=identifier,
            unit=unit,
            channel="keyboard",
            supported="yes" if supported else "no",
            readiness="yes" if r["ready"] else "no",
            pending=pending,
            backgroundPreparation="yes" if r["can_grab"] else "no",
        )
        result = self.target.context(target, within, [primitive], r["enabled"], r["role"])
        result["input"].update(
            foregroundSurface=target["surface"] if r["route"] else None,
            keyboardSurface=target["surface"] if r["route"] else None,
            focusedNode=r["recipient"]["node"] if r["focused"] else None,
            windowRoute="yes" if r["route"] else "no",
            editor=None if selection is None else selection.recipient["node"],
            editorOwner=None if selection is None else target["node"],
        )
        return result

    def guard(self, contract, ctx):
        r = self.receiver(contract["target"], contract["within"], ctx, contract.get("selection"), check=True)
        require(
            r["role"] == contract["role"] and r["enabled"],
            "The original keyboard target role or enabled state changed.",
            "input_unavailable",
        )
        return r

    def continuous_guard(self, contract, ctx, sequence):
        try:
            receiver = self.guard(contract, ctx)
        except Changed as error:
            # Once this primitive has fixed the actual receiver, semantic
            # replanning must not replace it under the same requested input.
            raise Failure(
                "The bound keyboard recipient changed during continuous input: " + str(error), "input_unavailable"
            ) from error
        require(
            receiver["ready"],
            "The selected control or working window is not the keyboard receiver.",
            "input_unavailable",
        )
        sequence.validate()
        return receiver

    def execute(self, operation, command, ctx):
        contract = operation.contracts.get(command["primitiveId"])
        expected = self.unit(operation)
        require(
            contract is not None
            and contract["unit"] == expected
            and contract["supported"]
            and command["target"] == contract["target"],
            "The native keyboard contract expired or changed target.",
            "stale_target",
        )
        preparing = command["kind"] == "prepare_receiver"
        require(preparing or command["kind"] == expected == "key_sequence", "Invalid keyboard execution unit.")
        require(preparing or command["foreground"] is False, "Key dispatch cannot activate a window.")
        target, preparation = contract["target"], []
        dispatch, confirmation, error_code, retry = "notDispatched", "unavailable", None, None
        detail = "Keyboard input was not dispatched."
        try:
            self.ax.begin(ctx)
            self.preflight(operation)
            if preparing:
                if self.prepare(operation, contract, command["foreground"], ctx, preparation):
                    confirmation, detail = "confirmed", "The selected receiver is ready for keyboard input."
                else:
                    error_code, retry = "input_unavailable", "preparationUnavailable"
                    detail = "Preparation did not establish the selected keyboard receiver; no requested key was sent."
            else:
                receiver = self.guard(contract, ctx)
                require(
                    receiver["ready"],
                    "The selected control or working window is not the keyboard receiver.",
                    "input_unavailable",
                )
                sequence = NativeSequenceReceiver(
                    self.b, receiver["window"], ctx, recipients=self.recipients, obj=receiver["obj"]
                )

                def guard():
                    self.continuous_guard(contract, ctx, sequence)

                def barrier():
                    nonlocal dispatch
                    operation.dispatched, dispatch = True, "unknown"

                self.desktop.press_key(
                    operation.intent["parameters"]["key"], ctx, validate=guard, before_dispatch=barrier
                )
                dispatch, detail = "dispatched", "Key sequence sent; read to verify its effect."
                # Return may legitimately close the window. Release is independent
                # of target lifetime, request cancellation, and subsequent focus.
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
        cleanup = "failed" if self.desktop.keys or self.desktop.buttons else "complete"
        if cleanup != "complete":
            operation.cleanup = cleanup
            retry = None
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
