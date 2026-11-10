# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Human intent: a desktop, an application with optional window scope, or a control."""

from __future__ import annotations

import sys
import weakref

from computer_use.diagnostics import local_failure
from computer_use.interaction.agent.requests import (
    ActOnElement,
    CloseWindow,
    DiscoverApps,
    DiscoverWindows,
    ObserveDesktop,
    ObserveElement,
    ObserveText,
    ObserveUI,
    ResolveElement,
    UIKeyboard,
    UIPointer,
)
from computer_use.interaction.requests import ComputerInput
from computer_use.native.contracts import Intent
from computer_use.protocol.primitives import nonempty
from computer_use.protocol.validation import normalize_key, validate_paste_text, validate_text
from computer_use.session.operations import operation

from . import parameters
from .execution import on_session, session_executor, synchronous
from .handles import handle_created
from .host import global_input_allowed
from .targets import Target, as_target, image_target, target_request


def observation_state(state=None, *, document=None):
    if document is not None:
        from computer_use.session.observation import lazy_observation

        return lazy_observation(document, (state or {}).get("screenshot"))
    from computer_use.session.observation import CapturedTextDocument, lazy_observation

    return lazy_observation(
        CapturedTextDocument(state["text"]),
        state.get("screenshot"),
        details_document=CapturedTextDocument(state.get("details", state["text"])),
    )


def _text(text, paste=False):
    try:
        (validate_paste_text if paste else validate_text)(text)
    except Exception as error:
        raise local_failure(str(error)) from error


def _key(key, controller):
    platform = getattr(controller.transport, "platform", None)
    try:
        allow_macos_glyphs = platform == "macos" or (platform is None and sys.platform == "darwin")
        return normalize_key(key, allow_macos_glyphs=allow_macos_glyphs)
    except Exception as error:
        raise local_failure(str(error)) from error


def _window(window):
    if window is not None:
        parameters.reference(window, "window reference")


class Computer:
    def __init__(self, *, allow_global_input=True):
        """Create a session. On macOS only direct Computer input methods use global events."""
        from .factory import session_controller

        if allow_global_input is not True and allow_global_input is not False:
            raise TypeError("allow_global_input must be a boolean")
        self._executor = session_executor()

        def create():
            controller = session_controller(allow_global_input=allow_global_input and global_input_allowed())
            # Client construction binds this controller to its owning session executor.
            # pylint: disable-next=protected-access
            controller._executor = self._executor
            self._executor.register(controller)
            return controller

        try:
            self._controller = self._executor.call(create)
        except BaseException:
            self._executor.close_if_owned()
            raise
        self._apps = weakref.WeakValueDictionary()

    def close(self):
        self._executor.close_client(self._controller)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    @on_session
    def app(self, selector=None, *, window_title=None):
        if window_title is not None:
            if selector is not None:
                raise local_failure("select either an application or an exact window title")
            try:
                nonempty(window_title, "window_title")
            except ValueError as error:
                raise local_failure(str(error)) from error
            if len(window_title.encode("utf-16-le", errors="surrogatepass")) > 8188:
                raise local_failure("window_title exceeds 4094 UTF-16 units")
            return App(self._controller, {"selector": window_title}, _internal=True, window_title=window_title)
        # Exact types preserve scalar validation and identity contracts.
        # pylint: disable-next=unidiomatic-typecheck
        if type(selector) is int:
            parameters.reference(selector, "application reference")
            scope, key = self._controller.interfaces.application(selector), ("reference", selector)
        elif isinstance(selector, str):
            try:
                nonempty(selector, "selector")
            except Exception as error:
                raise local_failure(str(error)) from error
            scope, key = dict(selector=selector), ("selector", selector)
        else:
            raise local_failure(
                "app expects an integer reference or application selector string",
            )
        app = self._apps.get(key)
        # An explicit textual lookup may create a new handle after the prior
        # process ended. Previously returned handles keep their own lifetime.
        if app is not None and isinstance(selector, str):
            running = self._controller.state(app).running
            if (
                running is not None and selector != running.application
                and self._controller.interfaces.windows.application_ended(running.session, running.application)
            ):
                app = None
        if app is None:
            app = App(self._controller, scope, _internal=True)
            self._apps[key] = app
        return app

    @synchronous
    def list_apps(self):
        """Return application choices with id, display_name, and is_running fields."""
        return operation(lambda: self._controller.run(DiscoverApps()))

    @synchronous
    def list_windows(self, *, app_name):
        """Return visible macOS window metadata for an exact application display name."""
        try:
            nonempty(app_name, "app_name")
        except ValueError as error:
            raise local_failure(str(error)) from error
        return operation(lambda: self._controller.run(DiscoverWindows(app_name)))

    @synchronous
    def read(self, *, view="both", full=False):
        parameters.view(view, full)
        return operation(lambda: self._controller.run(ObserveDesktop(view, full)))

    @synchronous
    def click(self, options=None, *, at=None, target=None):
        """On macOS, click a native screen point or a geometrically located Target."""
        if (at is None) == (target is None):
            raise local_failure("click expects exactly one of at or target")
        point = self._input_point(target if target is not None else at)
        return self._global_input("click", parameters.click(options), (point,))

    @synchronous
    def scroll(self, direction, *, at=None, target=None, pages=1):
        parameters.scroll(direction, pages)
        if (at is None) == (target is None):
            raise local_failure("scroll expects exactly one of at or target")
        point = self._input_point(target if target is not None else at)
        return self._global_input("scroll", {"direction": direction, "pages": pages}, (point,))

    @synchronous
    def drag(self, start, end):
        points = self._input_point(start), self._input_point(end)
        if points[0] == points[1]:
            raise local_failure("drag start and end must differ")
        return self._global_input("drag", {}, points)

    @synchronous
    def type_text(self, text):
        """Type into the OS's current receiver, without selecting or focusing one."""
        _text(text)
        return self._global_input("insert", {"text": text})

    @synchronous
    def press_key(self, key):
        return self._global_input("key", {"key": _key(key, self._controller)})

    @synchronous
    def paste(self, text):
        _text(text, paste=True)
        return self._global_input("paste", {"text": text})

    @on_session
    def point(self, at):
        """Retain a point in the last delivered desktop image, including its scale."""
        return image_target(self._controller, None, parameters.point(at))

    def _input_point(self, value):
        return parameters.screen_point(value) if isinstance(value, tuple) else as_target(self._controller, value)

    def _global_input(self, intent, args, points=()):
        return operation(lambda: self._controller.run(ComputerInput(Intent(intent, args, global_input=True), points)))


class App:
    def __init__(self, controller=None, scope=None, *, _internal=False, window_title=None):
        if not _internal:
            raise TypeError("App requires an application binding")
        self._controller = controller
        self._executor = executor_for_controller(controller)
        self._lookup_scopes = ()
        self._menu_only = False
        self._context = None
        self._window_title = window_title
        self._title_window_ref = None
        controller.register(self, scope)
        from computer_use.session.interfaces import InstanceState

        running = controller.state(self).running
        self._instance = (
            controller.interfaces.windows.application(running.session, running.application, running.name)
            if running is not None
            else InstanceState(scope["selector"])
        )
        self._anchor, self._image = None, None
        handle_created(self, dict(kind="App", controller=controller, selector=scope["selector"]))

    @synchronous
    def close_window(self, window):
        """Request the selected window's normal close; return any confirmation UI."""
        parameters.reference(window, "window reference")
        return operation(lambda: self._controller.run(CloseWindow(self, window)))

    @synchronous
    def element(self, target, *, timeout_ms=3000, window=None, fresh=False):
        """Locate a name/path in the latest observation, or explicitly query fresh facts.

        Numbers always retain their original binding; fresh only controls
        name/path lookup. Reads and subsequent input still use their own rules.
        """
        _window(window)
        # pylint: disable-next=unidiomatic-typecheck
        if type(timeout_ms) is not int or not 0 <= timeout_ms <= 10000:
            raise local_failure("timeout_ms must be an integer in 0-10000")
        # pylint: disable-next=unidiomatic-typecheck
        if type(target) is int:
            parameters.reference(target, "element reference")
        if fresh is not True and fresh is not False:
            raise local_failure("fresh must be a boolean")

        async def resolve():
            request = ResolveElement(self, target, timeout_ms, window, fresh)
            snapshot = await operation(lambda: self._controller.run(request))
            return Element(self._controller, self, snapshot, _internal=True)

        return resolve()

    @synchronous
    def read(self, *, view="tree", full=False, menu_path=None, allow_foreground=False, window=None):
        _window(window)
        parameters.view(view, full, menu_path)
        if allow_foreground is not True and allow_foreground is not False:
            raise local_failure("allow_foreground must be a boolean")
        if self._window_title is not None and menu_path is not None:
            raise local_failure("menu_path can expand beyond the exact titled window")
        return operation(lambda: self._controller.run(ObserveUI(self, view, full, menu_path, allow_foreground, window)))

    @synchronous
    def click(self, options=None, *, at, window=None, force_pointer=False):
        """Click an image location; force_pointer keeps this call on mouse delivery."""
        _window(window)
        if force_pointer is not True and force_pointer is not False:
            raise local_failure("force_pointer must be a boolean")
        click = parameters.click(options)
        if force_pointer:
            click["pointerOnly"] = True
        if isinstance(at, Target):
            return self._point_action(at, "click", click, window=window)
        at = parameters.point(at)
        return operation(lambda: self._controller.run(UIPointer(self, "click", (at,), click, window)))

    @synchronous
    def scroll(self, direction, *, at, pages=1, window=None):
        _window(window)
        parameters.scroll(direction, pages)
        if isinstance(at, Target):
            return self._point_action(at, "scroll", {"direction": direction, "pages": pages}, window=window)
        at = parameters.point(at)
        request = UIPointer(self, "scroll", (at,), {"direction": direction, "pages": pages}, window)
        return operation(lambda: self._controller.run(request))

    @synchronous
    def drag(self, start, end, *, window=None):
        _window(window)
        if isinstance(start, Target) or isinstance(end, Target):
            return self._point_action(start, "drag", {}, window=window, end=end)
        start, end = parameters.point(start), parameters.point(end)
        if start == end:
            raise local_failure("drag start and end must differ")
        return operation(lambda: self._controller.run(UIPointer(self, "drag", (start, end), {}, window)))

    @synchronous
    def type_text(self, text, *, window=None):
        _window(window)
        _text(text)
        return operation(lambda: self._controller.run(UIKeyboard(self, "type_text", dict(text=text), window)))

    @synchronous
    def paste(self, text, *, window=None):
        _window(window)
        _text(text, paste=True)
        return operation(lambda: self._controller.run(UIKeyboard(self, "paste", dict(text=text), window)))

    @synchronous
    def press_key(self, key, *, window=None):
        _window(window)
        key = _key(key, self._controller)
        return operation(lambda: self._controller.run(UIKeyboard(self, "press_key", dict(key=key), window)))

    @synchronous
    def point(self, at, *, window=None):
        """Retain a point in this App's or the selected window's delivered image."""
        from computer_use.interaction.agent.scope import request_scope

        _window(window)
        at = parameters.point(at)

        async def retain():
            owner = await request_scope(self._controller, self, window)
            return image_target(self._controller, owner, at)

        return operation(retain)

    def _point_action(self, target, intent, args, *, window=None, end=None):
        from computer_use.interaction.agent.scope import request_scope

        async def execute():
            owner = await request_scope(self._controller, self, window)
            request = target_request(self._controller, target, intent, args, owner=owner, end=end)
            return await self._controller.run(request)

        return operation(execute)


class Element:
    def __init__(self, controller=None, owner=None, snapshot=None, *, _internal=False):
        if not _internal:
            raise TypeError("Element requires an App element binding")
        self._controller, self._owner, self._snapshot = controller, owner, snapshot
        self._executor = executor_for_controller(controller)
        handle_created(self, dict(kind="Element", controller=controller, owner=owner, snapshot=snapshot, query=None))

    def _act(self, intent, args=None):
        return operation(lambda: self._controller.run(ActOnElement(self._owner, self._snapshot, intent, args or {})))

    @property
    @on_session
    def target(self):
        return Element._retain_target(self)

    def _retain_target(self):
        return Target(
            self._controller,
            self._owner,
            self._snapshot["binding"],
            _snapshot=dict(self._snapshot),
            _internal=True,
        )

    @synchronous
    def read(self, *, cursor=None):
        parameters.cursor(cursor)
        return operation(lambda: self._controller.run(ObserveElement(self._owner, self._snapshot, cursor)))

    @synchronous
    def read_text(self, *, cursor=None, length=4096):
        parameters.cursor(cursor)
        # pylint: disable-next=unidiomatic-typecheck
        if type(length) is not int or not 1 <= length <= 16384:
            raise local_failure("length must be an integer in 1-16384 code points")
        return operation(lambda: self._controller.run(ObserveText(self._owner, self._snapshot, cursor, length)))

    @synchronous
    def click(self, options=None):
        return self._act("click", parameters.click(options))

    @synchronous
    def scroll(self, direction, *, pages=1):
        parameters.scroll(direction, pages)
        return self._act("scroll", dict(direction=direction, pages=pages))

    @synchronous
    def type_text(self, text, *, replace=False):
        _text(text)
        # pylint: disable-next=unidiomatic-typecheck
        if type(replace) is not bool:
            raise local_failure("replace must be a bool")
        return self._act("replace" if replace else "insert", dict(text=text))

    @synchronous
    def paste(self, text):
        _text(text, paste=True)
        return self._act("paste", dict(text=text))

    @synchronous
    def press_key(self, key):
        return self._act("key", {"key": _key(key, self._controller)})

    @synchronous
    def focus(self):
        return self._act("focus")

    @synchronous
    def open_menu(self):
        return self._act("openMenu")

    @synchronous
    def perform_action(self, action):
        try:
            nonempty(action, "action")
        except Exception as error:
            raise local_failure(str(error)) from error
        return self._act("action", dict(action=action))


def executor_for_controller(controller):
    executor = getattr(controller, "_executor", None)
    if executor is None:
        executor = session_executor()
        # Handle construction shares the controller's existing session executor.
        # pylint: disable-next=protected-access
        controller._executor = executor
        executor.register(controller)
    return executor


def computer_for_controller(controller):
    computer = object.__new__(Computer)
    computer._controller, computer._apps = controller, weakref.WeakValueDictionary()
    # This factory initializes the same private handle state as Computer.__init__.
    # pylint: disable-next=protected-access
    computer._executor = executor_for_controller(controller)
    return computer
