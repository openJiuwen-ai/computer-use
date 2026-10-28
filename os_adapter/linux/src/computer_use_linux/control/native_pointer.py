"""A pointer sequence bound to original pixels, independent of accessibility.

Python chooses preparation versus dispatch. Native code proves window identity,
image geometry and the actual hit route, then owns one continuous event sequence.
"""

from copy import deepcopy
from typing import Any, NamedTuple

from computer_use_linux.contracts.protocol import Failure, fields, finite, integer, require
from computer_use_linux.control.native_target import NativeTarget


def pointer_intent(value, *, element=False):
    fields(value, ("kind", "parameters"))
    kind = value["kind"]
    if kind == "click":
        p = fields(value["parameters"], () if element else ("x", "y"), ("button", "count"))
        require(p.get("button", "left") in ("left", "middle", "right"), "Invalid click button.")
        integer(p.get("count", 1), 1, 2)
        keys = () if element else ("x", "y")
    elif kind == "scroll":
        p = fields(value["parameters"], ("direction", "pages") if element else ("x", "y", "direction", "pages"))
        require(p["direction"] in ("up", "down", "left", "right"), "Invalid scroll direction.")
        require(0 < finite(p["pages"]) <= 100, "Scroll amount must be positive and at most 100 pages.")
        keys = () if element else ("x", "y")
    elif kind == "drag" and not element:
        p = fields(value["parameters"], ("fromX", "fromY", "toX", "toY"))
        keys = ("fromX", "fromY", "toX", "toY")
    else:
        raise Failure("This pointer input intent is not implemented.", "mode_unsupported")
    for key in keys:
        finite(p[key])
    return deepcopy(value)


class PointerRouteState(NamedTuple):
    """Mapped gesture points and the window's current pointer admission."""

    points: tuple[tuple[float, float], ...]
    selected: Any
    native: Any
    ready: bool


class NativePointer:
    def __init__(self, backend, store, directory, captures):
        self.b, self.store, self.directory, self.captures = backend, store, directory, captures
        self.desktop, self.target = backend.desktop, NativeTarget(backend, store, directory)

    @staticmethod
    def options(intent):
        p = intent["parameters"]
        return dict(
            button=p.get("button", "left"),
            count=p.get("count", 1),
            direction=p.get("direction"),
            pages=p.get("pages", 1),
        )

    def image(self, target, ctx, *, during_drag=False):
        ctx.check()
        self.b.ensure_unlocked()
        image = self.captures.input(target)
        if target["surface"] is None:
            require(
                self.b.mode == "exclusive", "Desktop-wide pointer input requires exclusive mode.", "mode_unsupported"
            )
            return image, None, None
        if during_drag:
            # The server's implicit grab routes an in-progress drag. A newly
            # opened child or changed widget layout is not another recipient.
            self.directory.validate(target, ctx)
            selected = self.directory.surfaces[target["surface"]]
            native = self.desktop.validate(selected)
        else:
            selected, native = self.target.window(target, ctx)
        require(native.mapped, "The captured window is not visible.", "input_unavailable")
        return image, selected, native

    def geometry(self, operation, target, ctx):
        require(
            getattr(self.desktop, "can_input", False),
            "This desktop has no native pointer event route.",
            "mode_unsupported",
        )
        image, selected, native = self.image(target, ctx)
        p = operation.intent["parameters"]
        pairs = (
            ((p["fromX"], p["fromY"]), (p["toX"], p["toY"]))
            if operation.intent["kind"] == "drag"
            else ((p["x"], p["y"]),)
        )
        points = tuple(self.desktop.point(image.capture, x, y) for x, y in pairs)
        self.desktop.pointer_preflight(operation.intent["kind"], points, **self.options(operation.intent))
        return points, selected, native

    def route(self, operation, target, ctx) -> PointerRouteState:
        points, selected, native = self.geometry(operation, target, ctx)
        available = True
        if selected is not None:
            try:
                # Only the first point receives a new press. Drag endpoints can
                # be covered while the server routes to the implicit grab owner.
                self.desktop.require_hit(selected, points[0])
            except Failure as error:
                if error.code not in ("stale_target", "input_unavailable"):
                    raise
                available = False
        return PointerRouteState(points, selected, native, available)

    def inspect(self, operation, target, within, ctx):
        require(not within and target["node"] is None, "Image input cannot select an accessibility ancestor.")
        points, selected, native, ready = self.route(operation, target, ctx)
        operation.bind(target, within, self.store)
        supported = ready or native is not None and not native.override_redirect
        identifier = operation.contract("pointer_sequence", target, (), points=points, supported=supported)
        primitive = dict(
            id=identifier,
            unit="pointer_sequence",
            channel="pointer",
            supported="yes" if supported else "no",
            readiness="yes" if ready else "no",
            pending=[] if ready else ["foreground"],
            backgroundPreparation="no",
        )
        result = self.target.context(target, (), [primitive], True, None)
        result.update(targetLabel="Captured window" if selected else "Captured desktop", enabled="unknown")
        result["input"].update(geometryCurrent="yes", pointerRoute="yes" if ready else "no")
        return result

    def guard(self, operation, contract, ctx, *, during_drag=False):
        if during_drag:
            _, selected, native = self.image(contract["target"], ctx, during_drag=True)
            points = contract["points"]
            # Moving/resizing this window can be the requested drag's effect.
            # Continue its original absolute trajectory under the native grab;
            # never recompute it from the now changed image geometry.
            self.desktop.pointer_preflight(operation.intent["kind"], points, **self.options(operation.intent))
        else:
            points, selected, native = self.geometry(operation, contract["target"], ctx)
            require(points == contract["points"], "The original image coordinates changed.", "stale_target")
        if selected is not None and not during_drag:
            self.desktop.require_hit(selected, points[0])
        return points, selected, native

    def click_state(self, operation, contract, ctx) -> PointerRouteState:
        points, selected, native, ready = self.route(operation, contract["target"], ctx)
        require(points == contract["points"], "The original image coordinates changed.", "stale_target")
        return PointerRouteState(points, selected, native, ready)

    def prepare(self, operation, contract, foreground, ctx, preparation):
        points, selected, native, ready = self.route(operation, contract["target"], ctx)
        require(points == contract["points"], "The original image coordinates changed.", "stale_target")
        if ready:
            return True
        if not foreground:
            return False  # No addressed X11 operation uncovers an obscured pixel.
        require(
            self.b.mode == "exclusive" or self.desktop.assist,
            "Host policy does not permit foreground assistance.",
            "mode_unsupported",
        )
        require(
            native is not None and not native.override_redirect,
            "An unmanaged popup cannot be activated to repair a pointer route.",
            "input_unavailable",
        )
        self.desktop.require_idle_input()
        preparation.append("foreground")
        self.desktop.activate(native, ctx)
        self.guard(operation, contract, ctx)
        return True

    def execute(self, operation, command, ctx):
        contract = operation.contracts.get(command["primitiveId"])
        require(
            contract is not None
            and contract["unit"] == "pointer_sequence"
            and contract["supported"]
            and command["target"] == contract["target"],
            "The native pointer contract expired or changed target.",
            "stale_target",
        )
        preparing = command["kind"] == "prepare_receiver"
        require(preparing or command["kind"] == "pointer_sequence", "Invalid pointer execution unit.")
        require(preparing or command["foreground"] is False, "Pointer dispatch cannot activate a window.")
        dispatch, confirmation, preparation = "notDispatched", "unavailable", []
        detail, error_code, retry = "Pointer input was not dispatched.", None, None
        desktop_target = contract["target"]["surface"] is None
        expected_keyboard = None
        try:
            if preparing:
                if self.prepare(operation, contract, command["foreground"], ctx, preparation):
                    confirmation, detail = "confirmed", "The selected pointer target is reachable."
                else:
                    error_code, retry = "input_unavailable", "preparationUnavailable"
                    detail = "The selected point is covered; no requested pointer input was sent."
            else:
                points, _, _ = self.guard(operation, contract, ctx)
                if desktop_target:
                    expected_keyboard = (
                        self.captures.input(contract["target"]).keyboard
                        if operation.intent["kind"] == "scroll"
                        else self.captures.pointer_destination(contract["target"], points[0], ctx)
                    )

                def barrier():
                    nonlocal dispatch
                    operation.dispatched, dispatch = True, "unknown"

                def scroll_step_complete():
                    contract["scrollStarted"] = True

                self.desktop.pointer_sequence(
                    operation.intent["kind"],
                    points,
                    ctx,
                    **self.options(operation.intent),
                    before_dispatch=barrier,
                    validate=lambda: self.guard(operation, contract, ctx),
                    validate_drag=lambda: self.guard(operation, contract, ctx, during_drag=True),
                    scroll_step_complete=scroll_step_complete,
                )
                dispatch, detail = "dispatched", "Pointer sequence sent; read to verify its effect."
                ctx.check()
                if desktop_target:
                    self.captures.input_finished(contract["target"], ctx, expected=expected_keyboard, pointer=True)
        except Exception as error:
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
        if desktop_target and (error_code is not None or cleanup != "complete"):
            self.captures.input_failed()
        return dict(
            target=dict(contract["target"]),
            dispatch=dispatch,
            detail=detail,
            preparation=preparation,
            confirmation=confirmation,
            cleanup=cleanup,
            errorCode=error_code,
            retryReason=retry,
            context=None,
        )
