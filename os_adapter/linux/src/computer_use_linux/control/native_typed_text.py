"""One keyboard text recipe for the already selected native receiver.

Insert follows the live keyboard selection. Replacement selects and proves the
native Text range where available. Explicitly editable spin controls without
Text use an addressed pointer focus and full-selection shortcut, preserving
the unobservable selection range. Their unknown caret does not support insert.
No clipboard substitution, value setter or editor search belongs here.
"""

import time

from computer_use_linux.contracts.protocol import Failure, integer, require
from computer_use_linux.control.native_keyboard import NativeKeyboard
from computer_use_linux.control.native_selection import Changed
from computer_use_linux.control.native_sequence_receiver import NativeSequenceReceiver
from computer_use_linux.system.x11_pointer import PointerCallbacks


class NativeTypedText(NativeKeyboard):
    requires_editable = True

    def __init__(self, backend, store, directory, *, recipients, element_pointer):
        super().__init__(backend, store, directory, recipients=recipients)
        self.element_pointer = element_pointer

    def preflight(self, operation, *, shortcut_selection=False):
        super().preflight(operation)
        self.desktop.text_preflight(operation.intent["parameters"]["text"])
        if shortcut_selection:
            self.desktop.keycode("ctrl")
            self.desktop.keycode("a")
        if operation.intent["kind"] == "replace" and not operation.intent["parameters"]["text"]:
            self.desktop.keycode("BackSpace")

    def selection(self, obj):
        interfaces = self.target.call("Accessible", "get_interfaces", obj)
        require("Text" in interfaces, "Full replacement requires an observable native text range.", "input_unavailable")
        count = self.target.call("Text", "get_character_count", obj)
        integer(count, 0, 2147483647)
        number = self.target.call("Text", "get_n_selections", obj)
        require(
            # Exact types preserve scalar validation and identity contracts.
            # pylint: disable-next=unidiomatic-typecheck
            type(number) is int and number in (0, 1),
            "Full replacement requires one unambiguous native selection.",
            "input_unavailable",
        )
        selected = None
        if number:
            bounds = self.target.call("Text", "get_selection", obj, 0)
            start, end = getattr(bounds, "start_offset", None), getattr(bounds, "end_offset", None)
            integer(start, 0, count)
            integer(end, start, count)
            selected = start, end
        return count, selected

    def inspect(self, operation, target, within, ctx, selection=None):
        r = self.receiver(target, within, ctx, selection)
        operation.bind(target, within, self.store)
        mapped, shortcut_selection = True, False
        interfaces = r["interfaces"]
        if operation.intent["kind"] == "replace" and "Text" not in interfaces:
            # AT-SPI EDITABLE is an interaction declaration. A spin control
            # may intentionally omit its editor and all Text methods while
            # still accepting ordinary keyboard selection and text input.
            shortcut_selection = r["role"] == "spin button" and r["can_grab"] and r["editable"]
        try:
            self.preflight(operation, shortcut_selection=shortcut_selection)
        except Failure as error:
            if error.code not in ("input_unavailable", "mode_unsupported"):
                raise
            mapped = False
        supported = mapped and r["editable"] and self.supported(r, target)
        if r["role"] == "spin button" and "Text" not in interfaces:
            # FOCUSED may name a scope rather than its internal editor. An
            # insertion cannot silently click and relocate its unknown caret.
            supported = supported and operation.intent["kind"] == "replace"
        if shortcut_selection:
            supported = supported and (self.b.mode == "exclusive" or self.desktop.assist)
        selection_known = "unknown"
        pending = [] if r["enabled"] else ["enabled"]
        if operation.intent["kind"] == "replace":
            if r["obj"] is None:
                supported = False
            else:
                supported = supported and ("Text" in interfaces or shortcut_selection)
                if "Text" in interfaces:
                    try:
                        self.selection(r["obj"])
                        selection_known = "yes"
                    except (Failure, self.ax.glib.Error):
                        ctx.check()
                        pending.append("selection")
        ready = r["route"] if shortcut_selection else r["ready"]
        if not ready:
            pending.append(
                "foreground" if shortcut_selection else "receiver" if r["can_grab"] or r["route"] else "foreground"
            )
        identifier = operation.contract(
            "key_sequence",
            target,
            within,
            role=r["role"],
            supported=supported,
            typedText=True,
            replacement=operation.intent["kind"] == "replace",
            shortcutSelection=shortcut_selection,
            selection=selection,
        )
        primitive = dict(
            id=identifier,
            unit="key_sequence",
            channel="keyboard",
            supported="yes" if supported else "no",
            readiness="yes" if supported and not pending else "no",
            pending=pending,
            backgroundPreparation="yes" if r["can_grab"] else "no",
        )
        result = self.target.context(target, within, [primitive], r["enabled"], r["role"], selection=selection_known)
        result["input"].update(
            foregroundSurface=target["surface"] if r["route"] else None,
            keyboardSurface=target["surface"] if r["route"] else None,
            focusedNode=r["recipient"]["node"] if r["focused"] else None,
            windowRoute="yes" if r["route"] else "no",
        )
        return result

    def prepare(self, operation, contract, foreground, ctx, preparation):
        if not contract["shortcutSelection"]:
            return super().prepare(operation, contract, foreground, ctx, preparation)
        # Only the native window route is preparation. Clicking the addressed
        # control belongs to the replacement's one requested-effect barrier.
        r = self.guard(contract, ctx)
        if r["route"]:
            return True
        key = self.route_key(contract["target"], contract["within"], contract["role"], contract.get("selection"))
        if not foreground:
            operation.background_unavailable.add(key)
            return False
        require(
            self.b.mode == "exclusive" or self.desktop.assist,
            "Host policy does not permit foreground assistance.",
            "mode_unsupported",
        )
        self.desktop.require_idle_input()
        preparation.append("foreground")
        self.desktop.activate(r["window"], ctx)
        return self.guard(contract, ctx)["route"]

    def pointer_focus(self, contract, ctx, preparation, before_dispatch):
        """One exact element click, never a different intent or retry route."""
        pointer, gesture = self.element_pointer, None
        options = dict(button="left", count=1, direction=None, pages=1)

        def guard():
            nonlocal gesture
            require(
                self.b.mode == "exclusive" or self.desktop.assist,
                "Addressed pointer focus requires exclusive mode or host foreground-assist authorization.",
                "mode_unsupported",
            )
            r = self.guard(contract, ctx)
            require(r["route"], "The original working window is not the keyboard window.", "input_unavailable")
            target, within = self.target.selected(
                contract["target"],
                contract["within"],
                contract.get("selection"),
                ctx,
                check=True,
                require_editable=True,
            )
            read = pointer.read_target(target, within, ctx)
            points, frame, hit = pointer.query_geometry(read, ctx, kind="click", options=options)
            pointer.require_member_hit(hit, r["obj"])
            # Geometry/hit queries can run application callbacks. Preserve
            # both the addressed recipient and its last scope after them.
            r = self.guard(contract, ctx)
            require(
                r["route"],
                "The original working window lost its keyboard route during pointer preparation.",
                "input_unavailable",
            )
            self.desktop.require_hit(read[5], points[0])
            require(
                self.desktop.active() == r["window"].key and self.desktop.focus_belongs(r["window"].key),
                "The original working window lost its keyboard route during pointer preparation.",
                "input_unavailable",
            )
            if gesture is None:
                gesture = frame
            require(gesture == frame, "Element pointer geometry changed during input preparation.", "input_unavailable")
            return points

        points = guard()
        self.desktop.require_idle_input()

        def barrier():
            before_dispatch()
            preparation.append("pointerFocus")

        self.desktop.pointer_sequence(
            "click", points, ctx, **options, callbacks=PointerCallbacks(validate=guard, before_dispatch=barrier)
        )

    def guard(self, contract, ctx):
        r = super().guard(contract, ctx)
        require(r["editable"], "The selected control no longer declares editable text.", "input_unavailable")
        if contract["replacement"]:
            require(r["obj"] is not None, "Full replacement requires a specified text control.", "invalid_request")
        if contract["shortcutSelection"]:
            require(
                r["role"] == "spin button" and r["can_grab"] and "Text" not in r["interfaces"],
                "The original control no longer supports keyboard text replacement.",
                "input_unavailable",
            )
        return r

    def execute(self, operation, command, ctx):
        contract = operation.contracts.get(command["primitiveId"])
        require(
            operation.intent["kind"] in ("insert", "replace")
            and contract is not None
            and contract["unit"] == "key_sequence"
            and contract.get("typedText") is True
            and contract["supported"]
            and command["target"] == contract["target"],
            "The typed text contract expired or changed target.",
            "stale_target",
        )
        preparing = command["kind"] == "prepare_receiver"
        require(preparing or command["kind"] == "key_sequence", "Invalid typed text execution unit.")
        require(preparing or command["foreground"] is False, "Text dispatch cannot activate a window.")
        target, preparation = contract["target"], []
        dispatch, confirmation, error_code, retry = "notDispatched", "unavailable", None, None
        detail, content_started = "Keyboard text was not dispatched.", False
        text = operation.intent["parameters"]["text"]
        sequence = None

        def barrier():
            nonlocal dispatch
            operation.dispatched, dispatch = True, "unknown"

        def receiver_guard():
            if sequence is not None:
                return self.continuous_guard(contract, ctx, sequence)
            r = self.guard(contract, ctx)
            require(
                r["ready"], "The selected control or working window is not the keyboard receiver.", "input_unavailable"
            )
            return r

        def selection_sample():
            r = receiver_guard()
            count, selected = self.selection(r["obj"])
            # Selection queries can run callbacks; confirm the recipient again.
            receiver_guard()
            return count, count == 0 or selected == (0, count)

        def full_selection():
            count, full = selection_sample()
            require(
                full, "The complete control contents are no longer selected; replacement stopped.", "input_unavailable"
            )
            return count

        def wait_full_selection():
            # A successful setter confirms acceptance, not synchronous provider
            # publication. Wait only for the selected range, never replay it.
            until = min(ctx.deadline, time.monotonic() + 0.4)
            while True:
                count, full = selection_sample()
                if full:
                    return count
                require(
                    time.monotonic() < until,
                    "Native full selection did not become observable; replacement stopped.",
                    "input_unavailable",
                )
                ctx.pause(0.02)

        def guard():
            if contract["replacement"] and not contract["shortcutSelection"] and not content_started:
                full_selection()
            else:
                receiver_guard()

        def character():
            nonlocal content_started
            guard()
            content_started = True

        try:
            self.ax.begin(ctx)
            self.preflight(operation, shortcut_selection=contract["shortcutSelection"])
            if preparing:
                if self.prepare(operation, contract, command["foreground"], ctx, preparation):
                    confirmation, detail = "confirmed", "The selected receiver is ready for keyboard text."
                    if contract["shortcutSelection"]:
                        detail = (
                            "The working window route is ready; addressed pointer focus is part of the replacement."
                        )
                else:
                    error_code, retry = "input_unavailable", "preparationUnavailable"
                    detail = "Preparation did not establish the selected receiver; no requested text was sent."
            else:
                if contract["shortcutSelection"]:
                    self.pointer_focus(contract, ctx, preparation, barrier)
                r = receiver_guard()
                self.desktop.require_idle_input()
                sequence = NativeSequenceReceiver(
                    self.b,
                    r["window"],
                    ctx,
                    recipients=self.recipients,
                    obj=r["obj"],
                    pointer_focused=contract["shortcutSelection"],
                )
                if contract["shortcutSelection"]:
                    # This is part of the requested replacement, not a probe
                    # that policy may repeat. Once selection is sent, all
                    # later failures retain possible effects and stop replay.
                    self.desktop.press_key("ctrl+a", ctx, validate=receiver_guard, before_dispatch=barrier)
                    preparation.append("keyboardSelection")
                    if text:
                        self.desktop.type_text(
                            text, ctx, validate=guard, before_dispatch=barrier, before_character=character
                        )
                    else:
                        self.desktop.press_key("BackSpace", ctx, validate=guard, before_dispatch=barrier)
                elif contract["replacement"]:
                    count, selected = self.selection(r["obj"])
                    if count and selected != (0, count):
                        r = receiver_guard()
                        # Selecting all is part of the requested replacement,
                        # not a preparation that may be repeated by policy.
                        ctx.sent()
                        barrier()
                        preparation.append("nativeSelection")
                        if selected is None:
                            accepted = self.target.call("Text", "add_selection", r["obj"], 0, count)
                        else:
                            accepted = self.target.call("Text", "set_selection", r["obj"], 0, 0, count)
                        require(
                            accepted is True,
                            "Native full selection was not confirmed; replacement stopped.",
                            "execution_unknown",
                        )
                        wait_full_selection()
                    count = full_selection()
                    if not text and count:
                        self.desktop.press_key("BackSpace", ctx, validate=guard, before_dispatch=barrier)
                    elif text:
                        self.desktop.type_text(
                            text, ctx, validate=guard, before_dispatch=barrier, before_character=character
                        )
                    else:
                        confirmation = "confirmed"
                elif text:
                    self.desktop.type_text(
                        text, ctx, validate=guard, before_dispatch=barrier, before_character=character
                    )
                else:
                    confirmation = "confirmed"
                if operation.dispatched:
                    dispatch, detail = "dispatched", "Keyboard text recipe sent; read to verify its effect."
                    if contract["shortcutSelection"]:
                        detail += (
                            " Full-selection shortcut sent; the provider exposes no observable text selection range."
                        )
                else:
                    detail = "The requested text operation needed no input."
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
            operation.cleanup, retry = cleanup, None
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
