# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from ..identity import WeakIdentityMap
from ..protocol.primitives import frozen

_views = WeakIdentityMap()


def retain_observation_data(state, roots, refs, windows=(), instance=None, identity=None):
    # JS Set iteration preserves the observed order (notably window enums).
    data = dict(roots=roots, refs=frozen(dict.fromkeys(refs)).keys(), windows=frozen(dict.fromkeys(windows)).keys())
    if instance is not None:
        data["instance"] = instance
    if identity is not None:
        data["identity"] = frozen({**identity, "windows": tuple(frozen(w) for w in identity["windows"])})
    _views.set(state, data)


def observation_data(state):
    return _views.get(state)
