# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Persistent display identities; one owner per native session."""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING

from computer_use.interaction.read.accessibility._native import require_read_engine

if TYPE_CHECKING:
    from collections.abc import Hashable, Iterable


class DisplayReferences:
    """Session aliases are identities, not a least-recently-used content cache."""

    def __init__(self, session: str, *, native=None) -> None:
        self.session = session
        self._native = native
        self._refs, self._next = OrderedDict(), 0

    def reference(self, native: Hashable) -> str:
        """Return the existing display alias or allocate the next session number.

        Args:
            native: Native execution port or unmodified provider facts for this domain.
        """
        if self._native is not None:
            return self._native.reference(native)
        if native not in self._refs:
            self._refs[native] = str(self._next)
            self._next += 1
        self._refs.move_to_end(native)
        return self._refs[native]

    def reference_batch(self, natives: Iterable[Hashable]) -> tuple[str, ...]:
        """Allocate or touch aliases in the supplied order."""
        if self._native is not None:
            return tuple(self._native.reference_batch(tuple(natives)))
        return tuple(self.reference(native) for native in natives)

    def scoped_batch(self, surface: str | None, kind: str | None, tokens: tuple[str, ...]) -> tuple[str, ...]:
        if self._native is not None:
            return tuple(self._native.scoped_batch(surface, kind, tokens))
        return self.reference_batch((surface, kind, token) for token in tokens)


def read_references(session: str, platform: str | None) -> DisplayReferences:
    """Select one alias owner for the entire driver session."""
    native = require_read_engine().NativeReadReferences(session) if platform in ("macos", "windows", "linux") else None
    return DisplayReferences(session, native=native)
