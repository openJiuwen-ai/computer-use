"""Query current focus and its necessary containment path without preparing input."""

from computer_use_linux.contracts.protocol import Failure, require
from computer_use_linux.control.native_focus_discovery import NativeFocusConflict, NativeFocusDiscovery
from computer_use_linux.control.native_target import NativeTarget


class NativeCurrentReceiver:
    """Own no input state; report local focus gaps without retiring the scope."""

    _FOCUS_CONTAINERS = frozenset(("panel", "section", "filler", "viewport", "embedded", "grouping"))

    def __init__(self, backend, store, directory):
        self.backend, self.ax, self.store = backend, backend.ax, store
        self.target = NativeTarget(backend, store, directory)

    def inspect(self, target, within, ctx):
        require(
            target["node"] is not None and target["capture"] is None,
            "Current receiver observation requires an exact native container.",
        )
        # Original-target failures remain request failures. Only the subsequent
        # focus observation is optional evidence and may return unavailable.
        obj = self.target.validate_scope(target, within, ctx)
        _, window = self.target.window(target, ctx)
        result = self.target.context(target, within, [], False, None)
        result["enabled"] = "unknown"  # This query grants no operation capability.
        evidence = self._observe(obj, target, window, ctx)
        # This applies to every focus outcome, including unavailable/absent.
        # Loss of the requested scope is never an optional-focus failure.
        self.target.validate_scope(target, within, ctx)
        result["input"]["currentReceiver"] = evidence
        return result

    def _observe(self, obj, target, window, ctx):
        def publish(state, node=None, ancestors=(), detail=None):
            evidence = dict(state=state, node=node, ancestors=list(ancestors))
            if detail is not None:
                evidence["detail"] = detail
            return evidence

        def terminal(candidate):
            # Only known AT-SPI grouping roles justify searching below a
            # focused object. Missing role evidence does not license a child
            # search; the public resolver checks the candidate's own semantics.
            try:
                role = self.ax.call(self.ax.A.Accessible.get_role_name, candidate)
            except self.ax.GLib.Error:
                ctx.check()
                return True
            return not isinstance(role, str) or role not in self._FOCUS_CONTAINERS

        try:
            root = window.accessible
            if root is None:
                root = self.ax.matching_window(window, self.ax.windows())
            if root is None:
                return publish("unavailable", detail="The original window has no confirmed accessibility root.")
            self.ax.pump()
            discovery = NativeFocusDiscovery(self.ax, root, ctx)
            focused, path = discovery.discover(terminal=terminal)
            if focused is None:
                return publish("absent", detail="No current accessible receiver was reported in the original window.")
            requested = self.ax.native_key(obj)
            if requested not in path:
                return publish("conflict", detail="The current receiver is outside the requested container.")
            if discovery.source in ("event", "collection") and not terminal(focused):
                # These sources may name a FOCUSED toolkit container while
                # its descendant is the actual editor. Refine only that
                # positive branch using the same discovery object, node set
                # and original request deadline. This is not another window
                # census and does not consume continuous-input hints.
                candidate = self.ax.native_key(focused)
                focused, path = discovery.discover(probe=True, subtree=focused, terminal=terminal)
                if focused is None or candidate not in path:
                    return publish("unavailable", detail="The focused candidate branch changed during observation.")
                if requested not in path:
                    return publish("conflict", detail="The current receiver left the requested container.")
            index = path.index(requested)
            ancestors = [self.store.keep(target["surface"], discovery.objects[key]) for key in path[1 : index + 1]]
            node = self.store.keep(target["surface"], focused)
            # Focus queries/registration can run provider callbacks. Check the
            # scoped candidate again; do not issue a second whole-tree census.
            if not discovery.is_focused(focused) or discovery.focus_path(focused) != path:
                return publish("unavailable", detail="The current receiver changed during observation.")
            evidence = dict(state="confirmed", node=node, ancestors=ancestors)
        except NativeFocusConflict as error:
            ctx.check()
            return publish("conflict", detail=str(error))
        except (Failure, self.ax.GLib.Error) as error:
            ctx.check()  # Cancellation/deadline are never downgraded.
            if isinstance(error, Failure) and (
                error.effect != "none"
                or error.code
                not in ("input_unavailable", "observation_incomplete", "observation_unavailable", "stale_target")
            ):
                raise
            return publish("unavailable", detail="Current receiver evidence is incomplete: " + str(error))
        return evidence
