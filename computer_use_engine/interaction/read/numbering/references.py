# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Persistent display identities; one owner per native session."""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Hashable


class DisplayReferences:
    """Session aliases are identities, not a least-recently-used content cache."""

    def __init__(self, session: str) -> None:
        self.session = session
        self._refs, self._next = OrderedDict(), 0

    def reference(self, native: Hashable) -> str:
        """Return the existing display alias or allocate the next session number.

        Args:
            native: Native execution port or unmodified provider facts for this domain.
        """
        if native not in self._refs:
            self._refs[native] = str(self._next)
            self._next += 1
        self._refs.move_to_end(native)
        return self._refs[native]
