"""OS-provider seam for the production Linux v5 server, never a fake RPC peer.

Only AT-SPI objects and desktop enumeration are substituted. The actual Server,
native directory, facts collector, reference lifetime and SDK run unchanged.
"""

from __future__ import annotations

import signal
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.append(str(Path(__file__).resolve().parents[2] / "os_adapter/linux/src"))
from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.objects.registry import Registry, Window
from computer_use_linux.runtime.server import Server


class NativeError(Exception):
    pass


@dataclass(eq=False)
class Node:
    role: str
    name: str = ""
    text: str = ""
    children: list = field(default_factory=list)
    parent: Node | None = None
    interfaces: list = field(default_factory=list)
    states: set = field(default_factory=lambda: {"ENABLED", "SENSITIVE", "SHOWING"})
    actions: list = field(default_factory=list)
    action_history: list = field(default_factory=list)
    relations: list = field(default_factory=list)
    attributes: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)
    value: float = 0
    selections: list = field(default_factory=list)
    pid: int = 42
    dead: bool = False
    caret: int | None = None
    toolkit: str = "gtk"
    toolkit_version: str = "3.24.0"


class Relation:
    def __init__(self, kind, targets):
        self.kind, self.targets = kind, targets

    def get_relation_type(self):
        return self.kind

    def get_n_targets(self):
        return len(self.targets)

    def get_target(self, index):
        return self.targets[index]


class AX:
    GLib = NS(Error=NativeError)
    native_key = staticmethod(id)

    def __init__(self, app):
        self.app, self.ctx, self.calls, self.hooks = app, None, [], {}

        def getter(name, fn):
            def get(obj, *args):
                self.calls.append((name, obj, args))
                if name in self.hooks:
                    self.hooks[name](obj, *args)
                if obj.dead:
                    raise NativeError("Native object was destroyed")
                if name in obj.errors:
                    result = obj.errors[name]
                    if isinstance(result, BaseException):
                        raise result
                    return result
                return fn(obj, *args)

            return get

        self.A = NS(
            StateType=NS(
                **{
                    state: state
                    for state in (
                        "ENABLED",
                        "SENSITIVE",
                        "SHOWING",
                        "VISIBLE",
                        "SELECTED",
                        "EXPANDED",
                        "FOCUSED",
                        "MODAL",
                        "EDITABLE",
                        "FOCUSABLE",
                        "CHECKED",
                        "INDETERMINATE",
                        "MULTI_LINE",
                    )
                }
            ),
            RelationType=NS(
                LABELLED_BY="labelled-by",
                LABEL_FOR="label-for",
                SUBWINDOW_OF="subwindow-of",
                POPUP_FOR="popup-for",
                PARENT_WINDOW_OF="parent-window-of",
            ),
            Accessible=NS(
                **{
                    name: getter(name, fn)
                    for name, fn in {
                        "get_role_name": lambda obj: obj.role,
                        "get_name": lambda obj: obj.name,
                        "get_description": lambda obj: "",
                        "get_accessible_id": lambda obj: "",
                        "get_attributes": lambda obj: obj.attributes,
                        "get_interfaces": lambda obj: obj.interfaces,
                        "get_state_set": lambda obj: NS(contains=set(obj.states).__contains__),
                        "get_child_count": lambda obj: len(obj.children),
                        "get_child_at_index": lambda obj, index: obj.children[index],
                        "get_parent": lambda obj: obj.parent,
                        "get_relation_set": lambda obj: obj.relations,
                        "get_process_id": lambda obj: obj.pid,
                        "get_application": lambda obj: self.app,
                        "get_toolkit_name": lambda obj: obj.toolkit,
                        "get_toolkit_version": lambda obj: obj.toolkit_version,
                    }.items()
                }
            ),
            Component=NS(grab_focus=getter("grab_focus", lambda obj: True)),
            Text=NS(
                **{
                    name: getter(name, fn)
                    for name, fn in {
                        "get_character_count": lambda obj: len(obj.text),
                        "get_text": lambda obj, start, end: obj.text[start:end],
                        "get_caret_offset": lambda obj: len(obj.text) if obj.caret is None else obj.caret,
                        "get_n_selections": lambda obj: len(obj.selections),
                        "get_selection": lambda obj, index: NS(
                            start_offset=obj.selections[index][0], end_offset=obj.selections[index][1]
                        ),
                        "add_selection": self.add_selection,
                        "set_selection": self.set_selection,
                    }.items()
                }
            ),
            EditableText=NS(
                **{
                    name: getter(name, fn)
                    for name, fn in {
                        "set_text_contents": self.replace,
                        "insert_text": self.insert,
                        "delete_text": self.delete,
                    }.items()
                }
            ),
            Value=NS(get_current_value=getter("get_current_value", lambda obj: obj.value)),
            Action=NS(
                get_n_actions=getter("get_n_actions", lambda obj: len(obj.actions)),
                get_action_name=getter("get_action_name", lambda obj, index: obj.actions[index][0]),
                get_action_description=getter("get_action_description", lambda obj, index: obj.actions[index][1]),
                do_action=getter("do_action", self.invoke),
            ),
        )

    @staticmethod
    def add_selection(obj, start, end):
        obj.selections.append((start, end))
        obj.caret = end
        return True

    @staticmethod
    def set_selection(obj, index, start, end):
        if not 0 <= index < len(obj.selections):
            return False
        obj.selections[index] = (start, end)
        obj.caret = end
        return True

    @staticmethod
    def invoke(obj, index):
        obj.action_history.append(obj.actions[index][0])
        obj.text = str(len(obj.action_history))
        return True

    @staticmethod
    def replace(obj, text):
        obj.text, obj.caret, obj.selections = text, len(text), []
        return True

    @staticmethod
    def insert(obj, position, text, length):
        assert length == len(text.encode("utf-8"))
        obj.text = obj.text[:position] + text + obj.text[position:]
        obj.caret = position + len(text)
        return True

    @staticmethod
    def delete(obj, start, end):
        obj.text = obj.text[:start] + obj.text[end:]
        obj.caret, obj.selections = start, []
        return True

    def call(self, fn, *args):
        if self.ctx:
            self.ctx.check()
        return fn(*args)

    def begin(self, ctx):
        self.ctx = ctx

    def pump(self):
        pass

    def live(self, obj):
        if obj.dead:
            raise Failure("Native object was destroyed", "stale_target")

    def applications(self, notes=None):
        return [self.app]

    def windows(self, notes=None):
        return [(obj, obj.pid, self.app.name) for obj in self.app.children]

    def matching_window(self, native, candidates):
        return native.accessible

    def facts(self, *_):
        raise AssertionError("v5 must not call the legacy semantic collector")


class Desktop:
    def __init__(self, registry, windows):
        self.registry, self.items = registry, windows

    def windows(self, ctx=None, notes=None):
        return list(self.items)

    def validate(self, item):
        current = next((w for w in self.items if w.key == item.window.key), None)
        if (
            not item.alive
            or current is None
            or (current.pid, current.process_start) != (item.window.pid, item.window.process_start)
        ):
            self.registry.retire(item.window.key)
            raise Failure("Working window closed or was replaced", "stale_target")
        return current


class Backend:
    def __init__(self, mode="shared", stopped=None):
        self.mode = mode
        self.registry, self.closed = Registry(), False
        self.leases, self.locked = 0, False
        self.editor = Node(
            "entry",
            "Input",
            "a😀bc",
            interfaces=["Text", "EditableText"],
            states={"ENABLED", "SENSITIVE", "SHOWING", "EDITABLE", "FOCUSABLE"},
            selections=[(1, 2)],
        )
        self.button = Node(
            "push button", "确认", "0", interfaces=["Action", "Text"], actions=[("click", "Click the button")]
        )
        self.long_text = Node("text", "Full text", "中😀" * 3000, interfaces=["Text"])
        self.window = Node("frame", "Fixture", children=[self.editor, self.button, self.long_text])
        self.app = Node("application", "Linux native fixture", children=[self.window])
        self.window.parent = self.app
        for child in self.window.children:
            child.parent = self.window
        self.ax = AX(self.app)
        self.desktop = Desktop(
            self.registry,
            [
                Window(
                    11,
                    "Fixture",
                    self.app.name,
                    42,
                    (0, 0, 640, 480),
                    process_start="generation",
                    accessible=self.window,
                )
            ],
        )

    def installed(self):
        return []

    def ensure_unlocked(self):
        if self.locked:
            raise Failure("Desktop is locked", "input_unavailable")

    @contextmanager
    def coordinate(self, ctx):
        ctx.check()
        self.leases += 1
        try:
            yield
        finally:
            self.leases -= 1

    def pump(self):
        self.ax.pump()

    def close(self):
        self.closed = True

    def execute(self, *_):
        raise AssertionError("v5 must not dispatch a legacy command")


class PixelDesktop(Desktop):
    can_capture = True
    session_type = "x11"

    def __init__(self, registry, windows):
        super().__init__(registry, windows)
        self.captures, self.after_capture = [], None

    def active(self):
        return self.items[-1].key if self.items else None

    def capture(self, instance, scope, ctx, *, max_dimension=4096):
        import base64
        import io

        from PIL import Image

        from computer_use_linux.contracts.protocol import now, token

        ctx.check()
        start = now()
        bounds = self.validate(instance).rect if instance else (0, 0, 1024, 768)
        x, y, width, height = bounds
        image = Image.new("RGB", (width, height), "#204080")
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        value = dict(
            id=token("image"),
            kind="window" if instance else "desktop",
            scope=scope,
            startedAt=start,
            completedAt=now(),
            width=image.width,
            height=image.height,
            png=base64.b64encode(buffer.getvalue()).decode(),
        )
        self.captures.append(value)
        if self.after_capture:
            self.after_capture()
        return value, (x, y, width / image.width, height / image.height)


class CaptureBackend(Backend):
    def __init__(self, mode="shared", stopped=None):
        super().__init__(mode, stopped)
        self.desktop = PixelDesktop(self.registry, self.desktop.items)
        if "--no-ax" in sys.argv:
            self.desktop.items[0].accessible = None


class FixtureTextKeyboard:
    """Only the native XKB map/state reader; production plan and sender remain."""

    shift = 225

    def __init__(self, desktop):
        self.desktop = desktop
        self.generation = 1
        self.state_overrides = {}
        self.symbols = {}
        shifted = '~!@#$%^&*()_+{}|:"<>?'
        plain = "`1234567890-=[]\\;',./"
        for char in "abcdefghijklmnopqrstuvwxyz0123456789 `-=[]\\;',./":
            code = ord(char) + 8
            self.symbols[code, False] = ord(char)
            self.symbols[code, True] = ord(shifted[plain.index(char)] if char in plain else char.upper())

    @staticmethod
    def key_name(code):
        return "shift" if code == 225 else chr(code - 8) if type(code) is int else code

    def snapshot(self):
        from computer_use_linux.system.x11_text import KeyboardState, XkbSnapshot

        depressed = 1 if self.shift in self.desktop.keys or "shift" in self.desktop.keys else 0
        fields = dict(mods=depressed, base_mods=depressed, lookup_mods=depressed)
        fields.update(self.state_overrides)
        symbols = {
            key: value
            for key, value in self.symbols.items()
            if value not in self.desktop.keymap_missing and chr(value) not in self.desktop.keymap_missing
        }
        return XkbSnapshot(self.generation, KeyboardState(**fields), symbols, self.shift)

    def close(self):
        pass


class KeyboardDesktop(Desktop):
    """Substitute X server effects; reuse production sequence/release logic."""

    from computer_use_linux.system.x11 import X11

    press_key = X11.press_key
    release = X11.release
    can_input = True
    X = NS(KeyPress=2, KeyRelease=3, ButtonRelease=5)

    def __init__(self, registry, windows):
        super().__init__(registry, windows)
        self.assist = False
        self.keys, self.buttons, self.events = set(), set(), []
        self.foreground, self.keyboard_window = 22, 22
        self.activations, self.event_hook, self.keymap_missing = [], None, set()
        self.text_keyboard = FixtureTextKeyboard(self)

    def text_preflight(self, *args, **kwargs):
        return self.X11.text_preflight(self, *args, **kwargs)

    def type_text(self, *args, **kwargs):
        return self.X11.type_text(self, *args, **kwargs)

    def active(self):
        return self.foreground

    def focus_belongs(self, xid):
        return self.keyboard_window == xid

    def keyboard_focus(self):
        return getattr(self, "keyboard_child", self.keyboard_window)

    def keycode(self, name):
        if name in self.keymap_missing:
            raise Failure("Unmapped key", "input_unavailable")
        return name

    def require_idle_input(self):
        if self.keys or self.buttons:
            raise Failure("Held input", "input_unavailable")

    def activate(self, window, ctx):
        ctx.sent()
        self.activations.append(window.key)
        self.foreground = self.keyboard_window = window.key

    def _event(self, kind, detail):
        detail = self.text_keyboard.key_name(detail)
        self.events.append((kind, detail))
        if self.event_hook:
            self.event_hook(kind, detail)


class KeyboardBackend(Backend):
    def __init__(self, mode="shared", stopped=None):
        import os

        super().__init__(mode, stopped)
        self.editor.interfaces.append("Component")
        self.other_editor = Node(
            "entry",
            "Other input",
            "untouched",
            interfaces=["Text", "Component"],
            states={"ENABLED", "SENSITIVE", "SHOWING", "FOCUSABLE", "FOCUSED"},
        )
        self.other_window = Node("frame", "Other window", children=[self.other_editor], parent=self.app)
        self.other_editor.parent = self.other_window
        self.app.children.append(self.other_window)
        self.counter = Node("text", "Submissions", "0", interfaces=["Text"], parent=self.window)
        self.window.children.append(self.counter)
        self.desktop = KeyboardDesktop(
            self.registry,
            [
                *self.desktop.items,
                Window(
                    22,
                    "Other window",
                    self.app.name,
                    42,
                    (650, 0, 500, 500),
                    process_start="generation",
                    accessible=self.other_window,
                ),
            ],
        )
        self.desktop.assist = os.environ.get("COMPUTER_USE_FOREGROUND_ASSIST") == "1"

        def focus(obj):
            for node in (self.editor, self.other_editor):
                node.states.discard("FOCUSED")
            obj.states.add("FOCUSED")
            if "--self-activate" in sys.argv:
                key = 11 if obj is self.editor else 22
                self.desktop.foreground = self.desktop.keyboard_window = key

        def key(kind, detail):
            if kind == self.desktop.X.KeyPress and detail == "Return":
                if self.desktop.keyboard_window == 11 and "FOCUSED" in self.editor.states:
                    self.counter.text = str(int(self.counter.text) + 1)

        self.ax.hooks["grab_focus"] = focus
        self.desktop.event_hook = key


class TypedTextBackend(KeyboardBackend):
    """Actual text/selection effects behind production keyboard event delivery."""

    def __init__(self, mode="shared", stopped=None):
        super().__init__(mode, stopped)
        self.editor.text, self.editor.caret, self.editor.selections = "abcd", 2, []
        self.editor.interfaces.remove("EditableText")
        self.other_editor.states.add("EDITABLE")
        if "--typed-native-editable" in sys.argv:
            self.editor.interfaces.append("EditableText")
        if "--typed-no-text" in sys.argv:
            self.editor.interfaces.remove("Text")
        if "--typed-readonly" in sys.argv:
            self.editor.states.discard("EDITABLE")
        if "--typed-ready" in sys.argv:
            self.desktop.foreground = self.desktop.keyboard_window = 11
            self.editor.states.add("FOCUSED")
            self.other_editor.states.discard("FOCUSED")
        if "--typed-no-ax" in sys.argv:
            self.desktop.items[0].accessible = None
        self.journal = Path(sys.argv[sys.argv.index("--typed-journal") + 1]) if "--typed-journal" in sys.argv else None
        previous = self.desktop.event_hook

        def event(kind, detail):
            import json

            previous(kind, detail)
            if kind == self.desktop.X.KeyPress:
                target = next(
                    (
                        node
                        for key, node in ((11, self.editor), (22, self.other_editor))
                        if self.desktop.keyboard_window == key and "FOCUSED" in node.states
                    ),
                    None,
                )
                if target is not None and "EDITABLE" in target.states:
                    start, end = (
                        target.selections[0]
                        if target.selections
                        else (len(target.text) if target.caret is None else target.caret,) * 2
                    )
                    if detail == "a" and "ctrl" in self.desktop.keys:
                        target.selections = [(0, len(target.text))]
                        target.caret = len(target.text)
                    elif detail == "BackSpace":
                        if start == end:
                            start = max(0, start - 1)
                        target.text = target.text[:start] + target.text[end:]
                        target.caret, target.selections = start, []
                    elif len(detail) == 1 and not ({"ctrl", "alt", "super"} & self.desktop.keys):
                        char = detail
                        if "shift" in self.desktop.keys or self.desktop.text_keyboard.shift in self.desktop.keys:
                            plain = "`1234567890-=[]\\;',./"
                            shifted = '~!@#$%^&*()_+{}|:"<>?'
                            char = shifted[plain.index(char)] if char in plain else char.upper()
                        target.text = target.text[:start] + char + target.text[end:]
                        target.caret, target.selections = start + len(char), []
            if self.journal is not None:
                with self.journal.open("a") as stream:
                    stream.write(
                        json.dumps(
                            dict(
                                kind=kind,
                                detail=detail,
                                active=self.desktop.foreground,
                                text=self.editor.text,
                                other=self.other_editor.text,
                                keys=sorted(map(str, self.desktop.keys)),
                            )
                        )
                        + "\n"
                    )
            if "--typed-release-failed" in sys.argv and kind == self.desktop.X.KeyRelease:
                raise NativeError("Native key release unavailable")
            if "--typed-move-after-shift" in sys.argv and kind == self.desktop.X.KeyPress and detail == "shift":
                self.editor.states.discard("FOCUSED")
                self.other_editor.states.add("FOCUSED")
                self.desktop.foreground = self.desktop.keyboard_window = 22

        self.desktop.event_hook = event


class PasteClipboard:
    """Native clipboard seam; production units still own preparation and keys.

    Clipboard selection wire/storage is tested separately against the actual
    provider. This seam models ownership, recipient consumption and cleanup,
    without replacing a Server, SDK, Engine or execution unit.
    """

    def __init__(self, backend):
        self.backend, self.calls = backend, []
        self.text, self.owned, self.transferred = "", False, False
        self.before_send, self.after_send = None, None
        self.cleanup, self.error = "complete", None
        self.guards = 0

    def paste(self, text, ctx, send, consumer):
        self.calls.append(text)
        self.text, self.owned, self.transferred, self.consumer = text, True, False, consumer
        error = None

        def guard():
            self.guards += 1
            ctx.check()
            if not self.owned:
                raise Failure("Clipboard ownership changed", "input_unavailable")

        try:
            ctx.sent()
            if self.before_send:
                self.before_send()
            send(guard)
            if self.after_send:
                self.after_send()
            if self.error:
                raise self.error
        except Exception as caught:
            error = caught
        finally:
            if self.cleanup == "complete":
                self.owned = False
        return dict(transferred=self.transferred, cleanup=self.cleanup, error=error, prepared=True)


class PasteBackend(KeyboardBackend):
    def __init__(self, mode="shared", stopped=None):
        super().__init__(mode, stopped)
        self.clipboard, self.clipboard_requests = PasteClipboard(self), 0
        self.paste_counter = Node("text", "Paste count", "0", interfaces=["Text"], parent=self.window)
        self.window.children.append(self.paste_counter)
        if "--readonly" in sys.argv:
            self.editor.states.discard("EDITABLE")
        if "--clipboard-cleanup-failed" in sys.argv:
            self.clipboard.cleanup = "failed"
        previous = self.desktop.event_hook

        def key(kind, detail):
            previous(kind, detail)
            if kind != self.desktop.X.KeyPress or detail != "v" or "ctrl" not in self.desktop.keys:
                return
            for window, node in ((11, self.editor), (22, self.other_editor)):
                if (
                    self.desktop.keyboard_window == window
                    and "FOCUSED" in node.states
                    and self.clipboard.owned
                    and self.clipboard.consumer(window)
                ):
                    start, end = node.selections[0] if node.selections else (len(node.text), len(node.text))
                    node.text = node.text[:start] + self.clipboard.text + node.text[end:]
                    node.selections = []
                    self.clipboard.transferred = True
                    self.paste_counter.text = str(int(self.paste_counter.text) + 1)

        self.desktop.event_hook = key

        def consumer(instance):
            original = self.desktop.validate(instance)
            return lambda xid: any(
                w.key == xid and (w.pid, w.process_start) == (original.pid, original.process_start)
                for w in self.desktop.items
            )

        self.desktop.clipboard_consumer = consumer

    def get_clipboard(self):
        self.clipboard_requests += 1
        return self.clipboard


class PointerDesktop(PixelDesktop):
    """Only native pixels/window hits/X events are substituted, not v5 policy."""

    from computer_use_linux.system.x11 import X11

    point = X11.point
    pointer = X11.pointer
    expose = X11.expose
    release = X11.release
    press_key = X11.press_key
    text_preflight = KeyboardDesktop.text_preflight
    type_text = KeyboardDesktop.type_text
    keycode = KeyboardDesktop.keycode
    can_input = True
    X = NS(KeyPress=2, KeyRelease=3, ButtonPress=4, ButtonRelease=5, MotionNotify=6)

    def __init__(self, registry, windows, mode):
        import os

        super().__init__(registry, windows)
        self.mode, self.assist = mode, os.environ.get("COMPUTER_USE_FOREGROUND_ASSIST") == "1"
        self.keys, self.buttons, self.events = set(), set(), []
        self.keymap_missing = set()
        self.text_keyboard = FixtureTextKeyboard(self)
        self.position, self.foreground, self.keyboard_window = (900, 700), 22, 22
        self.covered, self.user_held = "--covered" in sys.argv, False
        self.activations, self.hit_checks = [], []
        self.event_hook = self.activate_hook = None
        self.screen_size = (1024, 768)
        self.root = NS(get_geometry=lambda: NS(width=self.screen_size[0], height=self.screen_size[1]))
        self.journal = (
            Path(sys.argv[sys.argv.index("--pointer-journal") + 1]) if "--pointer-journal" in sys.argv else None
        )

    def active(self):
        return self.foreground

    def focus_belongs(self, xid):
        return self.keyboard_window == xid

    def keyboard_focus(self):
        return getattr(self, "keyboard_child", self.keyboard_window)

    def pointer_sequence(self, *args, **kwargs):
        return self.X11.pointer_sequence(self, *args, **kwargs)

    def pointer_preflight(self, *args, **kwargs):
        return self.X11.pointer_preflight(self, *args, **kwargs)

    def require_idle_input(self):
        if self.keys or self.buttons or self.user_held:
            raise Failure("User or previous sequence holds input", "input_unavailable")

    def require_hit(self, instance, point):
        window = self.validate(instance)
        self.hit_checks.append((window.key, tuple(point)))
        x, y, width, height = window.rect
        if self.covered or not (x <= point[0] < x + width and y <= point[1] < y + height):
            raise Failure("Target point is covered or outside its window; no click was sent.", "stale_target")

    def activate(self, window, ctx):
        ctx.sent()
        self.activations.append(window.key)
        self.foreground = self.keyboard_window = window.key
        self.covered = False
        if self.activate_hook:
            self.activate_hook()

    def _request_activation(self, window, ctx):
        # Native WM effects only: production expose() owns bounded readiness.
        # Exposing a window does not prove or require keyboard-focus transfer.
        ctx.sent()
        self.activations.append(window.key)
        self.foreground = window.key
        self.covered = False
        if self.activate_hook:
            self.activate_hook()

    def _event(self, kind, detail=0, x=0, y=0):
        import json

        self.events.append((kind, detail, x, y))
        if kind == self.X.MotionNotify:
            self.position = (x, y)
        if self.journal is not None:
            with self.journal.open("a") as stream:
                stream.write(
                    json.dumps(
                        dict(kind=kind, detail=detail, x=x, y=y, active=self.foreground, buttons=sorted(self.buttons))
                    )
                    + "\n"
                )
        if "--pointer-release-failed" in sys.argv and kind == self.X.ButtonRelease:
            raise NativeError("Synthetic release unavailable")
        if self.event_hook:
            self.event_hook(kind, detail, x, y)


class PointerBackend(Backend):
    def __init__(self, mode="shared", stopped=None):
        super().__init__(mode, stopped)
        self.desktop = PointerDesktop(self.registry, self.desktop.items, mode)
        # Pixel routes must remain usable when the selected app has no AX tree.
        self.desktop.items[0].accessible = None


def component_geometry(backend):
    """Native Component geometry/hit seam shared by pointer and text fixtures."""
    backend.hit_overrides, backend.hit_queries = {}, []
    backend.ax.A.StateType.VISIBLE = "VISIBLE"
    backend.ax.A.CoordType = NS(SCREEN="screen")

    def native(name, fn):
        def call(obj, *args):
            backend.ax.calls.append((name, obj, args))
            if name in backend.ax.hooks:
                backend.ax.hooks[name](obj, *args)
            if obj.dead:
                raise NativeError("Native object was destroyed")
            if name in obj.errors:
                value = obj.errors[name]
                if isinstance(value, BaseException):
                    raise value
                return value
            return fn(obj, *args)

        return call

    def contains(obj, x, y, coordinate):
        rect = getattr(obj, "rect", None)
        return rect is not None and rect[0] <= x < rect[0] + rect[2] and rect[1] <= y < rect[1] + rect[3]

    def hit(obj, x, y, coordinate):
        backend.hit_queries.append((obj, x, y))
        if obj in backend.hit_overrides:
            return backend.hit_overrides[obj]
        for child in reversed(obj.children):
            if "SHOWING" in child.states and contains(child, x, y, coordinate):
                return child
        return obj if contains(obj, x, y, coordinate) else None

    backend.ax.A.Component.get_extents = native(
        "get_extents", lambda obj, coord: NS(**dict(zip(("x", "y", "width", "height"), obj.rect)))
    )
    backend.ax.A.Component.contains = native("contains", contains)
    backend.ax.A.Component.get_accessible_at_point = native("get_accessible_at_point", hit)
    return contains


class TypedPointerDesktop(KeyboardDesktop):
    """X11 keys and pointer use production senders, with native effects only."""

    X = PointerDesktop.X
    pointer_preflight = PointerDesktop.pointer_preflight
    pointer_sequence = PointerDesktop.pointer_sequence
    require_hit = PointerDesktop.require_hit
    require_idle_input = PointerDesktop.require_idle_input
    expose = PointerDesktop.expose
    _request_activation = PointerDesktop._request_activation

    def __init__(self, registry, windows):
        super().__init__(registry, windows)
        self.position, self.covered, self.user_held = (900, 700), False, False
        self.hit_checks, self.screen_size = [], (1200, 800)
        self.root = NS(get_geometry=lambda: NS(width=self.screen_size[0], height=self.screen_size[1]))

    def _event(self, kind, detail=0, x=0, y=0):
        if kind in (self.X.KeyPress, self.X.KeyRelease):
            return super()._event(kind, detail)
        self.events.append((kind, detail, x, y))
        if kind == self.X.MotionNotify:
            self.position = x, y
        if self.event_hook:
            self.event_hook(kind, detail)
        return None


class TypedPointerBackend(TypedTextBackend):
    def __init__(self, mode="shared", stopped=None):
        super().__init__(mode, stopped)
        previous = self.desktop.event_hook
        self.desktop = TypedPointerDesktop(self.registry, self.desktop.items)
        self.window.interfaces = ["Component"]
        self.window.rect, self.editor.rect = (0, 0, 640, 480), (40, 50, 100, 60)
        self.window.states.add("VISIBLE")
        self.editor.states.add("VISIBLE")
        contains = component_geometry(self)

        def event(kind, detail):
            if (
                kind == self.desktop.X.ButtonPress
                and detail == 1
                and contains(self.editor, *self.desktop.position, "screen")
            ):
                self.editor.states.add("FOCUSED")
                self.other_editor.states.discard("FOCUSED")
                self.desktop.foreground = self.desktop.keyboard_window = 11
                self.editor.caret, self.editor.selections = 0, []
            previous(kind, detail)

        self.desktop.event_hook = event


class ElementPointerBackend(Backend):
    """AT-SPI Component/X11 seams; production contracts and sequence run unchanged.

    Component hit queries return the immediate child under one real coordinate,
    forcing the driver to traverse the native hit path. Tests may change native
    rectangles or hit responses, never the v5 input context or chosen primitive.
    """

    def __init__(self, mode="shared", stopped=None):
        super().__init__(mode, stopped)
        self.group = Node("panel", "Controls", interfaces=["Component"], parent=self.window)
        self.canvas = Node("drawing area", "Canvas", "0", interfaces=["Component", "Text"], parent=self.group)
        self.button.parent = self.group
        self.button.interfaces.append("Component")
        self.group.children = [self.canvas, self.button]
        self.window.children = [self.group]
        self.window.interfaces = ["Component"]
        self.window.rect, self.group.rect = (0, 0, 640, 480), (20, 20, 350, 250)
        self.canvas.rect, self.button.rect = (40, 50, 100, 60), (200, 50, 100, 60)
        for obj in (self.window, self.group, self.canvas, self.button):
            obj.states.add("VISIBLE")
        self.desktop = PointerDesktop(self.registry, self.desktop.items, mode)
        contains = component_geometry(self)

        def event(kind, detail, x, y):
            if "--element-move-on-motion" in sys.argv and kind == self.desktop.X.MotionNotify:
                self.canvas.rect = (70, 70, 100, 60)
            if kind == self.desktop.X.ButtonPress and contains(self.canvas, *self.desktop.position, "screen"):
                self.canvas.text = str(int(self.canvas.text) + 1)

        self.desktop.event_hook = event
        if "--element-action-false" in sys.argv:
            self.ax.hooks["do_action"] = lambda obj, index: obj.action_history.append(obj.actions[index][0])
            self.button.errors["do_action"] = False
        if "--element-no-component" in sys.argv:
            self.canvas.interfaces.remove("Component")
        if "--element-no-ax" in sys.argv:
            self.desktop.items[0].accessible = None
        if "--element-no-input" in sys.argv:
            self.desktop.can_input = False
        if "--element-overlap" in sys.argv:
            self.overlap = Node("button", "Overlap", interfaces=["Component"], parent=self.group)
            self.overlap.rect = self.canvas.rect
            self.overlap.states.add("VISIBLE")
            self.group.children.append(self.overlap)


def main():
    from computer_use_linux.windowing import native_directory

    native_directory.process_start = lambda pid: "generation"
    server = Server(
        TypedTextBackend
        if "--typed-text" in sys.argv
        else ElementPointerBackend
        if "--element-pointer" in sys.argv
        else PointerBackend
        if "--pointer" in sys.argv
        else PasteBackend
        if "--paste" in sys.argv
        else KeyboardBackend
        if "--keyboard" in sys.argv
        else CaptureBackend
        if "--capture" in sys.argv
        else Backend
    )
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: server.stopped.set())
    return server.run()


if __name__ == "__main__":
    raise SystemExit(main())
