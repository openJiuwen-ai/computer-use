# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from collections.abc import Mapping, Set
from dataclasses import dataclass
from typing import Protocol

from computer_use.interaction.write.routes.policy import RouteKey
from computer_use.native.contracts import Facts, HostPolicy, Intent, Primitive, UnitCommand


class RouteHistory(Protocol):
    """Read-only part of the existing attempt owner needed by selection.

    Attributes:
        unavailable: Routes already refused for this operation.
        background_unavailable: Routes whose background preparation failed.
        prepared: Route/foreground pairs already prepared by this operation.
    """

    @property
    def unavailable(self) -> Set[RouteKey]:
        pass

    @property
    def background_unavailable(self) -> Set[RouteKey]:
        pass

    @property
    def prepared(self) -> Set[tuple[RouteKey, bool]]:
        pass

    @property
    def click_refresh(self) -> RouteKey | None:
        pass

    @property
    def failed_steps(self) -> Mapping[RouteKey, int]:
        pass


@dataclass(frozen=True)
class SelectionRequest:
    """Inputs to pure candidate/route selection.

    Attributes:
        facts: Current native target and capability evidence.
        intent: Original parsed operation meaning and parameters.
        policy: Host input policy, when this narrow query supplies one.
        history: Read-only view of the existing attempt owner, when supplied.
        needs_editing_evidence: Whether an editor route still needs native evidence.
    """

    facts: Facts
    intent: Intent
    policy: HostPolicy | None = None
    history: RouteHistory | None = None
    needs_editing_evidence: bool = False


@dataclass(frozen=True)
class CandidateSet:
    """Ordered semantic candidates before live readiness/history filtering.

    Attributes:
        request: Original selection request, retained unchanged.
        primitives: Candidate declarations in the current semantic and preference order.
    """

    request: SelectionRequest
    primitives: tuple[Primitive, ...]


@dataclass(frozen=True)
class EligibleRoutes:
    """Candidates paired with exact native commands after policy filtering.

    Attributes:
        request: Original selection request and its host policy/history.
        candidates: Candidate declarations retained for refusal explanations.
        commands: Exact commands that remain eligible for route selection.
    """

    request: SelectionRequest
    candidates: tuple[Primitive, ...]
    commands: tuple[UnitCommand, ...]


@dataclass(frozen=True)
class RouteOptions:
    """Candidate preparation modes and the command chosen by route selection.

    Attributes:
        eligible: Filtered candidates and their original request.
        preparation: Candidate command and whether it requires foreground input.
        property_replacements: Eligible object-property replacement commands.
        selected: One chosen command, or None before a branch selects one.
    """

    eligible: EligibleRoutes
    preparation: tuple[tuple[UnitCommand, bool], ...] = ()
    property_replacements: tuple[UnitCommand, ...] = ()
    selected: UnitCommand | None = None
