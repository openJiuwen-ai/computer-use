# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Returned semantic scopes for name lookup within one host execution.

These facts do not commit model-delivery, image or keyboard-input baselines.
The host owns their bounded lifetime; copied async contexts share a cleared
container after the host exits, including exceptional and cancelled exits.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from computer_use.identity import WeakIdentityMap
from computer_use.session.interfaces import LookupContext, WindowIdentity

_scopes = ContextVar("computer_use_lookup_continuation", default=None)


@dataclass(frozen=True)
class LookupContinuation:
    session: str
    context: LookupContext
    windows: frozenset[WindowIdentity]
    generation: int


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


def retain_lookup_continuation(instance, context, windows, generation, session):
    """Retain only successful semantic publications, never image-only/catalogue reads."""
    scopes = _scopes.get()
    if scopes is None or context is None or context.window not in windows or session != instance.session:
        return
    previous = current_lookup_continuation(instance)
    if previous is not None and previous.generation > generation:
        return
    roots = frozenset(windows) | (previous.windows if previous is not None else frozenset())
    scopes.set(instance, LookupContinuation(session, context, roots, generation))
