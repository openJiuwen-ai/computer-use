# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Human intent: a desktop, an application, a window, or a control."""

from __future__ import annotations

import weakref

from computer_use.diagnostics import local_failure
from computer_use.interaction.agent.requests import (
    ActOnElement,
    DiscoverApps,
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
from computer_use.protocol.validation import validate_key, validate_paste_text, validate_text
from computer_use.session.operations import operation

from . import parameters
from .execution import on_session, session_executor, synchronous
from .handles import handle_created
from .targets import Target, as_target, element_target, image_target, target_request, window_target


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


def _key(key):
    try:
        validate_key(key)
    except Exception as error:
        raise local_failure(str(error)) from error


class Computer:
    def __init__(self, *, allow_global_input=True):
        """Create a session. On macOS only direct Computer input methods use global events."""
        from .factory import session_controller

        if type(allow_global_input) is not bool:
            raise TypeError("allow_global_input must be a boolean")
        self._executor = session_executor()

        def create():
            controller = session_controller(allow_global_input=allow_global_input)
            controller._executor = self._executor
            self._executor.register(controller)
            return controller

        try:
            self._controller = self._executor.call(create)
        except BaseException:
            if not self._executor._external:
                self._executor.close()
            raise
        self._apps = weakref.WeakValueDictionary()

    def close(self):
        if self._executor._closed:
            return
        try:
            self._executor.run(lambda: self._executor.close_resource(self._controller))
        finally:
            if not self._executor._external:
                self._executor.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    @on_session
    def app(self, selector):
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
        if app is None:
            app = App(self._controller, scope, _internal=True)
            self._apps[key] = app
        return app

    @on_session
    def window(self, ref):
        """Select a published concrete Window without changing desktop focus."""
        parameters.reference(ref, "window reference")
        return self._controller.interfaces.window(None, ref)

    @synchronous
    def list_apps(self):
        """Return application choices with id, display_name, and is_running fields."""
        return operation(lambda: self._controller.run(DiscoverApps()))

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
        _key(key)
        return self._global_input("key", {"key": key})

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


class UI:
    def __init__(self, controller=None, scope=None, *, _internal=False):
        if not _internal:
            raise TypeError("UI requires an application or window binding")
        self._controller = controller
        self._executor = executor_for_controller(controller)
        self._lookup_scopes = ()
        self._menu_only = False
        self._context = None
        self._input_context = None
        self._lookup_context = None
        self._delivered_generation = 0
        controller.register(self, scope)

    @synchronous
    def element(self, target, *, timeout_ms=3000):
        # pylint: disable-next=unidiomatic-typecheck
        if type(timeout_ms) is not int or not 0 <= timeout_ms <= 10000:
            raise local_failure("timeout_ms must be an integer in 0-10000")
        # pylint: disable-next=unidiomatic-typecheck
        if type(target) is int:
            parameters.reference(target, "element reference")

        async def resolve():
            snapshot = await operation(lambda: self._controller.run(ResolveElement(self, target, timeout_ms)))
            return Element(self._controller, self, snapshot, _internal=True)

        return resolve()

    @synchronous
    def read(self, *, view="tree", full=False, menu_path=None, allow_foreground=False):
        parameters.view(view, full, menu_path)
        if type(allow_foreground) is not bool:
            raise local_failure("allow_foreground must be a boolean")
        return operation(lambda: self._controller.run(ObserveUI(self, view, full, menu_path, allow_foreground)))

    @synchronous
    def click(self, options=None, *, at):
        if isinstance(at, Target):
            return self._point_action(at, "click", parameters.click(options))
        click, at = parameters.click(options), parameters.point(at)
        return operation(lambda: self._controller.run(UIPointer(self, "click", (at,), click)))

    @synchronous
    def scroll(self, direction, *, at, pages=1):
        parameters.scroll(direction, pages)
        if isinstance(at, Target):
            return self._point_action(at, "scroll", {"direction": direction, "pages": pages})
        at = parameters.point(at)
        return operation(
            lambda: self._controller.run(UIPointer(self, "scroll", (at,), dict(direction=direction, pages=pages)))
        )

    @synchronous
    def drag(self, start, end):
        if isinstance(start, Target) or isinstance(end, Target):
            return self._point_action(start, "drag", {}, end=end)
        start, end = parameters.point(start), parameters.point(end)
        if start == end:
            raise local_failure("drag start and end must differ")
        return operation(lambda: self._controller.run(UIPointer(self, "drag", (start, end), {})))

    @on_session
    def point(self, at):
        return image_target(self._controller, self, parameters.point(at))

    def _point_action(self, target, intent, args, *, end=None):
        return operation(
            lambda: self._controller.run(target_request(self._controller, target, intent, args, owner=self, end=end))
        )

    @synchronous
    def type_text(self, text):
        _text(text)
        return operation(lambda: self._controller.run(UIKeyboard(self, "type_text", dict(text=text))))

    @synchronous
    def paste(self, text):
        _text(text, paste=True)
        return operation(lambda: self._controller.run(UIKeyboard(self, "paste", dict(text=text))))

    @synchronous
    def press_key(self, key):
        _key(key)
        return operation(lambda: self._controller.run(UIKeyboard(self, "press_key", dict(key=key))))


class App(UI):
    def __init__(self, controller=None, scope=None, *, _internal=False):
        super().__init__(controller, scope, _internal=_internal)
        from computer_use.session.interfaces import InstanceState

        running = controller.state(self).running
        self._instance = (
            controller.interfaces.windows.application(running.session, running.application, running.name)
            if running is not None
            else InstanceState(scope["selector"])
        )
        self._anchor, self._image = None, None
        handle_created(self, dict(kind="App", controller=controller, selector=scope["selector"]))

    @on_session
    def window(self, ref):
        parameters.reference(ref, "window reference")
        return self._controller.interfaces.window(self, ref)


class Window(UI):
    def __init__(self, controller=None, scope=None, *, _internal=False, _app=None, _anchor=None):
        super().__init__(controller, scope, _internal=_internal)
        self._app, self._instance, self._anchor, self._image = _app, _app._instance, _anchor, None
        self._window_identity = controller.state(self).selection.root
        handle_created(self, dict(kind="Window", controller=controller, owner=_app, selector=scope["selector"]))

    @property
    @on_session
    def target(self):
        return window_target(self)


class Element:
    def __init__(self, controller=None, owner=None, snapshot=None, *, _internal=False):
        if not _internal:
            raise TypeError("Element requires an App or Window element binding")
        self._controller, self._owner, self._snapshot = controller, owner, snapshot
        self._executor = executor_for_controller(controller)
        handle_created(self, dict(kind="Element", controller=controller, owner=owner, snapshot=snapshot, query=None))

    def _act(self, intent, args=None):
        return operation(lambda: self._controller.run(ActOnElement(self._owner, self._snapshot, intent, args or {})))

    @property
    @on_session
    def target(self):
        return element_target(self)

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
        _key(key)
        return self._act("key", dict(key=key))

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
        controller._executor = executor
        executor.register(controller)
    return executor


def computer_for_controller(controller):
    computer = object.__new__(Computer)
    computer._controller, computer._apps = controller, weakref.WeakValueDictionary()
    computer._executor = executor_for_controller(controller)
    return computer
