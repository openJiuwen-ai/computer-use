# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native per-handle state and the references owned by each observed source.

This store never discovers windows, chooses a current modal or constructs public
App wrappers. The interaction controller performs that work and records results.
"""

from collections import OrderedDict

from ..diagnostics import local_failure
from ..identity import WeakIdentityMap
from ..native.contracts import Binding
from .state import AppState


class SessionBindings:
    def __init__(self):
        self._states = WeakIdentityMap()
        self._content = WeakIdentityMap()

    def register(self, owner, scope):
        self._states.set(owner, scope.get("_nativeState") or AppState(scope["selector"]))

    def state(self, owner):
        state = self._states.get(owner)
        if state is None:
            raise local_failure("unknown App", "stale_target")
        return state

    def scope(self, owner):
        state = self.state(owner)
        # Clearing a cache must not change an already-bound App into a lookup.
        return {
            "selector": state.selection.root if state.selection is not None else state.application or state.selector
        }

    @staticmethod
    def validate_element(target, session):
        if not isinstance(target, Binding) or target.session != session:
            raise local_failure(
                "element belongs to an expired driver session",
                "stale_target",
            )

    def remember(self, state, snapshots):
        """Keep action bindings for the session; bound only captured content.

        Element.read/read_text refresh their own data, while previously created
        Element objects keep their immutable snapshot. Dropping a historical
        text capture must never make an otherwise live display reference unknown.
        """
        state.refs.update(snapshots)
        content = self._content.get(state.refs)
        if content is None:
            content = OrderedDict()
            self._content.set(state.refs, content)
        for element_ref in snapshots:
            content[element_ref] = None
            content.move_to_end(element_ref)
        while len(content) > 8192:
            ref, _ = content.popitem(last=False)
            if ref in state.refs:
                state.refs[ref] = dict(state.refs[ref], text=None, children=None)
