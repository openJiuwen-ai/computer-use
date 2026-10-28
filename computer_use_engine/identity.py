"""Object-identity registries and immutable public result records."""

from __future__ import annotations

import weakref
from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING


class WeakIdentityMap:
    """Associate values with exact objects and release entries when keys die."""

    def __init__(self):
        self._items = {}

    def set(self, key, value):
        identity = id(key)

        def remove(reference):
            entry = self._items.get(identity)
            if entry is not None and entry[0] is reference:
                self._items.pop(identity, None)

        self._items[identity] = (weakref.ref(key, remove), value)

    def get(self, key, default=None):
        entry = self._items.get(id(key))
        return entry[1] if entry is not None and entry[0]() is key else default

    def has(self, key):
        entry = self._items.get(id(key))
        return entry is not None and entry[0]() is key

    def delete(self, key):
        if self.has(key):
            self._items.pop(id(key))

    def clear(self):
        self._items.clear()


class Record(dict):
    """Weak-referenceable internal record. Public results use FrozenRecord."""

    __slots__ = ("__weakref__",)

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        self[name] = value


class FrozenRecord(Mapping):
    """Expose result fields by key or attribute with an optional display string."""

    __slots__ = ("_data", "_display", "__weakref__")

    def __init__(self, data, *, display=None):
        object.__setattr__(self, "_data", MappingProxyType(dict(data)))
        object.__setattr__(self, "_display", display)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def __getattr__(self, name: str) -> Any:
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        raise AttributeError("result is immutable")

    def __repr__(self):
        return self._display if self._display is not None else repr(dict(self._data))


if TYPE_CHECKING:
    from typing import Any
