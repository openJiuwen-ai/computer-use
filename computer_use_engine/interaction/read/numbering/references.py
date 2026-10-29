# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bounded persistent display aliases; one owner per native session."""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Hashable


class DisplayReferences:
    """Bounded session aliases; retired numbers are never given to another target."""

    def __init__(self, session: str, limit: int = 16384) -> None:
        self.session = session
        # Exact types preserve scalar validation and identity contracts.
        # pylint: disable-next=unidiomatic-typecheck
        if type(limit) is not int or limit < 1:
            raise ValueError("Invalid display reference capacity")
        self._refs, self._next, self._limit = OrderedDict(), 0, limit

    def reference(self, native: Hashable) -> str:
        """Return the existing display alias or allocate the next session number.

        Args:
            native: Native execution port or unmodified provider facts for this domain.
        """
        if native not in self._refs:
            self._refs[native] = str(self._next)
            self._next += 1
            while len(self._refs) > self._limit:
                self._refs.popitem(last=False)
        self._refs.move_to_end(native)
        return self._refs[native]
