# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from ..diagnostics import diagnostic
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
    window_coverage=(),
    captured_trees=(),
    presentations=(),
):
    # JS Set iteration preserves the observed order (notably window enums).
    data = dict(
        roots=roots,
        refs=frozen(dict.fromkeys(refs)).keys(),
        windows=frozen(dict.fromkeys(windows)).keys(),
        partial=partial,
        content_available=content_available,
        gaps=tuple(gaps),
        window_coverage=tuple(frozen(record) for record in window_coverage),
        captured_trees=tuple(captured_trees),
        presentations=tuple(frozen(item) for item in presentations),
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


def component_delivered(state, component):
    """Acknowledge only components included in this exact returned object."""
    data = _views.get(state)
    if data is None:
        return
    data["window_coverage"] = tuple(
        frozen({**record, "delivered": tuple(sorted(set(record["delivered"]) | {component}))})
        if component in record["included"] else record
        for record in data["window_coverage"]
    )
    delivered = tuple(record for record in data["window_coverage"] if component in record["delivered"])
    if delivered:
        diagnostic({"kind": "observation_member_delivery", "component": component, "windows": delivered})
