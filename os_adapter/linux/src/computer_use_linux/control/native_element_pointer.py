"""Pointer input for one retained AT-SPI object, without keyboard focus.

Geometry belongs to the requested object. Python may select its exact native
hit as a passive control member, with retained structural evidence; this unit
does not choose a child or interpret roles. NativePointer owns dispatch,
cleanup and receipts; this unit supplies the element-bound gesture guard.
"""

import math
from typing import Any, NamedTuple

from computer_use_linux.contracts.protocol import Failure, require
from computer_use_linux.control.native_pointer import NativePointer, PointerRouteState


class ElementPointerTarget(NamedTuple):
    obj: Any
    states: Any
    interfaces: Any
    role: Any
    enabled: Any
    selected: Any
    native: Any


class ElementPointerSample(NamedTuple):
    """Element gesture geometry and the exact window sampled for its hit check."""

    points: tuple[tuple[float, float], ...]
    frame: Any
    selected: Any
    native: Any


class NativeElementPointer(NativePointer):
    def __init__(self, backend, store, directory, captures):
        super().__init__(backend, store, directory, captures)
        self.ax = self.b.ax

    def read_target(self, target, within, ctx):
        self.ax.begin(ctx)
        obj, states, interfaces, role, enabled = self.target.read(target, within, ctx)
        selected = self.directory.surfaces[target["surface"]]
        native = self.desktop.validate(selected)
        return ElementPointerTarget(obj, states, interfaces, role, enabled, selected, native)

    def _hit(self, root, point, ctx):
        # AT-SPI may return an immediate child or a deeper descendant. Descend
        # until the provider returns self/None. A null result at the root is
        # unknown, not evidence that a requested child's rectangle can be used.
        current, seen, reported = root, set(), False
        for _ in range(64):
            ctx.check()
            self.ax.live(current)
            key = self.ax.native_key(current)
            require(key not in seen, "Native pointer hit path is cyclic.", "input_unavailable")
            seen.add(key)
            interfaces = self.target.call("Accessible", "get_interfaces", current)
            require(
                isinstance(interfaces, (list, tuple)),
                "Native pointer hit interfaces are unavailable.",
                "input_unavailable",
            )
            if "Component" not in interfaces:
                return current if reported else None
            child = self.target.call(
                "Component", "get_accessible_at_point", current, *point, self.ax.atspi.CoordType.SCREEN
            )
            if child is None:
                return current if reported else None
            if self.ax.native_key(child) == key:
                return current
            current, reported = child, True
        raise Failure("Native pointer hit path exceeded its budget.", "input_unavailable")

    def query_geometry(self, read, ctx, *, kind, options):
        obj, states, interfaces, _, enabled, _, native = read
        require(
            getattr(self.desktop, "can_input", False),
            "This desktop has no native pointer event route.",
            "mode_unsupported",
        )
        require(enabled, "The selected pointer control is disabled.", "input_unavailable")
        require(
            native.mapped
            and "Component" in interfaces
            and all(states.contains(getattr(self.ax.atspi.StateType, state)) for state in ("VISIBLE", "SHOWING")),
            "The selected control has no currently visible pointer geometry.",
            "input_unavailable",
        )
        rect = self.target.call("Component", "get_extents", obj, self.ax.atspi.CoordType.SCREEN)
        require(rect is not None, "The selected control has no native bounds.", "input_unavailable")
        frame = tuple(getattr(rect, key, None) for key in ("x", "y", "width", "height"))
        require(
            all(type(v) in (int, float) and abs(v) <= 2147483647 and math.isfinite(v) for v in frame)
            and frame[2] > 0
            and frame[3] > 0,
            "The selected control has invalid pointer bounds.",
            "input_unavailable",
        )
        x, y, width, height = native.rect
        left, top = max(frame[0], x), max(frame[1], y)
        right, bottom = min(frame[0] + frame[2], x + width), min(frame[1] + frame[3], y + height)
        require(
            right > left and bottom > top, "The selected control is outside its working window.", "input_unavailable"
        )
        # Both AT-SPI SCREEN and XTEST use integer screen pixels. Use the same
        # in-bounds pixel for hit testing and event delivery, including 1px areas.
        point = (math.floor((left + right) / 2), math.floor((top + bottom) / 2))
        require(
            left <= point[0] < right and top <= point[1] < bottom,
            "The visible control region has no usable pixel.",
            "input_unavailable",
        )
        try:
            self.desktop.pointer_preflight(kind, (point,), **options)
        except Failure as error:
            if error.code != "invalid_request":
                raise
            # The intent was validated at begin. This coordinate came from the
            # provider, not the user: an offscreen/unsupported native pixel must
            # only disable the pointer route, leaving an addressed Action usable.
            raise Failure(
                "Native element pointer geometry is unavailable: " + str(error), "input_unavailable"
            ) from error
        root = native.accessible
        if root is None:
            root = self.ax.matching_window(native, self.ax.windows())
        require(root is not None, "The working window has no unique native accessibility root.", "input_unavailable")
        hit = self._hit(root, point, ctx)
        require(hit is not None, "The native pointer hit at the control center is unavailable.", "input_unavailable")
        return (point,), (tuple(native.rect), frame, point), hit

    def _member(self, operation, target, within, selection, ctx, *, continuing_hit=None):
        if selection is None:
            self.target.validate_scope(target, within, ctx)
            return self.store.get(target["node"], target["surface"]).obj
        if selection.actuator["relation"] == "scrollPointer":
            require(
                operation.intent["kind"] == "scroll" and selection.requested == target,
                "A selected scroll member requires its original element scroll.",
            )
            region = selection.actuator["region"]
            # A scroll area can be the requested element's ancestor. Both the
            # original element and the selected hit must remain inside that
            # same fixed area; ordinary controlPart descendant rules differ.
            constraints = tuple(within) + (() if region == target["node"] or region in within else (region,))
            self.target.validate_scope(target, constraints, ctx)
            path = selection.actuator.get("contentPath")
            member = dict(selection.recipient, node=path[-1]) if continuing_hit is not None else selection.recipient
            self.target.validate_scope(member, () if member["node"] == region else (region,), ctx)
            obj = (
                continuing_hit if continuing_hit is not None else self.store.get(member["node"], target["surface"]).obj
            )
            states = self.target.state(obj)
            require(
                any(states.contains(getattr(self.ax.atspi.StateType, key)) for key in ("ENABLED", "SENSITIVE")),
                "The selected scroll member is disabled.",
                "input_unavailable",
            )
            if continuing_hit is None:
                selection.check(ctx)
            else:
                selection.check_scroll_content(continuing_hit, ctx)
            return obj
        require(
            operation.intent["kind"] == "click" and selection.actuator["relation"] == "controlPart",
            "A selected pointer member requires its original element click.",
        )
        # Reuse exact owner/member containment, without using the member's
        # geometry or turning it into a text receiver. Facts are checked after
        # geometry/provider callbacks on every continuous sequence boundary.
        member, _ = self.target.selected(target, within, selection, ctx, check=True)
        return self.store.get(member["node"], member["surface"]).obj

    def _geometry_read(self, operation, target, selection, ctx, requested):
        if selection is None or selection.actuator["relation"] != "scrollPointer":
            return requested
        require(operation.intent["kind"] == "scroll", "Scroll geometry requires a scroll intent.")
        require(requested[4], "The requested scroll element is disabled.", "input_unavailable")
        region = dict(target, node=selection.actuator["region"])
        read = self.read_target(region, (), ctx)
        # Python may use an explicitly requested generic container as its own
        # area. Borrowing an ancestor still requires a declared native pane;
        # the selection evidence keeps the original role and path unchanged.
        require(
            region["node"] == target["node"] or read[3] == "scroll pane",
            "The selected ancestor is no longer a native scroll pane.",
            "input_unavailable",
        )
        return read

    def require_member_hit(self, hit, member):
        require(
            self.ax.native_key(hit) == self.ax.native_key(member),
            "The control center no longer hits its selected native pointer target.",
            "input_unavailable",
        )

    def _sample(self, operation, contract, ctx) -> ElementPointerSample:
        read = self.read_target(contract["target"], contract["within"], ctx)
        obj, _, _, role, _, selected, native = read
        require(
            self.ax.native_key(obj) == contract["object"] and role == contract["role"],
            "The original pointer control identity or role changed.",
            "stale_target",
        )
        geometry = self._geometry_read(operation, contract["target"], contract.get("selection"), ctx, read)
        points, frame, hit = self.query_geometry(
            geometry, ctx, kind=operation.intent["kind"], options=self.options(operation.intent)
        )
        selection = contract.get("selection")
        continuing = (
            hit
            if (contract.get("scrollStarted") and selection is not None and selection.actuator.get("contentPath"))
            else None
        )
        member = self._member(
            operation, contract["target"], contract["within"], selection, ctx, continuing_hit=continuing
        )
        self.require_member_hit(hit, member)
        return ElementPointerSample(points, frame, selected, native)

    def _route(self, selected, point):
        try:
            self.desktop.require_hit(selected, point)
        except Failure as error:
            if error.code not in ("stale_target", "input_unavailable"):
                raise
            return False
        return True

    def inspect(self, operation, target, within, ctx, selection=None):
        read = self.read_target(target, within, ctx)
        obj, _, _, role, enabled, selected, native = read
        operation.bind(target, within, self.store)
        hit, points = None, None
        try:
            geometry = self._geometry_read(operation, target, selection, ctx, read)
            points, _, hit = self.query_geometry(
                geometry, ctx, kind=operation.intent["kind"], options=self.options(operation.intent)
            )
        except (Failure, self.ax.glib.Error) as error:
            if isinstance(error, Failure) and error.code not in (
                "input_unavailable",
                "mode_unsupported",
                "stale_target",
            ):
                raise
        member = self._member(operation, target, within, selection, ctx) if selection is not None else obj
        geometry = hit is not None and self.ax.native_key(hit) == self.ax.native_key(member)
        ready = geometry and self._route(selected, points[0])
        supported = bool(getattr(self.desktop, "can_input", False)) and (
            not geometry or ready or not native.override_redirect
        )
        identifier = operation.contract(
            "pointer_sequence",
            target,
            within,
            object=self.ax.native_key(obj),
            role=role,
            supported=supported,
            elementPointer=True,
            gesture=None,
            selection=selection,
        )
        pending = ([] if enabled else ["enabled"]) + ([] if geometry else ["geometry"])
        if geometry and not ready:
            pending.append("foreground")
        primitive = dict(
            id=identifier,
            unit="pointer_sequence",
            channel="pointer",
            supported="yes" if supported else "no",
            readiness="yes" if ready else "no",
            pending=pending,
            backgroundPreparation="no",
        )
        result = self.target.context(target, within, [primitive], enabled, role)
        result["input"].update(geometryCurrent="yes" if geometry else "unknown", pointerRoute="yes" if ready else "no")
        if hit is not None:
            # This reference is an observation, not authority to click a child.
            # Python supplies a guarded controlPart before a distinct hit can
            # become executable. Keep it alive through that bounded lookup.
            ref = self.store.keep(target["surface"], hit)
            self.store.pins.add(ref)
            result["input"]["pointerHit"] = ref
        return result

    def execute(self, operation, command, ctx):
        contract = operation.contracts.get(command["primitiveId"])
        require(
            operation.intent["kind"] in ("click", "scroll")
            and contract is not None
            and contract.get("elementPointer") is True,
            "The addressed pointer contract is unavailable.",
            "stale_target",
        )
        return super().execute(operation, command, ctx)

    def guard(self, operation, contract, ctx, *, during_drag=False):
        require(not during_drag, "Element pointer input does not support dragging.", "invalid_request")
        points, frame, selected, native = self._sample(operation, contract, ctx)
        self.desktop.require_hit(selected, points[0])
        if contract["gesture"] is None:
            # Inspection/preparation may move this same control. Freeze only at
            # delivery, then stop if hover/layout changes before a later press.
            contract["gesture"] = frame
        require(
            contract["gesture"] == frame, "Element pointer geometry changed during the sequence.", "input_unavailable"
        )
        return points, selected, native

    def click_state(self, operation, contract, ctx) -> PointerRouteState:
        points, _, selected, native = self._sample(operation, contract, ctx)
        return PointerRouteState(points, selected, native, self._route(selected, points[0]))

    def prepare(self, operation, contract, foreground, ctx, preparation):
        points, _, selected, native = self._sample(operation, contract, ctx)
        if self._route(selected, points[0]):
            return True
        if not foreground:
            return False
        require(
            self.b.mode == "exclusive" or self.desktop.assist,
            "Host policy does not permit foreground assistance.",
            "mode_unsupported",
        )
        require(
            not native.override_redirect,
            "An unmanaged popup cannot be activated to repair a pointer route.",
            "input_unavailable",
        )
        self.desktop.require_idle_input()
        preparation.append("foreground")
        self.desktop.activate(native, ctx)
        # Recheck identity, modality, geometry and exact hit after preparation.
        # There is no gesture yet; moving the same control is not a stale image.
        points, _, selected, _ = self._sample(operation, contract, ctx)
        self.desktop.require_hit(selected, points[0])
        return True
