# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Text-free incremental presentation contracts shared with the terminal renderer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from types import MappingProxyType
from typing import Literal


@dataclass(frozen=True)
class TreeDelta:
    """Structural changes and retained context for one current display tree."""

    changes: Mapping[str, str]
    missing: tuple[str, ...]
    context: frozenset[str]
    expanded: frozenset[str]


@dataclass(frozen=True)
class DeltaPlan:
    """Chosen delivery scope before any fields or lines are formatted.

    Attributes:
        mode: Complete delivery, changed elements, or an unchanged-tree summary.
        trees: Per-tree changes in current document order; None means this tree is first observed or replaced.
        not_observed: Missing references whose removal is not proved by collection.
        retained: References still bound in the observation, including folded nodes.
    """

    mode: Literal["full", "diff", "unchanged"] = "full"
    trees: tuple[TreeDelta | None, ...] = ()
    not_observed: frozenset[str] = frozenset()
    retained: frozenset[str] = frozenset()
    reason: str = "initial-observation"


@dataclass(frozen=True)
class RustDeltaPlan:
    """Native whole-document plan used directly by the terminal tree renderer."""

    handle: object

    @cached_property
    def not_observed(self) -> frozenset[str]:
        return frozenset(self.handle.not_observed())

    @cached_property
    def retained(self) -> frozenset[str]:
        return frozenset(self.handle.retained())

    @property
    def mode(self) -> Literal["full", "diff", "unchanged"]:
        return self.handle.mode

    @property
    def reason(self) -> str:
        return self.handle.reason

    @cached_property
    def summaries(self) -> tuple[tuple[int, int, int] | None, ...]:
        return tuple(self.handle.summaries())

    def tree_at(self, index: int) -> TreeDelta | None:
        """Expose a single mixed legacy tree delta to its host renderer."""
        data = self.handle.tree(index)
        if data is None:
            return None
        changes, missing, context, expanded = data
        return TreeDelta(MappingProxyType(dict(changes)), tuple(missing), frozenset(context), frozenset(expanded))
