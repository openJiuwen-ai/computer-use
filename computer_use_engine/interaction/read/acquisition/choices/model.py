# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.native.query.directory import SurfaceReadProfile

if TYPE_CHECKING:
    from computer_use.errors import ComputerUseError
    from computer_use.interaction.read.acquisition.images.overview import WindowOverview
    from computer_use.native.contracts import Binding
    from computer_use.native.lifecycle import OperationLease
    from computer_use.native.scopes import WorkingWindow
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts


@dataclass(frozen=True)
class WindowChoiceRequest:
    """Ambiguous-read evidence used to build an application's window choices.

    Attributes:
        error: Original read failure that requires a window-selection response.
        application: Selected native application identity or catalogue entry.
        name: Display name of the selected application.
        directory: Observed application/window catalogue, including incomplete relationships.
        selection: Fixed working-instance/root-window selection, when established.
        include_overview: Whether the window-choice response includes independent window previews.
        profile: Original directory intent retained through preview rechecks.
    """

    error: ComputerUseError
    application: str
    name: str
    directory: DirectoryFacts
    selection: WorkingWindow | None = None
    include_overview: bool = True
    profile: SurfaceReadProfile = SurfaceReadProfile.ACCESSIBILITY


@dataclass(frozen=True)
class WindowChoiceDraft:
    """Window-choice request paired with previews and a refreshed catalogue.

    Attributes:
        request: Parsed request carrying the original operation scope and options.
        overview: Related-window preview and its source metadata, when collected.
        current: Application surface catalogue refreshed after collecting the previews.
    """

    request: WindowChoiceRequest
    overview: WindowOverview | None
    current: DirectoryFacts | None = None


@dataclass(frozen=True)
class WindowChoiceLease:
    """Window-choice draft within an open directory-query operation.

    Attributes:
        draft: Observation inputs and collected evidence awaiting final formatting.
        read: Open native operation lease; owns the token and original millisecond deadline.
    """

    draft: WindowChoiceDraft
    read: OperationLease[Binding]
