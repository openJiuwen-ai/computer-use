# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Reference allocation and rendered records used by delivery."""

from collections.abc import Hashable
from typing import Protocol, TypeAlias

from computer_use.protocol.records import Record


RenderedTree: TypeAlias = Record


class ReferenceAllocator(Protocol):
    """The existing session owner that allocates display aliases.

    Attributes:
        session: Driver generation to which all allocated aliases belong.
    """

    session: str

    def reference(self, native: Hashable) -> str:
        """Return an alias for an exact native identity, without rebinding it.

        Args:
            native: Native object token or exact compound scope key in this session.

        Returns:
            The retained or newly allocated display reference.
        """
        ...
