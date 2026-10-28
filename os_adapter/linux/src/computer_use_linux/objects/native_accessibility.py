"""AT-SPI connection for v5 raw facts, without starting a screen reader.

Native object handles and events stay here. Control classification, text
composition and route choice are not part of this adapter.
"""

from __future__ import annotations

from collections import OrderedDict

from computer_use_linux.contracts.protocol import Failure, gap


class NativeAccessibility:
    def __init__(self):
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi, Gio, GLib, GObject

        self._a, self.gio, self.glib = Atspi, Gio, GLib
        self.native_key = GObject.Object.__hash__
        self.ctx, self.dead = None, {}
        self._state, self._failure = "new", None
        self._initialized, self.listener, self._registered = False, None, []
        # Toolkit containers can emit a later focus notification even when
        # their current FOCUSED state is false. Keep bounded candidates; the
        # input unit validates live state and scope instead of trusting order.
        self.focus_events = OrderedDict()
        self.focus_lost = None
        self.events = (
            "object:children-changed",
            "object:property-change",
            "object:state-changed",
            "object:text-changed",
            "window:destroy",
        )

    @property
    def atspi(self):
        # Existing callers also obtain native interface methods through atspi.
        # Guard this boundary so retained objects cannot bypass initialization.
        self._ensure_started()
        return self._a

    def _check(self):
        if self.ctx:
            self.ctx.check()

    def _ensure_started(self):
        self._check()
        if self._state == "ready":
            return
        if self._state == "unavailable":
            raise Failure(self._failure, "observation_unavailable")
        if self._state != "new":
            raise Failure("The accessibility provider is " + self._state + ".", "observation_unavailable")
        from computer_use_linux.objects.accessibility_bus import connect_accessibility_bus

        self._state = "starting"
        try:
            # This selected-bus helper and libatspi.init are synchronous. Checks
            # bound admission/completion, not their internal blocking duration.
            connect_accessibility_bus(self.gio, self.glib)
            self._check()
            self._a.set_timeout(250, 250)
            status = self._a.init()
            self._initialized = status == 0
            self._check()
            if status != 0:
                raise Failure(
                    "AT-SPI initialization did not acquire a new provider (status " + str(status) + ").",
                    "observation_unavailable",
                )
            self.listener = self._a.EventListener.new(self.event)
            self._check()
            if self.listener is None:
                raise Failure("AT-SPI event listener is unavailable.", "observation_unavailable")
            for event in self.events:
                registered = self.listener.register(event)
                if registered:
                    self._registered.append(event)
                self._check()
                if not registered:
                    raise Failure("AT-SPI event registration failed: " + event, "observation_unavailable")
            self._state = "ready"
        except Exception as error:
            self._release()
            self._state = "new"
            # Cancellation/deadline is a request failure, never a permanent
            # provider diagnosis. Do not retry a genuine provider failure in
            # this driver session or select another accessibility bus.
            self._check()
            if isinstance(error, self.glib.Error) or (
                isinstance(error, Failure) and error.code == "observation_unavailable"
            ):
                self._state, self._failure = "unavailable", str(error)
                raise Failure(self._failure, "observation_unavailable") from error
            raise

    def _release(self):
        for event in self._registered:
            try:
                self.listener.deregister(event)
            except self.glib.Error:
                pass
        self._registered.clear()
        self.listener = None
        if self._initialized:
            self._initialized = False
            self._a.exit()

    def refresh(self, obj):
        self._ensure_started()
        self._clear_cache(obj)

    def _clear_cache(self, obj):
        clear = getattr(self._a.Accessible, "clear_cache_single", None)
        if clear is not None:
            clear(obj)
        else:
            # Older libraries have only recursive clear_cache, which can
            # invalidate descendants. Disable caching for this object instead.
            self._a.Accessible.set_cache_mask(obj, self._a.Cache.NONE)

    def event(self, event, *_):
        if self._state != "ready":
            return
        kind = event.type
        if kind == "object:state-changed:defunct":
            # libatspi also emits this from local Accessible.dispose, while
            # the GI proxy itself is being destroyed. Materializing source
            # can re-enter that wrapper's destruction and crash the process.
            # It is not proof that the remote UI disappeared. Live input
            # guards still refresh state/role and reject unreachable targets.
            return
        obj = event.source
        if obj is not None:
            if kind == "object:state-changed:focused":
                key = self.native_key(obj)
                if event.detail1:
                    self.focus_events[key] = obj
                    self.focus_events.move_to_end(key)
                    while len(self.focus_events) > 32:
                        self.focus_events.popitem(last=False)
                else:
                    self.focus_events.pop(key, None)
                    if self.focus_lost is not None:
                        # Forget optional discovery context conservatively;
                        # never query ancestry from a native event callback.
                        self.focus_lost()
            if kind == "window:destroy":
                self.dead[self.native_key(obj)] = obj
                self.focus_events.pop(self.native_key(obj), None)
            # Native events may be pumped between requests. They invalidate
            # local caches without starting a provider or using an old deadline.
            self._clear_cache(obj)

    def pump(self):
        loop = self.glib.MainContext.default()
        for _ in range(32):
            if not loop.pending():
                break
            loop.iteration(False)

    def begin(self, ctx):
        self.ctx = ctx
        ctx.check()
        self.pump()
        ctx.check()

    def call(self, fn, *args):
        self._ensure_started()
        if self.ctx:
            remaining = self.ctx.remaining_ms
            self.atspi.set_timeout(min(250, remaining), min(250, remaining))
        try:
            return fn(*args)
        finally:
            self._check()

    def live(self, obj):
        if obj is None or self.native_key(obj) in self.dead:
            raise Failure("Native accessible was destroyed.", "stale_target")
        try:
            self.refresh(obj)
            states = self.call(self.atspi.Accessible.get_state_set, obj)
            if states is None or states.contains(self.atspi.StateType.DEFUNCT):
                raise Failure("Native accessible is defunct or unavailable.", "stale_target")
            if self.call(self.atspi.Accessible.get_role, obj) == self.atspi.Role.INVALID:
                raise Failure("Native accessible no longer exists.", "stale_target")
            return states
        except self.glib.Error as error:
            raise Failure("Cannot confirm native target: " + str(error), "stale_target") from error

    def children(self, obj, limit, notes):
        total = self.call(self.atspi.Accessible.get_child_count, obj)
        # Exact types preserve scalar validation and identity contracts.
        # pylint: disable-next=unidiomatic-typecheck
        if type(total) is not int or total < 0:
            raise Failure("Native directory child count is unavailable.", "observation_incomplete")
        result = []
        for index in range(min(total, limit)):
            try:
                child = self.call(self.atspi.Accessible.get_child_at_index, obj, index)
            except self.glib.Error as error:
                gap(notes, "Native directory child unavailable: " + str(error))
                break
            if child is None:
                break
            result.append(child)
        if len(result) != total:
            gap(notes, f"Native directory collected {len(result)} of {total} entries.")
        return result

    def applications(self, notes=None):
        root = self.call(self.atspi.get_desktop, 0)
        if root is None:
            gap(notes, "AT-SPI desktop root is unavailable.")
            return []
        return self.children(root, 512, notes)

    def windows(self, notes=None):
        result = []
        for app in self.applications(notes):
            try:
                self.live(app)
                pid = self.call(self.atspi.Accessible.get_process_id, app)
                name = self.call(self.atspi.Accessible.get_name, app)
                result.extend((obj, pid, name) for obj in self.children(app, 128, notes))
            except (Failure, self.glib.Error) as error:
                if self.ctx:
                    self.ctx.check()
                if isinstance(error, Failure) and error.code not in (
                    "observation_unavailable",
                    "observation_incomplete",
                    "stale_target",
                ):
                    raise
                gap(notes, "Native application top-levels unavailable: " + str(error))
        return result

    def rect(self, obj):
        if not self.call(self.atspi.Accessible.get_component_iface, obj):
            return None
        rect = self.call(self.atspi.Component.get_extents, obj, self.atspi.CoordType.SCREEN)
        return (rect.x, rect.y, rect.width, rect.height) if rect.width > 0 and rect.height > 0 else None

    def matching_window(self, window, candidates):
        matches = []
        for obj, pid, _ in candidates:
            if window.pid and pid != window.pid:
                continue
            try:
                rect = self.rect(obj)
                title = self.call(self.atspi.Accessible.get_name, obj)
                bounds = (window.rect, window.frame_rect)
                if rect and title == window.title:
                    for bound in bounds:
                        if bound and all(abs(a - c) <= 3 for a, c in zip(rect, bound)):
                            matches.append(obj)
                            break
            except self.glib.Error:
                continue
        return matches[0] if len(matches) == 1 else None

    def close(self):
        if self._state == "closed":
            return
        self._state = "closed"
        self.focus_lost = None
        self.focus_events.clear()
        self.dead.clear()
        self._release()
