# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.pipeline import Effect

if TYPE_CHECKING:
    from collections.abc import Hashable, Mapping, Sequence

    from computer_use.interaction.read.accessibility.snapshot import ReadSnapshot
    from computer_use.interaction.read.presentation.records import ReferenceAllocator


PURE, RESOURCE = {Effect.PURE}, {Effect.RESOURCE}


@dataclass(frozen=True)
class _References:
    """Reference lookup for aliases assigned during snapshot numbering.

    Attributes:
        session: Driver session that owns the retained native references.
        aliases: Native identity to display-reference lookup for the numbered snapshot.
    """

    session: str
    aliases: Mapping[str, str]

    def reference(self, native: Hashable) -> str:
        return self.aliases[native]


@dataclass(frozen=True)
class NumberingRequest:
    """Snapshots to number using the existing session reference allocator.

    Attributes:
        snapshots: Source snapshots in the order used for numbering.
        references: Session-owned allocator used to assign display aliases.
    """

    snapshots: tuple[ReadSnapshot, ...]
    references: ReferenceAllocator | None = None


@dataclass(frozen=True)
class NumberingRun:
    """Snapshot-numbering inputs and the alias map accumulated for each snapshot.

    Attributes:
        snapshots: Source snapshots in the order used for numbering.
        references: Session-owned allocator used to assign display aliases.
        aliases: Per-snapshot reference rewrite maps; native bindings stay unchanged.
    """

    snapshots: tuple[ReadSnapshot, ...]
    references: ReferenceAllocator
    aliases: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class _PartPreview:
    """One snapshot and its structured projection used to allocate visible references.

    Attributes:
        snapshot: Facts captured for this observation; display edits do not mutate native
            evidence.
        references: Session-owned allocator used to assign display aliases.
        visible_tokens: Native preview order when the Rust graph stays opaque.
    """

    snapshot: ReadSnapshot
    references: ReferenceAllocator
    visible_tokens: Sequence[str] = ()
