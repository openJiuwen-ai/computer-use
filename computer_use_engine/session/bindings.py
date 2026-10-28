"""One owner for App identity, explicit window catalogues and observed references.

This store never discovers windows, chooses a current modal or constructs public
App wrappers. The interaction controller performs that work and records results.
"""

import weakref
from collections import OrderedDict

from ..identity import WeakIdentityMap
from ..diagnostics import local_failure
from ..native.contracts import Binding
from ..protocol.primitives import reference
from .state import AppState


class SessionBindings:
    def __init__(self):
        self._states = WeakIdentityMap()
        self._bound_apps = weakref.WeakValueDictionary()
        self._window_catalogues = OrderedDict()

    def register(self, owner, scope):
        self._states.set(owner, scope.get("_nativeState") or AppState(scope["selector"]))

    def state(self, owner):
        state = self._states.get(owner)
        if state is None:
            raise local_failure("unknown App", "obtain an App from this Computer", "stale_target")
        return state

    def scope(self, owner):
        state = self.state(owner)
        # Clearing a cache must not change an already-bound App into a lookup.
        return dict(
            selector=state.selection.root if state.selection is not None else state.application or state.selector
        )

    def invalidate(self):
        self._bound_apps.clear()
        self._window_catalogues.clear()
        # Existing bindings retain their original session and fail as before.

    @staticmethod
    def validate_element(target, session):
        if not isinstance(target, Binding) or target.session != session:
            raise local_failure(
                "element belongs to an expired driver session",
                "observe a new App and obtain a new element",
                "stale_target",
            )

    validateSnapshot = validate_element

    def catalogue(self, state, directory, windows):
        state.directory, state.windows = directory, dict(windows)
        key = (directory["session"], state.application)
        self._window_catalogues.pop(key, None)
        self._window_catalogues[key] = (state.running, directory, dict(windows))
        while len(self._window_catalogues) > 32:
            self._window_catalogues.popitem(last=False)

    def window_catalogue(self, state, ref, session):
        if not reference(ref):
            raise local_failure(
                "window is not in this App's observed catalogue",
                "read the App and select a decimal reference from Windows",
                "stale_target",
            )
        if state.directory is not None and state.directory["session"] != session:
            raise local_failure(
                "window catalogue belongs to an expired driver session", "observe a new App", "stale_target"
            )
        cached = self._window_catalogues.get((session, state.application or state.selector))
        if cached is not None:
            running, directory, windows = cached
        elif state.directory is not None:
            running, directory, windows = state.running, state.directory, state.windows
        else:
            raise local_failure(
                "window is not in this App's observed catalogue",
                "read the App or use its exact already-observed running application identity",
                "stale_target",
            )
        if ref not in windows:
            raise local_failure(
                "window is not in this application's observed catalogue",
                "read the intended App and select a decimal reference from Windows",
                "stale_target",
            )
        return running, directory, windows

    def bound_app(self, binding):
        return self._bound_apps.get(binding)

    def bind_app(self, binding, app):
        self._bound_apps[binding] = app

    @staticmethod
    def remember(state, snapshots):
        state.refs.update(snapshots)
        while len(state.refs) > 8192:
            state.refs.popitem(last=False)

    def element_snapshot(self, owner, ref, session):
        state = self.state(owner)
        if not reference(ref) or ref not in state.refs:
            raise local_failure(
                "element reference was not observed in this App",
                "read the intended App and use one of its element numbers",
                "stale_target",
            )
        selected = state.refs[ref]
        self.validate_element(selected["binding"], session)
        return selected
