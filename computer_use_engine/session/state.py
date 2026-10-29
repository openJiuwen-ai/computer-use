# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Persistent App scope, observations, and delivered-coordinate state."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..native.contracts import Binding
from ..native.scopes import RunningApplication, WorkingWindow


@dataclass
class AppState:
    """Persistent state owned by the session's handle registry.

    Attributes:
        selector: Original application/instance selector.
        running: Confirmed running identity, when observed.
        selection: Fixed working-window selection, when established.
        observation: Latest snapshot, whose structure is defined by the read branch.
        query_target: Exact native scope for subsequent element lookup.
        query_scope_kind: Native scope kind associated with that query target.
        directory: Last observed window catalogue, or None before observation.
        refs: Retained read-owned element snapshots, indexed by display number.
        windows: Display window references mapped to native window identities.
        delivered: Capture identity and delivery epoch, when delivered to the model.
        delivered_pixels: Exact window identity, digest, and delivery epoch.
        capture_expired: Whether the delivered image's coordinate mapping has expired.
    """

    selector: str
    running: RunningApplication | None = None
    selection: WorkingWindow | None = None
    # Captured observation is opaque here: the read branch owns its definition.
    observation: object | None = None
    query_target: Binding | None = None
    query_scope_kind: str | None = None
    directory: DirectoryFacts | None = None
    refs: OrderedDict[str, Record] = field(default_factory=OrderedDict)
    windows: dict[str, str] = field(default_factory=dict)
    delivered: tuple[str | None, int] | None = None
    delivered_pixels: tuple[Binding, bytes, int] | None = None
    capture_expired: bool = False

    @property
    def application(self):
        return self.running.application if self.running is not None else None

    @property
    def name(self):
        return self.running.name if self.running is not None else ""


if TYPE_CHECKING:
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts
    from computer_use.protocol.records import Record
