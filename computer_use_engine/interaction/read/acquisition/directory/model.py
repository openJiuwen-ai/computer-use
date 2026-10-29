# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.native.contracts import Binding
    from computer_use.native.lifecycle import OperationLease
    from computer_use.protocol.records import Record


@dataclass(frozen=True)
class DirectoryRequest:
    """Application, surface, or root catalogue query with its call time limit.

    Attributes:
        query: Native directory query specifying its catalogue kind and scope.
        timeout_ms: Maximum duration in milliseconds, bounded by the enclosing call deadline.
    """

    query: Record
    timeout_ms: int = 30000


@dataclass(frozen=True)
class DirectoryPlan:
    """Directory query paired with the native operation that will execute it.

    Attributes:
        read: Open native operation lease; owns the token and original millisecond deadline.
        query: Native directory query specifying its catalogue kind and scope.
    """

    read: OperationLease[Binding]
    query: Record
