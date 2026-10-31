# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.native.query.directory import SurfaceReadProfile

if TYPE_CHECKING:
    from computer_use.native.contracts import Binding
    from computer_use.native.lifecycle import OperationLease
    from computer_use.native.scopes import WorkingWindow
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts


@dataclass(frozen=True)
class WorkingScopeRequest:
    """Existing working-window identity and its directory-discovery options.

    Attributes:
        selection: Fixed working-instance/root-window selection, when established.
        profile: Immutable window-discovery intent for this read.
    """

    selection: WorkingWindow
    profile: SurfaceReadProfile = SurfaceReadProfile.ACCESSIBILITY


@dataclass(frozen=True)
class _ScopeLease:
    """Working-scope discovery request within an open native read.

    Attributes:
        request: Parsed request carrying the original operation scope and options.
        read: Open native operation lease; owns the token and original millisecond deadline.
    """

    request: WorkingScopeRequest
    read: OperationLease[Binding]


@dataclass(frozen=True)
class _ObservedDirectory:
    """Working-scope request paired with the application's observed surface catalogue.

    Attributes:
        request: Parsed request carrying the original operation scope and options.
        directory: Observed application/window catalogue, including incomplete relationships.
    """

    request: WorkingScopeRequest
    directory: DirectoryFacts
