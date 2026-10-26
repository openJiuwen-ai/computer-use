"""Text-free incremental presentation contracts shared with the terminal renderer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
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
        trees: Per-tree changes in current document order.
        not_observed: Missing references whose removal is not proved by collection.
        retained: References still bound in the observation, including folded nodes.
    """

    mode: Literal["full", "diff", "unchanged"] = "full"
    trees: tuple[TreeDelta, ...] = ()
    not_observed: frozenset[str] = frozenset()
    retained: frozenset[str] = frozenset()
