# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Identify exact identifier fields delivered by captured native-fact supplements."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.accounting.native_details import collect_native_details

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.model import RenderedPart
    from computer_use.interaction.read.presentation.records import SnapshotFacts
    from computer_use.protocol.records import Record


def collect_identifiers(
    snapshot: SnapshotFacts,
    parts: Sequence[RenderedPart],
) -> tuple[tuple[Record, ...], ...]:
    """Locate residual identifiers with an actual same-reference detail supplement.

    The returned entries belong to the existing field-accounting ledger. Snapshot
    assembly marks their destination only after retaining the supplementary text.
    An identifier already represented by an existing caption keeps that accounting.
    Failed branches retain their original uncompressed facts.

    Args:
        snapshot: Captured facts and the unchanged display-to-native reference map.
        parts: Accounted root renderings with their exact supplementary field groups.

    Returns:
        Identifier destination records in root-part order, including captured empty
        strings and fields with read limitations. Their original values and statuses
        remain in the captured facts and diagnostic records.
    """
    return collect_native_details(snapshot, parts, "identifier")
