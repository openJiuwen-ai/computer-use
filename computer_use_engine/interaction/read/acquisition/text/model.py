# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.pipeline import Effect

if TYPE_CHECKING:
    from computer_use.interaction.read.acquisition.reader import CapturedField
    from computer_use.interaction.records import Remaining
    from computer_use.native.contracts import Binding
    from computer_use.native.lifecycle import OperationLease
    from computer_use.protocol.records import TextPage as NativeTextPage


PURE, QUERY = {Effect.PURE}, {Effect.QUERY}


@dataclass(frozen=True)
class CapturedTextRequest:
    """Retained text source and the bounded range requested from it.

    Attributes:
        source: Captured field holding local text or a native continuation reference.
        offset: Starting position in the source's declared text or relation units.
        length: Requested text length in the source's declared units.
        timeout_ms: Maximum duration in milliseconds, bounded by the enclosing call deadline.
    """

    source: CapturedField
    offset: int = 0
    length: int = 4096
    timeout_ms: int = 30000


@dataclass(frozen=True)
class CapturedTextPlan:
    """Text-page request paired with the current driver session identity.

    Attributes:
        request: Parsed request carrying the original operation scope and options.
        session: Current driver generation checked against the captured text source.
    """

    request: CapturedTextRequest
    session: str


@dataclass(frozen=True)
class LocalCapturedTextRead:
    """Local text-page request within the original call deadline.

    Attributes:
        plan: Resolved stage plan; its fields and limits come from the original request.
        remaining: Callback returning milliseconds left on the original call; may raise on
            expiration.
    """

    plan: CapturedTextPlan
    remaining: Remaining


@dataclass(frozen=True)
class OpenCapturedTextRead:
    """Native text-page request within an open driver operation.

    Attributes:
        plan: Resolved stage plan; its fields and limits come from the original request.
        read: Open native operation lease; owns the token and original millisecond deadline.
    """

    plan: CapturedTextPlan
    read: OperationLease[Binding]


@dataclass(frozen=True)
class CapturedTextPage:
    """Collected native text page paired with its source request.

    Attributes:
        plan: Resolved stage plan; its fields and limits come from the original request.
        page: Collected native text page and its range metadata.
    """

    plan: CapturedTextPlan
    page: NativeTextPage
