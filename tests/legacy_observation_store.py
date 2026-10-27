import hashlib
import weakref

from computer_use._identity import Record, WeakIdentityMap
from computer_use.client.images import imageDeliveryEpoch, imageDeliveryRequired, pngSize
from computer_use.diagnostics import localFailure
from computer_use.interaction.read.presentation.formatting import numberString
from computer_use.protocol.primitives import reference

"Protocol-3 observation cache, retained only for historical comparison tests."


def observedRefs(previous, current):
    refs = dict(previous["refs"]) if previous is not None else {}
    for ref, token in current.items():
        refs.pop(ref, None)
        refs[ref] = token
    while len(refs) > 8192:
        refs.pop(next(iter(refs)))
    return refs


def imageHash(image):
    return hashlib.sha256(image).hexdigest()


class ObservationStore:
    def __init__(self):
        self._epoch = -1
        self._generation = 0
        self.reset()

    def reset(self):
        self._epoch += 1
        self._instances, self._selectors = weakref.WeakValueDictionary(), weakref.WeakValueDictionary()
        self._recent = {}
        self._owners, self._views, self._delivered, self._offered = (WeakIdentityMap() for _ in range(4))
        self._latestScope, self._expiredCaptures = WeakIdentityMap(), WeakIdentityMap()

    def _instance(self, scope):
        return (
            scope["window"]["instance"]
            if "window" in scope
            else getattr(self._selectors.get(scope["selector"]), "id", None)
        )

    def _window(self, instance, scope):
        state = self._instances.get(instance) if instance is not None else None
        return state["windows"].get(scope) if state is not None and scope is not None else None

    def alias(self, selector, instance):
        state = self._instances.get(instance)
        if state is not None:
            self._selectors[selector] = state

    def retain(self, owner, scope):
        instance = self._instance(scope)
        state = self._instances.get(instance) if instance is not None else None
        if state is not None:
            self._owners.set(owner, state)

    def invalidate(self, target):
        instance = self._instance(target)
        if instance is None:
            return
        if "window" in target:
            state = self._instances.get(instance)
            if state is not None:
                state["windows"].pop(target["window"]["scope"], None)
        else:
            self._instances.pop(instance, None)
            self._recent.pop(instance, None)

    def validate(self, scope):
        window = scope.get("window")
        if window is not None and window["epoch"] != self._epoch:
            raise localFailure(
                "window belongs to an expired desktop session",
                "read the application and select a current window",
                "stale_target",
                scope["selector"],
            )
        current = getattr(self._selectors.get(scope["selector"]), "id", None)
        if window is not None and current is not None and current != window["instance"]:
            raise localFailure(
                "window belongs to a previous application instance",
                "read the application and select a current window",
                "stale_target",
                scope["selector"],
            )

    def bind(self, scope, ref):
        self.validate(scope)
        if not reference(ref):
            raise localFailure(
                "window expects an observed decimal reference", "use a window reference from this App's observation"
            )
        if scope.get("window", {}).get("ref") == ref:
            return scope
        instance = self._instance(scope)
        known = self._instances.get(instance) if instance is not None else None
        if known is None or ref not in known["catalog"]:
            raise localFailure(
                f"window {ref} is not in this App's observed window catalog",
                "read the application and select a listed window",
                "stale_target",
                scope["selector"],
            )
        native_scope = known["catalog"][ref]
        if native_scope not in known["windows"] and "catalogScope" in known:
            observed = [
                candidate["windows"][native_scope]
                for candidate in list(self._instances.values())
                if candidate.get("catalogScope") == known["catalogScope"]
                and candidate["catalog"].get(ref) == native_scope
                and native_scope in candidate["windows"]
            ]
            if observed:
                view = max(observed, key=lambda item: item["generation"])
                return dict(
                    selector=view["selector"],
                    window=dict(ref=ref, scope=native_scope, instance=view["instance"], epoch=self._epoch),
                )
        return dict(
            selector=scope["selector"], window=dict(ref=ref, scope=native_scope, instance=instance, epoch=self._epoch)
        )

    def record(self, owner, scope, observation):
        self.validate(scope)
        if "window" in scope and (
            observation["instance"] != scope["window"]["instance"]
            or observation["scope"] != scope["window"]["scope"]
            or observation["window"] != scope["window"]["ref"]
        ):
            raise localFailure(
                "backend returned a different window for a bound App",
                "read the application to inspect current windows",
                "execution_failed",
                scope["selector"],
            )
        previous_instance = getattr(self._selectors.get(scope["selector"]), "id", None)
        if previous_instance is not None and previous_instance != observation["instance"]:
            self._instances.pop(previous_instance, None)
            self._recent.pop(previous_instance, None)
        instance = self._instances.get(observation["instance"])
        if (
            instance is not None
            and "catalogScope" in instance
            and "catalogScope" in observation
            and instance["catalogScope"] != observation["catalogScope"]
        ):
            raise localFailure(
                "backend changed this App's window catalogue identity",
                "read the application again",
                "execution_failed",
                scope["selector"],
            )
        if instance is None:
            instance = Record(id=observation["instance"], catalog=observation["windows"], windows={})
            self._instances[observation["instance"]] = instance
        self._selectors[scope["selector"]] = instance
        self._owners.set(owner, instance)
        self._recent.pop(instance["id"], None)
        self._recent[instance["id"]] = instance
        while len(self._recent) > 64:
            self._recent.pop(next(iter(self._recent)))
        if "catalogScope" in observation:
            instance["catalogScope"] = observation["catalogScope"]
        live = set(observation["windows"].values())
        for known in list(self._instances.values()):
            if known is not instance and (
                "catalogScope" not in observation or known.get("catalogScope") != observation["catalogScope"]
            ):
                continue
            known["catalog"] = observation["windows"]
            for id_ in list(known["windows"]):
                if id_ not in live and not (known is instance and id_ == observation["scope"]):
                    known["windows"].pop(id_)
        previous_window = instance["windows"].get(observation["scope"])
        self._generation += 1
        window = Record(
            owner=weakref.ref(owner),
            selector=scope["selector"],
            instance=observation["instance"],
            scope=observation["scope"],
            generation=self._generation,
            appRef=observation["appRef"],
            refs=observedRefs(previous_window, observation["refs"]),
        )
        png = observation["screenshot"]
        state = Record(
            **window,
            image=png,
            imageHash=imageHash(png) if png is not None else None,
            imageSize=pngSize(png) if png is not None else None,
        )
        instance["windows"][observation["scope"]] = window
        self._latestScope.set(owner, observation["scope"])
        self._offered.set(owner, state)
        view, delivered = self._views.get(owner), self._delivered.get(owner)
        unchanged = (
            previous_window is not None
            and view is not None
            and delivered is not None
            and delivered["view"] is view
            and delivered["epoch"] == imageDeliveryEpoch()
            and not self._expiredCaptures.has(view)
            and view["instance"] == state["instance"]
            and view["scope"] == state["scope"]
            and view["image"] is not None
            and state["imageHash"] is not None
            and view["imageHash"] == state["imageHash"]
            and imageHash(view["image"]) == view["imageHash"]
        )
        if not unchanged:
            self._views.set(owner, state)

    def shareView(self, source, target, scope):
        self.validate(scope)

        def matches(view):
            return (
                view is not None
                and "window" in scope
                and view["instance"] == scope["window"]["instance"]
                and view["scope"] == scope["window"]["scope"]
                and view["selector"] == scope["selector"]
                and self._window(view["instance"], view["scope"]) is not None
            )

        offered = self._offered.get(source)
        if matches(offered):
            self._offered.set(target, offered)
            self._latestScope.set(target, offered["scope"])
        view = self._views.get(source)
        if not self._views.has(target) and matches(view):
            self._views.set(target, view)
            delivered = self._delivered.get(source)
            if delivered is not None and delivered["view"] is view:
                self._delivered.set(target, delivered)

    def shareSelectedView(self, source, target, scope):
        self.shareView(source, target, scope)
        if not self._offered.has(target) and "window" in scope:
            view = self._window(scope["window"]["instance"], scope["window"]["scope"])
            observer = view["owner"]() if view is not None else None
            if observer is not None:
                self.shareView(observer, target, scope)

    def imageIsCurrent(self, owner, png):
        return any(
            view is not None
            and not self._expiredCaptures.has(view)
            and view["image"] is png
            and getattr(self._selectors.get(view["selector"]), "id", None) == view["instance"]
            and self._window(view["instance"], view["scope"]) is not None
            for view in (self._views.get(owner), self._offered.get(owner))
        )

    def expireCapture(self, owner, appRef):
        for view in (self._views.get(owner), self._offered.get(owner)):
            if view is not None and view["appRef"] == appRef:
                self._expiredCaptures.set(view, True)

    def imageDelivered(self, owner, png):
        view = next(
            (
                view
                for view in (self._offered.get(owner), self._views.get(owner))
                if view is not None and view["image"] is png
            ),
            None,
        )
        if view is not None and self.imageIsCurrent(owner, png):
            self._views.set(owner, view)
            self._delivered.set(owner, dict(view=view, epoch=imageDeliveryEpoch()))

    def element(self, scope, ref):
        self.validate(scope)
        if not reference(ref):
            raise localFailure(
                "element expects an observed decimal reference", "use an element reference from this App's observation"
            )
        instance = self._instances.get(self._instance(scope))
        states = (
            [
                state
                for state in instance["windows"].values()
                if "window" not in scope or scope["window"]["scope"] == state["scope"]
            ]
            if instance is not None
            else []
        )
        states = [state for state in states if ref in state["refs"]]
        if not states:
            raise localFailure(
                f"element {ref} is absent from this App's valid window observations",
                "read its window and choose an observed element",
                "stale_target",
                scope["selector"],
            )
        state = max(states, key=lambda item: item["generation"])
        return dict(instance=state["instance"], token=state["refs"][ref], scope=state["scope"], epoch=self._epoch)

    def validateElement(self, binding):
        if binding["epoch"] != self._epoch:
            raise localFailure(
                "element belongs to an expired desktop session",
                "read the application and create a new Element",
                "stale_target",
                "Element",
            )
        if self._window(binding["instance"], binding["scope"]) is None:
            raise localFailure(
                "element window observation expired",
                "read its window before using this target",
                "stale_target",
                "Element",
            )

    def found(self, owner, scope, result):
        app_ref = self.appRef(owner, scope)
        instance = self._instance(scope)
        state = self._window(instance, scope["window"]["scope"] if "window" in scope else self._latestScope.get(owner))
        if state is None or result["appRef"] != app_ref or result["scope"] != state["scope"]:
            raise localFailure(
                "lookup returned a different window",
                "read the intended window again",
                "stale_target",
                scope["selector"],
            )
        self._instances[state["instance"]]["windows"][state["scope"]] = Record(
            {**state, "refs": observedRefs(state, {result["ref"]: result["token"]})}
        )
        return dict(instance=state["instance"], token=result["token"], scope=state["scope"], epoch=self._epoch)

    def validateSnapshot(self, binding):
        if binding["epoch"] != self._epoch:
            raise localFailure(
                "element snapshot and target belong to an expired desktop session",
                "read the application and create a new Element",
                "snapshot_expired",
                "Element",
            )

    def appRef(self, owner, scope, coordinates=False):
        self.validate(scope)
        view, instance = self._views.get(owner), self._instance(scope)
        state = (
            view
            if coordinates
            else self._window(instance, scope["window"]["scope"] if "window" in scope else self._latestScope.get(owner))
        )
        if coordinates and view is not None and self._expiredCaptures.has(view):
            raise localFailure(
                "window screenshot mapping expired",
                (
                    "read this App and return its current screenshot with printImage, then "
                    "choose coordinates from that "
                    "image"
                ),
                "snapshot_expired",
                scope["selector"],
            )
        if state is None or state["instance"] != instance or self._window(instance, state["scope"]) is None:
            raise localFailure(
                "application observation expired or missing",
                "read this App before sending input",
                "stale_target",
                scope["selector"],
            )
        return state["appRef"]

    def checkCoordinates(self, owner, scope, x, y, operation):
        self.appRef(owner, scope, True)
        view = self._views.get(owner)
        if view["imageSize"] is None:
            raise localFailure(
                f"{operation}: latest App.read has no window PNG",
                "read this App again; use its window PNG, not a desktop or preview image",
                "input_unavailable",
                scope["selector"],
            )
        delivered = self._delivered.get(owner)
        if imageDeliveryRequired() and (
            delivered is None or delivered["view"] is not view or delivered["epoch"] != imageDeliveryEpoch()
        ):
            raise localFailure(
                f"{operation}: this App's current window image has not been returned to the model",
                (
                    "printImage the screenshot from this App.read(), then choose coordinates in "
                    "the next tool call; desktop and overview images do not establish window "
                    "coordinates"
                ),
                "input_unavailable",
                scope["selector"],
            )
        width, height = view["imageSize"]["width"], view["imageSize"]["height"]
        if x >= width or y >= height:
            raise localFailure(
                (
                    f"{operation}: ({numberString(x)}, {numberString(y)}) is outside window PNG "
                    f"{width}x{height}; expected 0<=x<{width} and 0<=y<{height}"
                ),
                "use this App's latest window PNG",
            )
