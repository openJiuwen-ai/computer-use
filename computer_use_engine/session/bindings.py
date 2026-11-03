# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native per-handle state and the references owned by each observed source.

This store never discovers windows, chooses a current modal or constructs public
App wrappers. The interaction controller performs that work and records results.
"""

from ..identity import WeakIdentityMap
from ..diagnostics import local_failure
from ..native.contracts import Binding
from .state import AppState


class SessionBindings:
    def __init__(self):
        self._states = WeakIdentityMap()

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

    @staticmethod
    def validate_element(target, session):
        if not isinstance(target, Binding) or target.session != session:
            raise local_failure(
                "element belongs to an expired driver session",
                "observe a new App and obtain a new element",
                "stale_target",
            )

    @staticmethod
    def remember(state, snapshots):
        state.refs.update(snapshots)
        while len(state.refs) > 8192:
            state.refs.popitem(last=False)
