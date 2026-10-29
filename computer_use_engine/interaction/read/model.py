# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING

from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.components.support import ObservationGap
from computer_use.interaction.read.acquisition.images.capture import WindowImage
from computer_use.interaction.read.acquisition.images.overview import WindowOverview
from computer_use.interaction.read.acquisition.menus.query import MenuRead
from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
from computer_use.interaction.read.presentation.content import DisplayText
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.native.contracts import Binding
from computer_use.native.scopes import WorkingWindow
from computer_use.pipeline import Effect

if TYPE_CHECKING:
    from typing import Any

    from computer_use.protocol.records import NativeDirectory as DirectoryFacts


PURE = {Effect.PURE}


@dataclass(frozen=True)
class AppObservation:
    """Collected window, menu, and image evidence for one working application instance.

    Attributes:
        selection: Fixed working-instance/root-window selection, when established.
        target: Native identity of the element, window, or application being observed.
        applications: Application catalogue captured while resolving the requested scope.
        source_directory: Catalogue captured with the source facts, before scope rechecking.
        directory: Observed application/window catalogue, including incomplete relationships.
        tree: Semantic snapshot with retained native facts and display-reference bindings.
        image: Captured pixels and their original coordinate/identity metadata, when available.
        document: Immutable structured observation, rendered only when text is requested.
        context_notes: Structured scope notes consumed by the public interface composition.
        refs: Display element aliases mapped to their native execution bindings.
        windows: Display window aliases mapped to native surface identities.
        gaps: Explicit collection failures or omissions retained in the observation.
        scope_confirmed: Whether the fixed observation scope was confirmed at recheck time.
        launch: Original startup receipt; retained even when later observation fails.
        menus: Menu snapshots collected for the same application scope.
        overview: Related-window preview and its source metadata, when collected.
    """

    selection: WorkingWindow
    target: Binding
    applications: DirectoryFacts | None
    source_directory: DirectoryFacts
    directory: DirectoryFacts
    tree: SemanticSnapshot | None
    image: WindowImage | None
    document: DisplayDocument
    refs: Mapping[str, Binding]
    windows: Mapping[str, str]
    gaps: tuple[ObservationGap, ...]
    scope_confirmed: bool
    launch: Mapping[str, Any] | None = None
    menus: MenuRead | None = None
    overview: WindowOverview | None = None
    context_notes: tuple[DisplayText, ...] = ()

    @cached_property
    def text(self) -> str:
        """Render the complete retained observation on first explicit access."""
        return self.document.text


@dataclass(frozen=True)
class MenuAppObservation:
    """Menu snapshots and formatted text for an application observation.

    Attributes:
        application: Native application identity that owns the observed menu sources.
        name: Display name of the selected application.
        selection: Fixed working-instance/root-window selection, when established.
        menu: Collected native menu sources and their query scope.
        directory: Observed application/window catalogue, including incomplete relationships.
        windows: Display window aliases mapped to native surface identities.
        document: Immutable structured menu presentation.
        launch: Original startup receipt; retained even when later observation fails.
    """

    application: str
    name: str
    selection: WorkingWindow | None
    menu: MenuRead
    directory: DirectoryFacts
    windows: Mapping[str, str]
    document: DisplayDocument
    launch: Mapping[str, Any] | None = None

    @cached_property
    def text(self) -> str:
        """Render the complete retained menu observation on first explicit access."""
        return self.document.text


class WindowSelectionRequired(ComputerUseError):
    """Preserve the catalogue and any fixed root when a view needs a choice."""

    def __init__(
        self,
        error: ComputerUseError,
        application: str,
        name: str,
        directory: DirectoryFacts,
        launch: Mapping[str, Any] | None = None,
        overview: WindowOverview | None = None,
        selection: WorkingWindow | None = None,
    ) -> None:
        super().__init__({key: getattr(error, key) for key in ("code", "effect", "target", "detail", "next")})
        self.application, self.display_name, self.directory, self.launch = application, name, directory, launch
        self.overview = overview
        self.selection = selection
