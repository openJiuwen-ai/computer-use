# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.interaction.requests import FindElement

if TYPE_CHECKING:
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.interaction.records import Remaining
    from computer_use.native.contracts import Binding
    from computer_use.native.lifecycle import OperationLease
    from computer_use.session.state import AppState


@dataclass(frozen=True)
class FindPlan:
    """Lookup scope and budget prepared from an element query.

    Attributes:
        request: Caller-supplied path, enabled-state flag, and lookup wait limit.
        state: Owning handle's session state, including its observation/query root.
        target: Bound query root, possibly reconciled after a modal closes.
        remaining: Milliseconds remaining on the call, set when its scope opens.
    """

    request: FindElement
    state: AppState
    target: Binding
    remaining: Remaining | None = None


@dataclass(frozen=True)
class FindLease:
    """Open native lookup operation and the facts collected for its result.

    Attributes:
        plan: Query scope and request associated with this operation.
        read: Native lease supplying the operation token and remaining budget.
        selected: Exact element binding returned by path resolution.
        tree: Facts collected beneath the selected element for its snapshot.
    """

    plan: FindPlan
    read: OperationLease[Binding]
    selected: Binding | None = None
    tree: SemanticSnapshot | None = None
