# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from ..identity import WeakIdentityMap
from ..protocol.primitives import frozen

_views = WeakIdentityMap()


def retain_observation_data(
    state,
    roots,
    refs,
    windows=(),
    instance=None,
    identity=None,
    *,
    partial=False,
    content_available=True,
    gaps=(),
    directory=None,
):
    # JS Set iteration preserves the observed order (notably window enums).
    data = dict(
        roots=roots,
        refs=frozen(dict.fromkeys(refs)).keys(),
        windows=frozen(dict.fromkeys(windows)).keys(),
        partial=partial,
        content_available=content_available,
        gaps=tuple(gaps),
        directory=frozen(
            {
                **directory,
                "surfaces": tuple(frozen(item) for item in directory["surfaces"]),
                "stopReasons": tuple(directory["stopReasons"]),
                "observed": frozen(directory["observed"]),
                "provider": frozen(directory["provider"]),
            }
        )
        if directory is not None
        else None,
    )
    if instance is not None:
        data["instance"] = instance
    if identity is not None:
        data["identity"] = frozen({**identity, "windows": tuple(frozen(w) for w in identity["windows"])})
    _views.set(state, data)


def observation_data(state):
    return _views.get(state)
