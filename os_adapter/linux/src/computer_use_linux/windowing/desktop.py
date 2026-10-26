"""Session-specific desktop services; AT-SPI remains usable without X11.

The accessibility-only provider intentionally has no screenshot or synthetic
input. An absent desktop capability is reported before dispatch, not emulated
through Xwayland or by choosing some other application's window.
"""

from __future__ import annotations

import os

from computer_use_linux.contracts.protocol import Failure, gap
from computer_use_linux.objects.registry import Window


def coordination_identity(desktop):
    display = os.environ.get("WAYLAND_DISPLAY") if desktop.session_type == "wayland" else os.environ.get("DISPLAY", "")
    return display or os.environ.get("DBUS_SESSION_BUS_ADDRESS", "accessibility")


def ensure_unlocked(ax):
    "Consult the selected session's lock service, without starting one."
    gio, g_lib = ax.Gio, ax.GLib
    for name, path in (
        ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver"),
        ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver"),
        ("org.mate.ScreenSaver", "/org/mate/ScreenSaver"),
        ("org.cinnamon.ScreenSaver", "/org/cinnamon/ScreenSaver"),
    ):
        try:
            bus = gio.bus_get_sync(gio.BusType.SESSION, None)
            result = bus.call_sync(
                name,
                path,
                name,
                "GetActive",
                None,
                g_lib.VariantType.new("(b)"),
                gio.DBusCallFlags.NO_AUTO_START,
                150,
                None,
            )
            if result.unpack()[0]:
                raise Failure(
                    "Desktop is locked; no input was dispatched.",
                    "input_unavailable",
                    next="Unlock this desktop session before continuing.",
                )
        except g_lib.Error:
            continue


def process_start(pid):
    try:
        with open(f"/proc/{pid}/stat") as stream:
            return stream.read().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return ""


class AccessibilityDesktop:
    can_capture = False
    can_input = False

    def __init__(self, ax, retire, session_type, reason=""):
        self.ax, self.retire = ax, retire
        self.session_type = session_type
        self.notes = [
            ("Window catalogue includes AT-SPI windows only; applications without accessibility may be absent.")
        ]
        if reason:
            self.notes.append(reason)

    def info(self, obj, app_name=""):
        ax = self.ax
        states = ax.live(obj)
        pid = ax.call(ax.A.Accessible.get_process_id, obj)
        app = ax.call(ax.A.Accessible.get_application, obj)
        name = app_name or (ax.call(ax.A.Accessible.get_name, app) if app is not None else "")
        return Window(
            f"atspi:{ax.native_key(obj)}",
            ax.call(ax.A.Accessible.get_name, obj),
            name or "Unknown application",
            pid,
            ax.rect(obj),
            modal=states.contains(ax.A.StateType.MODAL),
            mapped=states.contains(ax.A.StateType.SHOWING),
            process_start=process_start(pid),
            accessible=obj,
        )

    def windows(self, ctx=None, notes=None):
        ax = self.ax
        windows = []
        for note in self.notes:
            gap(notes, note)
        for obj, _, name in ax.windows(notes=notes) if notes is not None else ax.windows():
            if ctx:
                ctx.check()
            try:
                window = self.info(obj, name)
                # Hidden windows are still included: SHOWING is not identity.
                windows.append(window)
            except (Failure, ax.GLib.Error) as error:
                gap(notes, "AT-SPI window query failed: " + str(error))
                if ax.ctx:
                    ax.ctx.check()
        if notes is not None:
            self.raw_window_relations(windows, notes)
            return windows
        objects = {w.accessible: w for w in windows}
        parents = {w.key: set() for w in windows}
        for window in windows:
            for parent in ax.call(ax.Relation.get_is_subwindow_of, window.accessible):
                if parent in objects and parent != window.accessible:
                    parents[window.key].add(objects[parent].key)
            for child in ax.call(ax.Relation.get_is_parent_window_of, window.accessible):
                if child in objects and child != window.accessible:
                    parents[objects[child].key].add(window.key)
        for window in windows:
            if len(parents[window.key]) == 1:
                window.transient = next(iter(parents[window.key]))
        return windows

    def raw_window_relations(self, windows, notes):
        """A native failed relation query is not a declaration of no parent.

        Read direct AT-SPI relations and preserve query failures separately
        from successful empty results.
        """
        ax = self.ax
        objects = {ax.native_key(w.accessible): w for w in windows}
        parents, unknown = {w.key: set() for w in windows}, set()
        kinds = {
            getattr(ax.A.RelationType, name, None): name for name in ("SUBWINDOW_OF", "POPUP_FOR", "PARENT_WINDOW_OF")
        }
        if None in kinds:
            kinds.pop(None)
            unknown.update(w.key for w in windows)
            gap(notes, "This AT-SPI library does not expose all window relation types.")
        for window in windows:
            try:
                relations = ax.call(ax.A.Accessible.get_relation_set, window.accessible)
                if not isinstance(relations, (list, tuple)) or len(relations) > 128:
                    raise Failure("Invalid or oversized native window relation set.", "observation_incomplete")
                for relation in relations:
                    kind = kinds.get(relation.get_relation_type())
                    if kind is None:
                        continue
                    count = relation.get_n_targets()
                    if type(count) is not int or not 0 <= count <= 512:
                        raise Failure("Native window relation exceeds the target budget.", "observation_incomplete")
                    for index in range(count):
                        obj = ax.call(relation.get_target, index)
                        other = objects.get(ax.native_key(obj)) if obj is not None else None
                        if other is None:
                            gap(notes, "A native window relationship points outside the available catalogue.")
                            if kind != "PARENT_WINDOW_OF":
                                unknown.add(window.key)
                            continue
                        child, parent = (other, window) if kind == "PARENT_WINDOW_OF" else (window, other)
                        parents[child.key].add(parent.key)
            except (Failure, ax.GLib.Error) as error:
                if ax.ctx:
                    ax.ctx.check()
                unknown.add(window.key)
                gap(notes, "Native window relationship unavailable: " + str(error))
        for window in windows:
            candidates = parents[window.key]
            if len(candidates) == 1 and window.key not in unknown:
                window.transient = next(iter(candidates))
                window.parent_state = "known"
            elif candidates or window.key in unknown or window.modal:
                window.transient = None
                window.parent_state = "unknown"
                if len(candidates) > 1:
                    gap(notes, "Native window relationship has conflicting parents.")
            else:
                window.parent_state = "none"

    def validate(self, instance):
        if not instance.alive:
            raise Failure("Working window was closed.", "stale_target")
        try:
            current = self.info(instance.window.accessible)
        except (Failure, self.ax.GLib.Error) as error:
            self.retire(instance.window.key)
            raise Failure("The bound accessible window no longer exists.", "stale_target") from error
        if (current.key, current.pid, current.process_start) != (
            instance.window.key,
            instance.window.pid,
            instance.window.process_start,
        ):
            self.retire(instance.window.key)
            raise Failure("Working window identity changed.", "stale_target")
        return current

    def unavailable(self, operation):
        raise Failure(
            (
                f"{operation}"
                f" is not connected for this "
                f"{self.session_type}"
                f" session. Native accessibility reading and supported element "
                f"actions remain available."
            ),
            "mode_unsupported",
            next=(
                "Use an observed element's supported native action, or connect an X11 "
                "desktop for screenshot and keyboard/mouse "
                "control."
            ),
        )

    def capture(self, *_):
        self.unavailable("Screenshot capture")

    def prepare(self, *_args, **_kwargs):
        self.unavailable("Synthetic input")

    def present(self, *_):
        self.unavailable("Demo foreground presentation")

    def require_focus(self, *_):
        self.unavailable("Keyboard routing")

    def require_hit(self, *_):
        self.unavailable("Visual pointer input")

    def point(self, *_):
        self.unavailable("Screenshot coordinates")

    def pointer(self, *_args, **_kwargs):
        self.unavailable("Pointer input")

    def press_key(self, *_):
        self.unavailable("Keyboard input")

    def type_text(self, *_):
        self.unavailable("Synthetic typing")

    def clipboard_consumer(self, *_):
        self.unavailable("Clipboard paste")

    def active(self):
        return None

    def pump(self):
        pass

    def release(self):
        pass

    def close(self):
        pass

    def configure_launch(self, context):
        # Preserve the actual session. In particular never redirect a Wayland
        # app into the Xwayland DISPLAY merely because that variable exists.
        pass


def connect_desktop(ax, mode, retire):
    wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))
    if wayland:
        return AccessibilityDesktop(ax, retire, "wayland")
    if not os.environ.get("DISPLAY"):
        return AccessibilityDesktop(
            ax, retire, "accessibility-only", "DISPLAY is absent; using the session's accessibility bus."
        )
    from computer_use_linux.system.x11 import X11

    try:
        return X11(mode, retire)
    except Exception as error:
        return AccessibilityDesktop(ax, retire, "x11-unavailable", f"X11 connection unavailable: {error}")
