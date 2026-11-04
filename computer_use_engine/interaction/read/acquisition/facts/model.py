# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.pipeline import Effect

if TYPE_CHECKING:
    from computer_use.interaction.records import Remaining
    from computer_use.native.contracts import Binding
    from computer_use.native.lifecycle import OperationLease
    from computer_use.protocol.observation import NativeObservation
    from computer_use.protocol.observation.adapters import DriverReadMetadata
    from computer_use.protocol.records import Record, RelationPage


PURE, QUERY, RESOURCE = {Effect.PURE}, {Effect.QUERY}, {Effect.RESOURCE}


@dataclass(frozen=True)
class FactReadRequest:
    """A bounded native graph read within an already admitted operation.

    Attributes:
        read: Open native operation lease; owns the token and original millisecond deadline.
        node: Exact source node or read-only projected node, retaining its original reference.
        fields: Native fields requested for each element in this collection.
        max_nodes: Maximum number of native objects this collection may read.
        max_depth: Maximum native relation depth; None uses the existing platform default.
        children_page: Continuation of the selected root's native child relation, when
            requested.
        relation_page: Continuation of another observed native relation, when requested.
        scope_kind: Original read-scope kind, when specified.
        query_remaining: Optional tighter allowance for the native query. Returns
            positive integer milliseconds or raises TimeoutError on expiry.
            Normalization and its final check retain the enclosing lease deadline.
    """

    read: OperationLease[Binding]
    node: str | None = None
    fields: list[str] | None = None
    max_nodes: int = 1000
    max_depth: int | None = None
    children_page: RelationPage | None = None
    relation_page: RelationPage | None = None
    scope_kind: str | None = None
    query_remaining: Remaining | None = None


@dataclass(frozen=True)
class FactScope:
    """The exact native scope resolved from a fact-read request.

    Attributes:
        request: A bounded native graph read within an already admitted operation.
        target: Native identity of the element, window, or application being observed.
    """

    request: FactReadRequest
    target: Binding


@dataclass(frozen=True)
class NativeReadCapabilities:
    """Provider metadata used by pure read-profile planning.

    Attributes:
        platform: Native provider platform used to select the fact-read profile.
        features: Capabilities advertised by the driver; not inferred from one app's appearance.
    """

    platform: str
    features: Collection[str]


@dataclass(frozen=True)
class FactProfile:
    """Selected native fields, depth, and query options for one read.

    Attributes:
        scope: The exact native scope resolved from a fact-read request.
        fields: Native fields selected after platform and capability planning.
        max_depth: Maximum native relation depth; None uses the existing platform default.
        options: Normalized collection options for the selected element.
    """

    scope: FactScope
    fields: list[str]
    max_depth: int
    options: Record


@dataclass(frozen=True)
class CollectedFacts:
    """One native graph batch paired with the profile that produced it.

    Attributes:
        profile: Selected fields, depth, and native query options used for this collection.
        batch: Validated native facts, retaining source identities and incomplete-read evidence.
    """

    profile: FactProfile
    batch: NativeObservation
    metadata: DriverReadMetadata


@dataclass(frozen=True)
class RelationReadRequest:
    """A bounded continuation of one observed native relationship.

    Attributes:
        target: Native identity of the element, window, or application being observed.
        property: Exact native relationship/property name to read or account for.
        cursor: Opaque continuation token from the same source observation, or None initially.
        limit: Existing upper bound on returned items for this local page/format operation.
        timeout_ms: Maximum duration in milliseconds, bounded by the enclosing call deadline.
    """

    target: Binding
    property: str
    cursor: str | None = None
    limit: int = 128
    timeout_ms: int = 30000


@dataclass(frozen=True)
class OpenRelationRead:
    """A relation request paired with its existing native operation lease.

    Attributes:
        request: A bounded continuation of one observed native relationship.
        read: Open native operation lease; owns the token and original millisecond deadline.
    """

    request: RelationReadRequest
    read: OperationLease[Binding]


if TYPE_CHECKING:
    from typing import Collection
