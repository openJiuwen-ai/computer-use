# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Human intent: a desktop, an application instance, a window, or a control."""

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
from computer_use.protocol.primitives import nonempty
from computer_use.protocol.validation import validate_key, validate_paste_text, validate_text
from computer_use.session.operations import operation

from . import parameters
from .handles import handle_created


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
        raise local_failure(
            str(error), "use paste for multiline text; send control keys separately with press_key"
        ) from error


def _key(key):
    try:
        validate_key(key)
    except Exception as error:
        raise local_failure(str(error), "use a documented key name or modifier chord") from error


class Computer:
    def __init__(self):
        from .factory import session_controller

        self._controller, self._apps = session_controller(), weakref.WeakValueDictionary()

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
                raise local_failure(
                    str(error), "use an app name or a published integer application reference"
                ) from error
            scope, key = dict(selector=selector), ("selector", selector)
        else:
            raise local_failure(
                "app expects an integer reference or application selector string",
                "use list_apps() to discover application instances",
            )
        app = self._apps.get(key)
        if app is None:
            app = App(self._controller, scope, _internal=True)
            self._apps[key] = app
        return app

    async def list_apps(self):
        """Return application choices with id, display_name, and is_running fields."""
        return await operation(lambda: self._controller.run(DiscoverApps()))

    async def read(self, *, view="both", full=False):
        parameters.view(view, full)
        return await operation(lambda: self._controller.run(ObserveDesktop(view, full)))

    async def click(self, options=None, *, at):
        click, (x, y) = parameters.click(options), parameters.point(at)
        self._controller.desktop.check_coordinates(x, y)
        return await self._controller.act(
            "click", dict(desktopRef=self._controller.desktop.target(True), x=x, y=y, **click)
        )

    async def scroll(self, direction, *, at, pages=1):
        parameters.scroll(direction, pages)
        x, y = parameters.point(at)
        self._controller.desktop.check_coordinates(x, y)
        return await self._controller.act(
            "scroll", dict(desktopRef=self._controller.desktop.target(True), x=x, y=y, direction=direction, pages=pages)
        )

    async def drag(self, start, end):
        start, end = parameters.point(start), parameters.point(end)
        if start == end:
            raise local_failure("drag start and end must differ", "use click for a stationary gesture")
        for x, y in (start, end):
            self._controller.desktop.check_coordinates(x, y)
        return await self._controller.act(
            "drag",
            dict(
                desktopRef=self._controller.desktop.target(True), fromX=start[0], fromY=start[1], toX=end[0], toY=end[1]
            ),
        )

    async def type_text(self, text):
        _text(text)
        return await self._controller.act("type_text", dict(desktopRef=self._controller.desktop.target(), text=text))

    async def press_key(self, key):
        _key(key)
        return await self._controller.act("press_key", dict(desktopRef=self._controller.desktop.target(), key=key))


class UI:
    def __init__(self, controller=None, scope=None, *, _internal=False):
        if not _internal:
            raise TypeError("Use Computer.app() or App.window(); UI is not directly constructible")
        self._controller = controller
        self._lookup_scopes = ()
        self._menu_only = False
        controller.register(self, scope)

    async def element(self, target, *, timeout_ms=3000):
        # pylint: disable-next=unidiomatic-typecheck
        if type(timeout_ms) is not int or not 0 <= timeout_ms <= 10000:
            raise local_failure("timeout_ms must be an integer in 0-10000", "omit it to use 3000 ms")
        # pylint: disable-next=unidiomatic-typecheck
        if type(target) is int:
            parameters.reference(target, "element reference")
        snapshot = await operation(lambda: self._controller.run(ResolveElement(self, target, timeout_ms)))
        return Element(self._controller, self, snapshot, _internal=True)

    async def read(self, *, view="tree", full=False, menu_path=None):
        parameters.view(view, full, menu_path)
        return await operation(lambda: self._controller.run(ObserveUI(self, view, full, menu_path)))

    async def click(self, options=None, *, at):
        click, at = parameters.click(options), parameters.point(at)
        return await operation(lambda: self._controller.run(UIPointer(self, "click", (at,), click)))

    async def scroll(self, direction, *, at, pages=1):
        parameters.scroll(direction, pages)
        at = parameters.point(at)
        return await operation(
            lambda: self._controller.run(UIPointer(self, "scroll", (at,), dict(direction=direction, pages=pages)))
        )

    async def drag(self, start, end):
        start, end = parameters.point(start), parameters.point(end)
        if start == end:
            raise local_failure("drag start and end must differ", "use click for a stationary gesture")
        return await operation(lambda: self._controller.run(UIPointer(self, "drag", (start, end), {})))

    async def type_text(self, text):
        _text(text)
        return await operation(lambda: self._controller.run(UIKeyboard(self, "type_text", dict(text=text))))

    async def paste(self, text):
        _text(text, paste=True)
        return await operation(lambda: self._controller.run(UIKeyboard(self, "paste", dict(text=text))))

    async def press_key(self, key):
        _key(key)
        return await operation(lambda: self._controller.run(UIKeyboard(self, "press_key", dict(key=key))))


class App(UI):
    def __init__(self, controller=None, scope=None, *, _internal=False):
        super().__init__(controller, scope, _internal=_internal)
        from computer_use.session.interfaces import InstanceState

        self._instance = InstanceState(scope["selector"])
        self._anchor, self._image = None, None
        handle_created(self, dict(kind="App", controller=controller, selector=scope["selector"]))

    def window(self, ref):
        parameters.reference(ref, "window reference")
        return self._controller.interfaces.window(self, ref)


class Window(UI):
    def __init__(self, controller=None, scope=None, *, _internal=False, _app=None, _anchor=None):
        super().__init__(controller, scope, _internal=_internal)
        self._app, self._instance, self._anchor, self._image = _app, _app._instance, _anchor, None
        self._window_identity = controller.state(self).selection.root
        handle_created(self, dict(kind="Window", controller=controller, owner=_app, selector=scope["selector"]))


class Element:
    def __init__(self, controller=None, owner=None, snapshot=None, *, _internal=False):
        if not _internal:
            raise TypeError("Use await app.element(target) or await window.element(target)")
        self._controller, self._owner, self._snapshot = controller, owner, snapshot
        handle_created(self, dict(kind="Element", controller=controller, owner=owner, snapshot=snapshot, query=None))

    async def _act(self, intent, args=None):
        return await operation(
            lambda: self._controller.run(ActOnElement(self._owner, self._snapshot, intent, args or {}))
        )

    async def read(self, *, cursor=None):
        parameters.cursor(cursor)
        return await operation(lambda: self._controller.run(ObserveElement(self._owner, self._snapshot, cursor)))

    async def read_text(self, *, cursor=None, length=4096):
        parameters.cursor(cursor)
        # pylint: disable-next=unidiomatic-typecheck
        if type(length) is not int or not 1 <= length <= 16384:
            raise local_failure(
                "length must be an integer in 1-16384 code points", "use read_text(length=4096) or a smaller page"
            )
        return await operation(lambda: self._controller.run(ObserveText(self._owner, self._snapshot, cursor, length)))

    async def click(self, options=None):
        return await self._act("click", parameters.click(options))

    async def scroll(self, direction, *, pages=1):
        parameters.scroll(direction, pages)
        return await self._act("scroll", dict(direction=direction, pages=pages))

    async def type_text(self, text, *, replace=False):
        _text(text)
        # pylint: disable-next=unidiomatic-typecheck
        if type(replace) is not bool:
            raise local_failure("replace must be a bool", "use replace=True only for full control replacement")
        return await self._act("replace" if replace else "insert", dict(text=text))

    async def paste(self, text):
        _text(text, paste=True)
        return await self._act("paste", dict(text=text))

    async def press_key(self, key):
        _key(key)
        return await self._act("key", dict(key=key))

    async def focus(self):
        return await self._act("focus")

    async def open_menu(self):
        return await self._act("openMenu")

    async def perform_action(self, action):
        try:
            nonempty(action, "action")
        except Exception as error:
            raise local_failure(str(error), "copy a Secondary Actions name from this element") from error
        return await self._act("action", dict(action=action))


def computer_for_controller(controller):
    computer = object.__new__(Computer)
    computer._controller, computer._apps = controller, weakref.WeakValueDictionary()
    return computer
