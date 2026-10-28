# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Source-bound plans for sharing repeated collection coverage in whole views."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from computer_use.interaction.read.presentation.interface.atoms.model import AtomDocument
from computer_use.interaction.read.presentation.interface.model import InterfaceDocument


@dataclass(frozen=True)
class SharedCoverage:
    """Equal empty initial pages whose individual targets remain independently readable.

    Attributes:
        refs: Every original target covered by the declaration, in observation order.
        total: Positive number of direct children reported for each target.
        reason: Exact collection reason, shared only when every source agrees.
        scope: Original native window or menu boundary containing these targets.
    """

    refs: tuple[str, ...]
    total: int
    reason: str
    scope: str


@dataclass(frozen=True)
class CoverageRequest:
    """Validated block organization and immutable per-source coverage facts."""

    interface: InterfaceDocument
    atoms: AtomDocument


@dataclass(frozen=True)
class CoveragePlan:
    """Whole-view declarations; original atom hints remain available for diff reads."""

    by_block: Mapping[str, tuple[SharedCoverage, ...]] = field(default_factory=lambda: MappingProxyType({}))
    omitted_refs: frozenset[str] = frozenset()
