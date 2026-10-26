from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.pipeline import Effect

if TYPE_CHECKING:
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.interaction.read.presentation.preparation import PreparedSnapshot
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
    aliases: dict[str, str]

    def reference(self, native: Hashable) -> str:
        return self.aliases[native]


@dataclass(frozen=True)
class NumberingRequest:
    """Snapshots to number using the existing session reference allocator.

    Attributes:
        snapshots: Source snapshots in the order used for numbering.
        references: Session-owned allocator used to assign display aliases.
    """

    snapshots: tuple[SemanticSnapshot, ...]
    references: ReferenceAllocator | None = None


@dataclass(frozen=True)
class NumberingRun:
    """Snapshot-numbering inputs and the alias map accumulated for each snapshot.

    Attributes:
        snapshots: Source snapshots in the order used for numbering.
        references: Session-owned allocator used to assign display aliases.
        aliases: Per-snapshot reference rewrite maps; native bindings stay unchanged.
    """

    snapshots: tuple[SemanticSnapshot, ...]
    references: ReferenceAllocator
    aliases: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class _PartPreview:
    """One snapshot and its structured projection used to allocate visible references.

    Attributes:
        snapshot: Facts captured for this observation; display edits do not mutate native
            evidence.
        references: Session-owned allocator used to assign display aliases.
        prepared: Immutable compressed projection and visible reference order.
    """

    snapshot: SemanticSnapshot
    references: ReferenceAllocator
    prepared: PreparedSnapshot | None = None


if TYPE_CHECKING:
    from typing import Hashable
