"""Current native object/scope checks shared by addressed execution units.

No candidate search, action semantics, projection, or route selection here.
"""

from typing import Any, NamedTuple

from computer_use_linux.contracts.protocol import Failure, require, token


class TargetState(NamedTuple):
    """Live object metadata sampled before the final scope verification."""

    obj: Any
    states: Any
    interfaces: list[str] | tuple[str, ...]
    role: str
    enabled: bool


class NativeTarget:
    def __init__(self, backend, store, directory):
        self.b, self.ax, self.store, self.directory = backend, backend.ax, store, directory

    def call(self, interface, name, obj, *args):
        method = getattr(getattr(self.ax.atspi, interface, None), name, None)
        if method is None:
            raise Failure("Native provider has no " + interface + "." + name + ".", "input_unavailable")
        return self.ax.call(method, obj, *args)

    def state(self, obj):
        self.ax.live(obj)
        states = self.call("Accessible", "get_state_set", obj)
        if states is None:
            raise Failure("The selected control state is unavailable.", "input_unavailable")
        return states

    def read_only(self, states):
        flag = getattr(self.ax.atspi.StateType, "READ_ONLY", None)
        return flag is not None and states.contains(flag)

    def window(self, target, ctx):
        ctx.check()
        self.b.ensure_unlocked()
        self.directory.validate(target, ctx)
        selected = self.directory.surfaces[target["surface"]]
        native = self.b.desktop.validate(selected)
        items = self.directory.windows(ctx, [])
        descendants = {native.key}
        for _ in range(len(items)):
            added = {i.window.key for i in items if i.window.transient in descendants}
            if added <= descendants:
                break
            descendants |= added
        blocked = [
            i.ref
            for i in items
            if i.window.key != native.key and i.window.key in descendants and i.window.modal and i.window.mapped
        ]
        if blocked:
            raise Failure("A modal window blocks the requested control: " + ", ".join(blocked), "blocked_by_modal")
        return selected, native

    def validate_scope(self, target, within, ctx):
        """Recheck the bound object's window and ancestor boundary.

        Provider queries can run application callbacks. Execution units call
        this after their last metadata query, before the possible mutation.
        This is a live boundary check, not an atomic desktop transaction.
        """
        _, native = self.window(target, ctx)
        root = native.accessible
        if root is None:
            root = self.ax.matching_window(native, self.ax.windows())
        if root is None:
            raise Failure("The target window has no unique native accessibility root.", "input_unavailable")
        self.ax.live(root)
        obj = self.store.get(target["node"], target["surface"]).obj
        require(target["node"] not in within, "An ancestor scope cannot contain the target itself.", "invalid_request")
        required = {self.ax.native_key(self.store.get(ref, target["surface"]).obj) for ref in within}
        current, seen = obj, set()
        for _ in range(128):
            if current is None or self.ax.native_key(current) in seen:
                break
            self.ax.live(current)
            key = self.ax.native_key(current)
            seen.add(key)
            if key == self.ax.native_key(root):
                if not required <= seen:
                    raise Failure("The selected control left its observed ancestor scope.", "stale_target")
                break
            current = self.call("Accessible", "get_parent", current)
        else:
            raise Failure("The native parent path exceeded its verification budget.", "input_unavailable")
        if self.ax.native_key(root) not in seen:
            raise Failure("The native control no longer belongs to the selected working window.", "stale_target")
        return obj

    def selected(self, target, within, selection, ctx, *, check=False, require_editable=False):
        """Validate an already selected member; never search or change intent.

        Python supplies the owner/member pair and its collected structural
        facts. Both native objects remain inside the original working window.
        """
        if selection is None:
            return target, within
        require(selection.requested == target, "The selected member changed its requested owner.", "stale_target")
        if check:
            selection.check(ctx)
        self.validate_scope(target, within, ctx)
        states = self.state(self.store.get(target["node"], target["surface"]).obj)
        require(
            any(states.contains(getattr(self.ax.atspi.StateType, key)) for key in ("ENABLED", "SENSITIVE")),
            "The requested owner is disabled.",
            "input_unavailable",
        )
        if require_editable:
            require(not self.read_only(states), "The requested owner is read-only.", "input_unavailable")
        recipient = selection.recipient
        constraints = (*within, target["node"])
        # Recheck after provider callbacks; the parent path must prove the
        # original owner, not merely membership of the same application.
        self.validate_scope(recipient, constraints, ctx)
        return recipient, constraints

    def read(self, target, within, ctx) -> TargetState:
        ctx.check()
        self.b.ensure_unlocked()
        obj = self.store.get(target["node"], target["surface"]).obj
        # Read semantics first, then prove the original scope after provider
        # callbacks. No unrelated keyboard focus is needed for object units.
        states = self.state(obj)
        interfaces = self.call("Accessible", "get_interfaces", obj)
        require(isinstance(interfaces, (list, tuple)), "Native interface list is unavailable.", "input_unavailable")
        enabled = any(states.contains(getattr(self.ax.atspi.StateType, key)) for key in ("ENABLED", "SENSITIVE"))
        role = self.call("Accessible", "get_role_name", obj)
        self.validate_scope(target, within, ctx)
        return TargetState(obj, states, interfaces, role, enabled)

    @staticmethod
    def context(target, within, primitives, enabled, role, *, selection="unknown", actions_complete=False):
        return dict(
            kind="inputContext",
            binding=dict(target),
            observed=token("observation"),
            targetLabel="Native control",
            identity="yes",
            withinScope="yes",
            enabled="yes" if enabled else "no",
            blockedBy=[],
            primitives=primitives,
            nativeRole=role if isinstance(role, str) and role else None,
            actionsComplete=actions_complete,
            within=list(within),
            input=dict(
                foregroundSurface=None,
                keyboardSurface=None,
                focusedNode=None,
                focusAncestors=[],
                editor=None,
                editorOwner=None,
                selectionKnown=selection,
                geometryCurrent="unknown",
                windowRoute="unknown",
                pointerRoute="unknown",
            ),
        )
