"""Human intent: a desktop, an application instance, a window, or a control."""

from __future__ import annotations

import weakref

from computer_use._identity import FrozenRecord
from computer_use.diagnostics import localFailure
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
from computer_use.protocol.validation import validateKey, validatePasteText, validateText
from computer_use.session.operations import operation

from . import parameters
from .handles import handleCreated


def observationState(state=None, *, document=None):
    if document is not None:
        from computer_use.session.observation import lazyObservation

        return lazyObservation(document, (state or {}).get("screenshot"))
    return FrozenRecord(state, display=state["text"])


def _text(text, paste=False):
    try:
        (validatePasteText if paste else validateText)(text)
    except Exception as error:
        raise localFailure(
            str(error), "use paste for multiline text; send control keys separately with pressKey"
        ) from error


def _key(key):
    try:
        validateKey(key)
    except Exception as error:
        raise localFailure(str(error), "use a documented key name or modifier chord") from error


class Computer:
    def __init__(self):
        from .factory import sessionController

        self._controller, self._apps = sessionController(), weakref.WeakValueDictionary()

    def app(self, selector):
        if type(selector) is int:
            parameters.reference(selector, "application reference")
            scope, key = self._controller.interfaces.application(selector), ("reference", selector)
        elif isinstance(selector, str):
            try:
                nonempty(selector, "selector")
            except Exception as error:
                raise localFailure(
                    str(error), "use an app name or a published integer application reference"
                ) from error
            scope, key = dict(selector=selector), ("selector", selector)
        else:
            raise localFailure(
                "app expects an integer reference or application selector string",
                "use listApps() to discover application instances",
            )
        app = self._apps.get(key)
        if app is None:
            app = App(self._controller, scope, _internal=True)
            self._apps[key] = app
        return app

    async def listApps(self):
        return await operation(lambda: self._controller.run(DiscoverApps()))

    async def read(self, *, view="both", full=False):
        parameters.view(view, full)
        return await operation(lambda: self._controller.run(ObserveDesktop(view, full)))

    async def click(self, options=None, *, at):
        click, (x, y) = parameters.click(options), parameters.point(at)
        self._controller.desktop.checkCoordinates(x, y)
        return await self._controller.act(
            "click", dict(desktopRef=self._controller.desktop.target(True), x=x, y=y, **click)
        )

    async def scroll(self, direction, *, at, pages=1):
        parameters.scroll(direction, pages)
        x, y = parameters.point(at)
        self._controller.desktop.checkCoordinates(x, y)
        return await self._controller.act(
            "scroll", dict(desktopRef=self._controller.desktop.target(True), x=x, y=y, direction=direction, pages=pages)
        )

    async def drag(self, start, end):
        start, end = parameters.point(start), parameters.point(end)
        if start == end:
            raise localFailure("drag start and end must differ", "use click for a stationary gesture")
        for x, y in (start, end):
            self._controller.desktop.checkCoordinates(x, y)
        return await self._controller.act(
            "drag",
            dict(
                desktopRef=self._controller.desktop.target(True), fromX=start[0], fromY=start[1], toX=end[0], toY=end[1]
            ),
        )

    async def typeText(self, text):
        _text(text)
        return await self._controller.act("typeText", dict(desktopRef=self._controller.desktop.target(), text=text))

    async def pressKey(self, key):
        _key(key)
        return await self._controller.act("pressKey", dict(desktopRef=self._controller.desktop.target(), key=key))


class UI:
    def __init__(self, controller=None, scope=None, *, _internal=False):
        if not _internal:
            raise TypeError("Use Computer.app() or App.window(); UI is not directly constructible")
        self._controller = controller
        self._lookup_scopes = ()
        self._menu_only = False
        controller.register(self, scope)

    async def element(self, target, *, timeoutMs=3000):
        if type(timeoutMs) is not int or not 0 <= timeoutMs <= 10000:
            raise localFailure("timeoutMs must be an integer in 0-10000", "omit it to use 3000 ms")
        if type(target) is int:
            parameters.reference(target, "element reference")
        snapshot = await operation(lambda: self._controller.run(ResolveElement(self, target, timeoutMs)))
        return Element(self._controller, self, snapshot, _internal=True)

    async def read(self, *, view="tree", full=False, menuPath=None):
        parameters.view(view, full, menuPath)
        return await operation(lambda: self._controller.run(ObserveUI(self, view, full, menuPath)))

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
            raise localFailure("drag start and end must differ", "use click for a stationary gesture")
        return await operation(lambda: self._controller.run(UIPointer(self, "drag", (start, end), {})))

    async def typeText(self, text):
        _text(text)
        return await operation(lambda: self._controller.run(UIKeyboard(self, "typeText", dict(text=text))))

    async def paste(self, text):
        _text(text, paste=True)
        return await operation(lambda: self._controller.run(UIKeyboard(self, "paste", dict(text=text))))

    async def pressKey(self, key):
        _key(key)
        return await operation(lambda: self._controller.run(UIKeyboard(self, "pressKey", dict(key=key))))


class App(UI):
    def __init__(self, controller=None, scope=None, *, _internal=False):
        super().__init__(controller, scope, _internal=_internal)
        from computer_use.session.interfaces import InstanceState

        self._instance = InstanceState(scope["selector"])
        self._anchor, self._image = None, None
        handleCreated(self, dict(kind="App", controller=controller, selector=scope["selector"]))

    def window(self, ref):
        parameters.reference(ref, "window reference")
        return self._controller.interfaces.window(self, ref)


class Window(UI):
    def __init__(self, controller=None, scope=None, *, _internal=False, _app=None, _anchor=None):
        super().__init__(controller, scope, _internal=_internal)
        self._app, self._instance, self._anchor, self._image = _app, _app._instance, _anchor, None
        self._window_identity = controller.state(self).selection.root
        handleCreated(self, dict(kind="Window", controller=controller, owner=_app, selector=scope["selector"]))


class Element:
    def __init__(self, controller=None, owner=None, snapshot=None, *, _internal=False):
        if not _internal:
            raise TypeError("Use await app.element(target) or await window.element(target)")
        self._controller, self._owner, self._snapshot = controller, owner, snapshot
        handleCreated(self, dict(kind="Element", controller=controller, owner=owner, snapshot=snapshot, query=None))

    async def _act(self, intent, args=None):
        return await operation(
            lambda: self._controller.run(ActOnElement(self._owner, self._snapshot, intent, args or {}))
        )

    async def read(self, *, cursor=None):
        parameters.cursor(cursor)
        return await operation(lambda: self._controller.run(ObserveElement(self._owner, self._snapshot, cursor)))

    async def readText(self, *, cursor=None, length=4096):
        parameters.cursor(cursor)
        if type(length) is not int or not 1 <= length <= 16384:
            raise localFailure(
                "length must be an integer in 1-16384 code points", "use readText(length=4096) or a smaller page"
            )
        return await operation(lambda: self._controller.run(ObserveText(self._owner, self._snapshot, cursor, length)))

    async def click(self, options=None):
        return await self._act("click", parameters.click(options))

    async def scroll(self, direction, *, pages=1):
        parameters.scroll(direction, pages)
        return await self._act("scroll", dict(direction=direction, pages=pages))

    async def typeText(self, text, *, replace=False):
        _text(text)
        if type(replace) is not bool:
            raise localFailure("replace must be a bool", "use replace=True only for full control replacement")
        return await self._act("replace" if replace else "insert", dict(text=text))

    async def paste(self, text):
        _text(text, paste=True)
        return await self._act("paste", dict(text=text))

    async def pressKey(self, key):
        _key(key)
        return await self._act("key", dict(key=key))

    async def focus(self):
        return await self._act("focus")

    async def openMenu(self):
        return await self._act("openMenu")

    async def performAction(self, action):
        try:
            nonempty(action, "action")
        except Exception as error:
            raise localFailure(str(error), "copy a Secondary Actions name from this element") from error
        return await self._act("action", dict(action=action))


def computerForController(controller):
    computer = object.__new__(Computer)
    computer._controller, computer._apps = controller, weakref.WeakValueDictionary()
    return computer
