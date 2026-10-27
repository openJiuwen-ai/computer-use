"""Frozen protocol-3 adapter for historical parity/host fixtures only.

This is not part of the installed package and is never a fallback for Computer.
Current execution is exercised by native_* tests and actual desktop acceptance.
"""

import json
import weakref
from textwrap import dedent

from legacy_observation_store import ObservationStore
from repository import TESTS
from v5_handles import (
    App,
    Computer as Computer,
    Element as Element,
    computerForController as computerForController,
    observationState,
    selectWorkingWindow as selectWorkingWindow,
)

from computer_use._identity import WeakIdentityMap
from computer_use.client.images import imageDeliveryEpoch, imageDeliveryRequired, registerImage
from computer_use.client.result_presentation import actionResult, capturedText
from computer_use.diagnostics import localFailure
from computer_use.errors import ComputerUseError
from computer_use.interaction.read.presentation.formatting import jsonString
from computer_use.protocol.primitives import frozen
from computer_use.session.desktop_store import DesktopImageAccess, DesktopStore
from computer_use.session.events import (
    actionCompleted,
    observationCompleted,
    observationsInvalidated,
    pendingObservationsDiscarded,
)
from computer_use.session.observation_data import retainObservationData
from computer_use.session.operations import operation

_READS = {"readApp", "listApps", "readDesktop", "readElementText", "findElement"}


def legacySource(code):
    """Make an explicit historical fixture importable by a child test process."""
    return (
        "import sys\nsys.path.insert(0, " + repr(str(TESTS)) + ")\n"
        "from display_documents import install_fixture_renderer\ninstall_fixture_renderer()\n" + dedent(code)
    )


class TextSources:
    def __init__(self):
        self._sources = {}

    def clear(self):
        self._sources.clear()

    def record(self, refs, observed=None):
        for token in refs.values():
            self._sources.pop(token, None)
            if observed is not None:
                self._sources[token] = observed
        while len(self._sources) > 16384:
            self._sources.pop(next(iter(self._sources)))

    def get(self, token, ref):
        result = dict(ref=ref)
        if token in self._sources:
            result["observed"] = self._sources[token]
        return result


def windowKey(scope):
    return json.dumps([scope["selector"], scope.get("window")], ensure_ascii=True, separators=(",", ":"))


def rejectedRead(error):
    return isinstance(error, ComputerUseError) and error.code == "invalid_request" and error.effect == "none"


class Controller:
    def __init__(self, factory=None):
        if factory is None:
            from legacy_connection import Connection

            factory = Connection
        self.observations, self.textSources, self.desktop = (
            ObservationStore(),
            TextSources(),
            DesktopStore(DesktopImageAccess(registerImage, imageDeliveryEpoch, imageDeliveryRequired)),
        )
        self._scopes, self._workingSelections = WeakIdentityMap(), WeakIdentityMap()
        self._boundApps = weakref.WeakValueDictionary()
        self._reading = self._busy = False
        self.transport = factory(self.invalidate)

    def register(self, owner, scope):
        self._scopes.set(owner, scope)
        self.observations.retain(owner, scope)

    def scope(self, owner):
        scope = self._scopes.get(owner)
        self.observations.validate(scope)
        return scope

    def window(self, owner, ref):
        scope = self.observations.bind(self.scope(owner), ref)
        if scope is self.scope(owner):
            return owner
        key = windowKey(scope)
        app = self._boundApps.get(key)
        if app is None:
            app = App(self, scope, _internal=True)
            self._boundApps[key] = app
            self.observations.shareSelectedView(owner, app, scope)
        return app

    def workingWindow(self, owner, ref):
        scope = self.observations.bind(self.scope(owner), ref)
        app = App(self, scope, _internal=True)
        self._workingSelections.set(app, True)
        self.observations.shareSelectedView(owner, app, scope)
        return app

    def invalidate(self):
        self.observations.reset()
        self.textSources.clear()
        self.desktop.reset()
        self._boundApps.clear()
        observationsInvalidated(self)

    async def call(self, method, params, owner=None, options=None):
        return await operation(lambda: self._call(method, params, owner, options))

    async def _call(self, method, params, owner, options):
        if self._busy:
            raise localFailure("another system call is in progress", "await it before starting the next")
        self._busy = True
        try:
            result = await (
                self.transport.request(method, params, options)
                if options is not None
                else self.transport.request(method, params)
            )
            if method not in _READS:
                actionCompleted(result)
            return result
        except Exception as error:
            if (
                owner is not None
                and isinstance(error, ComputerUseError)
                and error.code == "snapshot_expired"
                and method in ("click", "scroll", "drag")
                and "appRef" in params
            ):
                self.observations.expireCapture(owner, params["appRef"])
            if (
                owner is not None
                and method not in ("readElementText", "findElement")
                and not (method == "readApp" and rejectedRead(error))
            ):
                if method not in _READS and isinstance(error, ComputerUseError):
                    pendingObservationsDiscarded(self)
                else:
                    observationsInvalidated(self)
            raise
        finally:
            self._busy = False

    async def act(self, method, params, owner=None):
        return actionResult(await self.call(method, params, owner))

    async def read(self, owner, full=False, menuPath=None, subtree=None):
        return await operation(lambda: self._read(owner, full, menuPath, subtree))

    async def readDesktop(self, full=False):
        return await operation(lambda: self._read_desktop(full))

    async def _read_desktop(self, full):
        if self._busy or self._reading:
            raise localFailure("another system call is in progress", "await it before starting the next")
        self._reading = True
        try:
            observation = await self.call("readDesktop", {})
            self.desktop.record(observation)
            state = observationState(
                dict(text=observation["text"], screenshot=observation["screenshot"], overview=None)
            )
            observationCompleted(
                dict(
                    source=self,
                    scope=observation["scope"],
                    observed=observation.get("observed"),
                    view="desktop",
                    state=state,
                    refs=frozen({}),
                    full=full,
                )
            )
            return state
        except Exception:
            self.desktop.reset()
            raise
        finally:
            self._reading = False

    async def _read(self, owner, full, menuPath, subtree):
        if self._busy or self._reading:
            raise localFailure("another system call is in progress", "await it before starting the next")
        scope = self.scope(owner)
        selector = scope["selector"]
        selecting = self._workingSelections.has(owner)
        window = None if subtree is not None else scope.get("window", {}).get("ref")
        self._reading = True
        try:
            params = dict(selector=selector)
            if menuPath is not None:
                params["menuPath"] = menuPath
            if window is not None:
                params["window"] = window
            if subtree is not None:
                params.update(subtree)
            observation = await self.call("readApp", params, owner)
            if "window" in scope and (
                observation["scope"] != scope["window"]["scope"] or observation["window"] != scope["window"]["ref"]
            ):
                raise localFailure(
                    "backend returned a different window for a bound App",
                    "read the intended App again",
                    "execution_failed",
                    selector,
                )
            scope = dict(
                selector=observation["instance"],
                **(
                    {"window": {**scope["window"], "instance": observation["instance"]}}
                    if "window" in scope and not selecting
                    else {}
                ),
            )
            self._scopes.set(owner, scope)
            self.observations.record(owner, scope, observation)
            if selecting:
                self._workingSelections.delete(owner)
            self.textSources.record(observation["refs"], observation.get("observed"))
            if "window" not in scope and not selecting:
                self.observations.alias(selector, observation["instance"])
            bound_scope = (
                self.observations.bind(scope, observation["window"]) if observation["window"] is not None else None
            )
            key = windowKey(bound_scope) if bound_scope is not None else None
            controller_ref, owner_ref = weakref.ref(self), weakref.ref(owner)

            def imageOwners():
                controller, original = controller_ref(), owner_ref()
                bound = controller._boundApps.get(key) if controller is not None and key is not None else None
                return (
                    [
                        candidate
                        for candidate in (original, bound)
                        if candidate is not None and (candidate is not bound or bound is not original)
                    ]
                    if bound is not original
                    else ([original] if original is not None else [])
                )

            for bound in imageOwners():
                if bound is not owner:
                    self.observations.shareView(owner, bound, bound_scope)
            if (png := observation.get("screenshot")) is not None:
                lines = observation["text"].split("\n")
                context = observation.get("imageContext") or "\n".join(
                    line
                    for line in [
                        next(
                            (text_line for text_line in lines if text_line.startswith("Window:")),
                            "App selector: " + jsonString(selector),
                        ),
                        next((line_local for line_local in lines if line_local.startswith("Document: ")), None),
                    ]
                    if line is not None
                )
                image = weakref.ref(png)

                def current():
                    controller, pixels = controller_ref(), image()
                    return (
                        controller is not None
                        and pixels is not None
                        and any(controller.observations.imageIsCurrent(handle, pixels) for handle in imageOwners())
                    )

                def delivered():
                    controller, pixels = controller_ref(), image()
                    if controller is not None and pixels is not None:
                        for handle in imageOwners():
                            controller.observations.imageDelivered(handle, pixels)

                registerImage(
                    png,
                    dict(
                        kind="window",
                        selector=selector,
                        context=context,
                        window=observation["window"],
                        observed=observation.get("observed"),
                        current=current,
                        delivered=delivered,
                    ),
                )
            if (overview := observation.get("overview")) is not None:
                registerImage(
                    overview,
                    dict(
                        kind="overview",
                        selector=selector,
                        context="\n".join(
                            line
                            for line in observation["text"].split("\n")
                            if line.startswith(("Windows:", "Overview windows"))
                        ),
                    ),
                )
            state = observationState(
                dict(
                    text=observation["text"], screenshot=observation["screenshot"], overview=observation.get("overview")
                )
            )
            retainObservationData(
                state,
                observation.get("roots", []),
                list(observation["refs"]),
                list(observation["windows"]),
                observation["instance"],
                observation.get("identity"),
            )
            event = dict(
                source=self,
                owner=owner,
                scope=observation["scope"],
                instance=observation["instance"],
                selector=scope["selector"],
                window=observation["window"],
                view="subtree" if subtree is not None else "menu" if menuPath is not None else "window",
                state=state,
                refs=frozen(observation["refs"]),
                full=full,
            )
            for name in ("observed", "coverage", "contextRefs"):
                if name in observation:
                    event[name] = observation[name]
            from display_documents import with_document

            event["document"] = with_document(
                dict(text=state["text"], refs=event["refs"], contextRefs=event.get("contextRefs", {}))
            )["document"]
            observationCompleted(event)
            return state
        except Exception as error:
            if not rejectedRead(error):
                self.observations.invalidate(scope)
            raise
        finally:
            self._reading = False

    async def readElement(self, owner, snapshot, query, offset, previous=None, *, cursor=None):
        if cursor is not None:
            raise localFailure(
                "this driver does not support content continuations",
                "use an offset printed by this driver or use a v5 session",
                "mode_unsupported",
            )
        selected = await self.find(owner, query["path"], query["timeoutMs"], False) if query is not None else snapshot
        binding = selected["binding"]
        self.observations.validateElement(binding)
        return await self.read(owner, True, subtree=dict(elementRef=binding["token"], offset=offset)), selected

    async def find(self, owner, path, timeoutMs, enabled):
        result = await self.call(
            "findElement", dict(appRef=self.appRef(owner), path=path, timeoutMs=timeoutMs, enabled=enabled), owner
        )
        binding = self.observations.found(owner, self.scope(owner), result)
        self.textSources.record({result["ref"]: result["token"]}, result.get("observed"))
        return dict(binding=binding, source=dict(ref=result["ref"], observed=result.get("observed")))

    def elementToken(self, owner, ref):
        return self.observations.element(self.scope(owner), ref)

    def elementSnapshot(self, owner, ref):
        binding = self.elementToken(owner, ref)
        return dict(binding=binding, source=self.textSources.get(binding["token"], ref))

    def validateElement(self, binding):
        self.observations.validateElement(binding)

    async def readText(self, owner, snapshot, options):
        binding = snapshot["binding"]
        self.observations.validateSnapshot(binding)
        return capturedText(
            await self.call(
                "readElementText",
                dict(ref=binding["token"], offset=options.get("offset", 0), length=options.get("length", 4096)),
                owner,
            ),
            snapshot["source"],
        )

    async def actElement(self, owner, snapshot, query, intent, parameters):
        """Legacy session adapter; v5 resolves a path inside Engine's operation."""

        async def perform():
            selected = (
                await self.find(owner, query["path"], query["timeoutMs"], True) if query is not None else snapshot
            )
            binding = selected["binding"]
            self.observations.validateElement(binding)
            methods = dict(
                click="clickElement",
                focus="focusElement",
                openMenu="openElementMenu",
                action="performElementAction",
                key="pressKeyElement",
                scroll="scroll",
                paste="pasteElement",
                insert="typeTextElement",
                replace="typeTextElement",
            )
            params = dict(ref=binding["token"], **parameters)
            if intent in ("insert", "replace"):
                params["replace"] = intent == "replace"
            return await self.act(methods[intent], params, owner)

        return await operation(perform)

    def appRef(self, owner, coordinates=False):
        return self.observations.appRef(owner, self.scope(owner), coordinates)

    def keyboardTarget(self, owner):
        result = dict(appRef=self.appRef(owner))
        if "window" in self.scope(owner):
            result["window"] = self.scope(owner)["window"]["ref"]
        return result

    def checkCoordinates(self, owner, x, y, operation="click"):
        self.observations.checkCoordinates(owner, self.scope(owner), x, y, operation)


def appForController(controller, selector):
    return App(controller, dict(selector=selector), _internal=True)
