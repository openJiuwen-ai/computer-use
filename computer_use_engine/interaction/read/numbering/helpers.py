# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.accessibility.snapshot import NativeSnapshot
from computer_use.interaction.read.numbering.references import DisplayReferences

if TYPE_CHECKING:
    from collections.abc import Iterable

    from computer_use.interaction.read.accessibility.snapshot import ReadSnapshot
    from computer_use.interaction.read.presentation.records import ReferenceAllocator


def snapshot_session(snapshot: ReadSnapshot) -> str:
    """Return the driver session without materializing a native graph."""
    return snapshot.session


def assign_native_batch(
    snapshot: NativeSnapshot, mapping: dict[str, str], references: ReferenceAllocator, natives: Iterable[str]
) -> None:
    """Allocate previously unseen tokens using the same session and scope keys."""
    tokens = tuple(dict.fromkeys(token for token in natives if token not in mapping))
    surface, scope_kind = snapshot.surface, snapshot.scope_kind
    if isinstance(references, DisplayReferences):
        aliases = references.scoped_batch(surface, scope_kind, tokens)
    else:
        aliases = tuple(references.reference((surface, scope_kind, token)) for token in tokens)
    mapping.update(zip(tokens, aliases, strict=True))
