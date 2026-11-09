# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Returned semantic scopes for name lookup within one host execution.

These facts do not commit model-delivery, image or keyboard-input baselines.
The host owns their bounded lifetime; copied async contexts share a cleared
container after the host exits, including exceptional and cancelled exits.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from computer_use.identity import WeakIdentityMap
from computer_use.session.interfaces import LookupContext, WindowIdentity

_scopes = ContextVar("computer_use_lookup_continuation", default=None)


@dataclass(frozen=True)
class LookupContinuation:
    session: str
    context: LookupContext
    windows: frozenset[WindowIdentity]
    generation: int
    sources: object = field(default_factory=dict)


@contextmanager
def lookup_continuation_scope():
    """Allow a host script to query observations already returned to that script."""
    scopes = WeakIdentityMap()
    token = _scopes.set(scopes)
    try:
        yield
    finally:
        scopes.clear()
        _scopes.reset(token)


def current_lookup_continuation(instance):
    scopes = _scopes.get()
    value = scopes.get(instance) if scopes is not None else None
    if value is None or value.session != instance.session:
        return None
    return value if value.generation >= instance.delivered_observation_generation else None


def discard_lookup_continuation(instance):
    """Drop only this application cache; unrelated SDK sessions keep theirs."""
    scopes = _scopes.get()
    if scopes is not None:
        scopes.delete(instance)


def retain_lookup_continuation(instance, context, windows, generation, session, *, sources=()):
    """Retain semantic facts/gaps without granting a new scope for failed reads."""
    scopes = _scopes.get()
    if scopes is None or session != instance.session:
        return
    previous = current_lookup_continuation(instance)
    if previous is not None and previous.generation > generation:
        return
    if context is None or context.window not in windows:
        # Failed tree acquisition can replace facts inside an existing scope,
        # but cannot establish new lookup/window/input scope. Pure image or
        # catalogue observations have no semantic sources and remain ignored.
        if not sources:
            return
        context = previous.context if previous is not None else instance.lookup_context
        windows = previous.windows if previous is not None else frozenset(instance.delivered_lookup_windows)
        if context is None or context.window not in windows:
            return
    roots = frozenset(windows) | (previous.windows if previous is not None else frozenset())
    from computer_use.session.observed_lookup import discard_lookup_results, merge_lookup_scopes, scope_key

    captured = merge_lookup_scopes(previous.sources if previous is not None else (), sources)
    discard_lookup_results(instance, {scope_key(source.target)[:4] for source in sources})
    scopes.set(instance, LookupContinuation(session, context, roots, generation, captured))
