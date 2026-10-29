# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.components.support import ObservationGap
from computer_use.pipeline import Effect

if TYPE_CHECKING:
    from typing import Any

    from computer_use.interaction.read.acquisition.images.capture import WindowImage
    from computer_use.interaction.read.acquisition.images.overview import WindowOverview
    from computer_use.interaction.read.acquisition.menus.query import MenuRead
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.native.contracts import Binding
    from computer_use.native.scopes import WorkingWindow
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts


PURE, QUERY = {Effect.PURE}, {Effect.QUERY}


class Refresh(str, Enum):
    """Acquisition actions selected after comparing the collected and current scopes."""

    KEEP = "keep"
    SCOPE = "recollect_scope"
    IMAGE = "recollect_image"
    TREE = "refresh_tree"


@dataclass(frozen=True)
class ReadPlan:
    """Fixed working scope and limits used by bounded component acquisition.

    Attributes:
        selection: Fixed working-instance/root-window selection, when established.
        directory: Observed application/window catalogue, including incomplete relationships.
        applications: Application catalogue captured while resolving the requested scope.
        launch: Original startup receipt; retained even when later observation fails.
        limits: Requested native collection, image, and view limits.
    """

    selection: WorkingWindow
    directory: DirectoryFacts
    applications: DirectoryFacts | None
    launch: Mapping[str, Any] | None
    limits: Mapping[str, Any]


@dataclass(frozen=True)
class Components:
    """Independent observations collected for the same requested scope.

    Attributes:
        source_directory: Catalogue captured with the source facts, before scope rechecking.
        tree: Semantic snapshot with retained native facts and display-reference bindings.
        image: Captured pixels and their original coordinate/identity metadata, when available.
        menus: Menu snapshots collected for the same application scope.
        overview: Related-window preview and its source metadata, when collected.
        gaps: Explicit collection failures or omissions retained in the observation.
        image_precedes_tree: Whether a newer tree refresh postdates the retained independent image capture.
    """

    source_directory: DirectoryFacts
    tree: SemanticSnapshot | None = None
    image: WindowImage | None = None
    menus: MenuRead | None = None
    overview: WindowOverview | None = None
    gaps: tuple[ObservationGap, ...] = ()
    image_precedes_tree: bool = False


@dataclass(frozen=True)
class ReadRunState:
    """Read-attempt state owned by the bounded acquisition loop.

    Attributes:
        plan: Fixed working scope and limits used by bounded component acquisition.
        target: Native identity of the element, window, or application being observed.
        directory: Observed application/window catalogue, including incomplete relationships.
        initializing: Whether startup evidence permits the existing bounded refresh allowance.
        attempt: Zero-based attempt count consumed by the acquisition refresh gate.
        refresh_tree: Whether to refresh only the tree while retaining already captured pixels.
        components: Tree, image, menu, and overview results collected for this attempt.
        gaps: Explicit collection failures or omissions retained in the observation.
    """

    plan: ReadPlan
    target: Binding
    directory: DirectoryFacts
    initializing: bool
    attempt: int = 0
    refresh_tree: bool = False
    components: Components | None = None
    gaps: tuple[ObservationGap, ...] = ()


@dataclass(frozen=True)
class CollectedAttempt:
    """Read-attempt inputs paired with the components actually captured.

    Attributes:
        state: Read-attempt state owned by the bounded acquisition loop.
        components: Tree, image, menu, and overview results collected for this attempt.
    """

    state: ReadRunState
    components: Components


@dataclass(frozen=True)
class RecheckedAttempt:
    """Collected components and the subsequent scope recheck evidence.

    Attributes:
        collected: Acquisition attempt and the components it produced.
        directory: Observed application/window catalogue, including incomplete relationships.
        target: Native identity of the element, window, or application being observed.
        current: Target resolved from the directory recheck, when available.
        scope_confirmed: Whether the fixed observation scope was confirmed at recheck time.
        gaps: Explicit collection failures or omissions retained in the observation.
    """

    collected: CollectedAttempt
    directory: DirectoryFacts
    target: Binding
    current: Binding | None
    scope_confirmed: bool
    gaps: tuple[ObservationGap, ...]


@dataclass(frozen=True)
class ClassifiedAttempt:
    """Pure refresh decision with the evidence for that decision.

    Attributes:
        rechecked: Collected evidence and the subsequent scope-recheck result.
        refresh: Next acquisition action selected from the recheck evidence.
        wait_gap: Wait failure retained as an observation gap, when present.
    """

    rechecked: RecheckedAttempt
    refresh: Refresh
    wait_gap: ObservationGap | None = None


@dataclass(frozen=True)
class AcquiredObservation:
    """Completed bounded acquisition, retaining explicit gaps and scope certainty.

    Attributes:
        plan: Fixed working scope and limits used by bounded component acquisition.
        target: Native identity of the element, window, or application being observed.
        directory: Observed application/window catalogue, including incomplete relationships.
        components: Tree, image, menu, and overview results collected for this attempt.
        gaps: Explicit collection failures or omissions retained in the observation.
        scope_confirmed: Whether the fixed observation scope was confirmed at recheck time.
    """

    plan: ReadPlan
    target: Binding
    directory: DirectoryFacts
    components: Components
    gaps: tuple[ObservationGap, ...]
    scope_confirmed: bool
