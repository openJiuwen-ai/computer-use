# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Original field projection at the interpreter recursion boundary."""

from collections.abc import Mapping
from types import MappingProxyType

from computer_use.identity import _ATOMIC_TYPES
from computer_use.identity import freeze as freeze


def thaw(value):
    kind = type(value)
    if kind in _ATOMIC_TYPES:
        return value
    if kind is dict or kind is MappingProxyType or isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if kind is tuple or kind is list or isinstance(value, (tuple, list)):
        return [thaw(item) for item in value]
    if isinstance(value, frozenset):
        return set(value)
    return value


def at_depth(function, value, frames):
    """Run a field operation with the original presentation ancestors charged."""
    if frames == 1:
        return function(value)
    return at_depth(function, value, frames - 1)
