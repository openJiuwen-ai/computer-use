"""Keep one native keyboard recipient for one continuous input primitive.

This is not editor selection or a session focus lock. Capture happens after
preparation, before the first keyboard event. AT-SPI Collection (or
one bounded tree walk, as used by screen readers) discovers current FOCUSED
objects; native objects and their live parent paths, never tokens or labels,
prove identity. Subsequent checks do not rediscover or switch recipients.

An AX-free application can use its exact X input-focus window. That boundary
does not identify toolkit controls sharing one X window. Once an accessible
recipient is captured, losing its evidence stops input instead of downgrading.
"""

import time
from dataclasses import replace

from computer_use_linux.contracts.protocol import Failure, require
from computer_use_linux.control.native_focus_discovery import NativeFocusDiscovery, _InitialProbeBudget


class NativeSequenceReceiver(NativeFocusDiscovery):
    SEARCH_SECONDS = 0.5
    SEARCH_NODES = 1024

    def __init__(self, backend, window, ctx, *, recipients, obj=None, pointer_focused=False):
        self.recipients = recipients
        hint, self.hint = recipients.hint, None
        if pointer_focused or obj is None:
            recipients.hint = None
        super().__init__(backend.ax, None, ctx)
        self.desktop = backend.desktop
        self.window = window.key
        self.xid = self.desktop.keyboard_focus()
        self.obj = None
        self.parent_keys = ()
        self.source = "x11"
        try:
            self._native_focus()
        except Exception:
            recipients.hint = None
            raise
        # Desktop visual input must not initialize an accessibility service.
        # App input normally already observed its root. The lookup is local to
        # this primitive, not another long-lived application/window cache.
        root = window.accessible
        if root is not None or getattr(self.ax, "_state", None) == "ready":
            original = self.ax.ctx
            # An addressed object's identity, working-window path and current
            # recipient comparison are required evidence: they cannot fall
            # back to an unknown XID-only recipient. Their bounded native
            # queries keep the request deadline, including a following key.
            # Only App/visual discovery is optional and uses a short probe.
            self.ax.ctx = ctx if obj is not None else self._probe_context()
            try:
                self.ax.pump()
                if root is None:
                    root = self.ax.matching_window(window, self.ax.windows())
                if root is not None:
                    self.root = root
                    if obj is not None:
                        self.obj = obj
                        self.parent_keys = self._path(obj)
                        self.source = "specified"
                        # A toolkit can mark a focus scope FOCUSED while an
                        # independent native widget still receives real keys.
                        # Compare the addressed object with one bounded current
                        # recipient discovery; never redirect to that object.
                        # Only an exact pointer focus completed inside this
                        # primitive supplies a narrower discovery entry. AX
                        # FOCUSED/SetFocus alone cannot establish that route.
                        # Known independent focus events are still checked by
                        # _discover, regardless of the subtree entry.
                        if not pointer_focused and self._hint(hint, obj):
                            current, path = self.obj, self.parent_keys
                        else:
                            recipients.hint = None
                            current, path = self.discover(probe=True, subtree=obj if pointer_focused else None)
                        key = self._key(obj)
                        require(
                            current is not None or key == self._key(root),
                            "The specified control has no observed current keyboard recipient.",
                            "input_unavailable",
                        )
                        if current is not None:
                            require(
                                key in path or self._key(current) in self.parent_keys,
                                "An independent control is the current keyboard recipient.",
                                "input_unavailable",
                            )
                            if key in path:
                                self.obj, self.parent_keys = current, path
                            else:
                                self.source = "specified"
                    else:
                        self.obj, self.parent_keys = self.discover()
            except (Failure, self.ax.GLib.Error) as error:
                recipients.hint = None
                ctx.check()  # The request deadline/cancellation is never optional.
                probe_budget = isinstance(error, _InitialProbeBudget) or (
                    isinstance(error, Failure)
                    and error.code == "deadline_exceeded"
                    and self.ax.ctx.deadline < ctx.deadline
                    and time.monotonic() >= self.ax.ctx.deadline
                )
                invalid_error = (
                    True
                    if (
                        self.obj is not None
                        or self.candidate_seen
                        or (
                            isinstance(error, Failure)
                            and not probe_budget
                            and error.code not in ("observation_unavailable", "stale_target")
                        )
                    )
                    else False
                )
                if invalid_error:
                    raise Failure(
                        "Cannot establish one continuous keyboard recipient: " + str(error),
                        "stale_target"
                        if isinstance(error, Failure) and error.code == "stale_target"
                        else "input_unavailable",
                    ) from error
                # No accessible recipient was established. For App/visual
                # input, an optional local probe must not disable the exact
                # native XID route. A candidate followed by incomplete
                # evidence is wrapped by _discover and cannot enter here.
                self.source = "x11"
                if not probe_budget:
                    self.root = None
            finally:
                self.ax.ctx = original
        if obj is not None:
            if self.obj is None:
                recipients.hint = None
            require(
                self.obj is not None, "The specified keyboard recipient is no longer observable.", "input_unavailable"
            )
        self.validate()
        if self.obj is not None and pointer_focused:
            self.hint = recipients.hint = self.window, self.xid, self.obj

    def _hint(self, hint, obj):
        if hint is None or self.recipients.hint is not hint or hint[:2] != (self.window, self.xid):
            return False
        previous = hint[2]
        try:
            if not self.is_focused(previous):
                return False
            path = self.focus_path(previous)
        except (Failure, self.ax.GLib.Error):
            self.ctx.check()
            return False
        if path is None or not (self._key(obj) in path or self._key(previous) in self.parent_keys):
            return False
        if self.recipients.hint is not hint:
            return False
        # This object was established by a prior exact pointer capture, not
        # inferred from the first FOCUSED node in a window-wide census. It is
        # still only a discovery hint: validate() below refreshes identity,
        # parent scope, XID and all known independent focus notifications.
        self.obj, self.parent_keys, self.source, self.hint = previous, path, "hint", hint
        return True

    def _probe_context(self):
        return replace(self.ctx, deadline=min(self.ctx.deadline, time.monotonic() + self.SEARCH_SECONDS))

    def _native_focus(self):
        self.ctx.check()
        require(
            self.desktop.active() == self.window
            and self.desktop.focus_belongs(self.window)
            and self.desktop.keyboard_focus() == self.xid,
            "The original native keyboard focus changed during continuous input.",
            "input_unavailable",
        )

    def validate(self):
        try:
            self._validate()
        except Exception:
            self.recipients.hint = None
            raise

    def _validate(self):
        self._native_focus()
        require(
            self.hint is None or self.recipients.hint is self.hint,
            "Native focus was lost during continuous input.",
            "input_unavailable",
        )
        if self.obj is None and self.root is not None:
            # Initial unknown AX state authorizes only the exact current XID,
            # not ignoring later positive evidence that another recipient
            # acquired focus. Do not adopt that new object halfway through a
            # chord/text payload or rediscover the entire tree per character.
            self.ax.begin(self.ctx)
            for key, event in tuple(getattr(self.ax, "focus_events", {}).items()):
                if key == self._key(self.root):
                    continue
                try:
                    path = self.focus_path(event) if self.is_focused(event) else None
                except (Failure, self.ax.GLib.Error):
                    self.ctx.check()
                    path = None
                require(
                    path is None,
                    "Another control acquired keyboard focus during continuous input.",
                    "input_unavailable",
                )
        if self.obj is not None:
            self.ax.begin(self.ctx)
            # An explicit element already receives the normal target/scope
            # guard. Add its final live focus check, not a duplicate parent
            # walk. App/desktop recipients have no such addressed guard.
            path = self.parent_keys if self.source == "specified" else self._path(self.obj)
            for event in tuple(getattr(self.ax, "focus_events", {}).values()):
                if self._key(event) in path:
                    continue
                try:
                    event_path = self.focus_path(event) if self.is_focused(event) else None
                except (Failure, self.ax.GLib.Error):
                    self.ctx.check()
                    event_path = None
                require(
                    event_path is None,
                    "Another control acquired keyboard focus during continuous input.",
                    "input_unavailable",
                )
            # Parent queries can run application callbacks. Refresh focus after
            # them, and recheck the independent native X route before delivery.
            require(
                not any(self._hidden(self.objects[key]) for key in path),
                "The original control is no longer in a showing keyboard scope.",
                "input_unavailable",
            )
            require(
                self.is_focused(self.obj),
                "The original control lost keyboard focus during continuous input.",
                "input_unavailable",
            )
        self._native_focus()
        require(
            self.hint is None or self.recipients.hint is self.hint,
            "Native focus was lost during continuous input.",
            "input_unavailable",
        )
