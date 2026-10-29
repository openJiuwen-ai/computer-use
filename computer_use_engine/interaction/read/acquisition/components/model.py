# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.components.support import ObservationGap
from computer_use.pipeline import Effect

if TYPE_CHECKING:
    from computer_use.interaction.read.acquisition.images.capture import WindowImage
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.native.contracts import Binding
    from computer_use.native.lifecycle import OperationLease
    from computer_use.protocol.records import Record


PURE, QUERY = {Effect.PURE}, {Effect.QUERY}


@dataclass(frozen=True)
class ComponentRequest:
    """Window identity, read limits, and the tree and pixel components to collect.

    Attributes:
        target: Native identity of the element, window, or application being observed.
        limits: Requested native collection, image, and view limits.
        include_image: Whether to collect pixels independently of tree availability.
        include_tree: Whether to collect native facts for this component request.
    """

    target: Binding
    limits: Record
    include_image: bool = True
    include_tree: bool = True


@dataclass(frozen=True)
class ComponentParts:
    """Collected window facts and pixels with any component-level failures.

    Attributes:
        tree: Semantic snapshot with retained native facts and display-reference bindings.
        image: Captured pixels and their original coordinate/identity metadata, when available.
        gaps: Explicit collection failures or omissions retained in the observation.
    """

    tree: SemanticSnapshot | None = None
    image: WindowImage | None = None
    gaps: tuple[ObservationGap, ...] = ()


@dataclass(frozen=True)
class ComponentLease:
    """Component request, current native operation, and accumulated component results.

    Attributes:
        request: Parsed request carrying the original operation scope and options.
        read: Active native operation lease, or None before an isolated scope opens.
        parts: Tree and pixel results accumulated within this component operation.
    """

    request: ComponentRequest
    read: OperationLease[Binding] | None
    parts: ComponentParts = ComponentParts()
