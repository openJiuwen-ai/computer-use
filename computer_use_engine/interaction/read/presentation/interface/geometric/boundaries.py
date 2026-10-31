# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Query retained region boundaries without changing native scope or identity."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.interface.model import InterfaceDraft


def same_region(draft: InterfaceDraft, refs: Iterable[str]) -> bool:
    """Require candidate sources to stay inside each established local leaf.

    Args:
        draft: Current blocks and the immutable evidence retained by region planning.
        refs: Original sources that a later stage proposes to group or align.

    Returns:
        False when any recorded scope places the sources in different leaves.
        Sources outside that scope remain subject to the caller's native scope
        and relationship checks. An absent region plan introduces no restriction.
    """
    if draft.geometric_regions is None:
        return True
    members = frozenset(refs)
    return all(
        len({scope.source_paths[ref] for ref in members if ref in scope.source_paths}) <= 1
        for scope in draft.geometric_regions.scopes.values()
    )
