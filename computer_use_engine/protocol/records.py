# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Typed directory messages and derived native-query indexes.

Record validation lives in the protocol decoders. These declarations describe
their output and the common semantic indexes, retaining open mappings for properties and directory
variants. Field status records preserve absent and failed reads explicitly.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypeAlias, TypedDict

Record: TypeAlias = dict[str, Any]
"""Open provider properties or host metadata with heterogeneous values."""


class Rectangle(TypedDict):
    """A rectangle in the coordinate space declared by its containing record.

    Attributes:
        x: Left edge in the containing record's coordinate units.
        y: Top edge in the same coordinate units.
        width: Horizontal extent in the same units.
        height: Vertical extent in the same units.
    """

    x: float
    y: float
    width: float
    height: float


class FactNode(TypedDict):
    """Facts for one native object, before semantic interpretation.

    Attributes:
        node: Opaque execution token scoped to the driver session.
        requestedFields: Properties actually requested from this object.
        values: Successfully read provider properties, including typed values.
        fieldStatus: Missing/failed/truncated property reads and their evidence.
        actions: Action catalogue, with state, items, and optional status detail.
    """

    node: str
    requestedFields: list[str]
    values: Record
    fieldStatus: Record
    actions: Record


class NativeRelation(TypedDict):
    """One observed relationship and its possibly incomplete target page.

    Attributes:
        source: Exact native source token in the containing driver's session.
        property: Native relationship name, such as children or focused element.
        targets: Ordered native target tokens on this page.
        certainty: Provider evidence for the relationship.
        state: Completeness/status of this relation read.
        cursor: Opaque continuation token, when more targets can be read.
        offset: Starting target index for this page.
        total: Full target count when reported by the provider.
        detail: Provider explanation of incomplete or conflicting evidence.
    """

    source: str
    property: str
    targets: list[str]
    certainty: str
    state: str
    cursor: NotRequired[str]
    offset: NotRequired[int]
    total: NotRequired[int]
    detail: NotRequired[str]


class FactCollection(TypedDict):
    """Derived field, action, and relationship indexes for common semantic rules.

    Attributes:
        session: Driver generation owning all tokens in this batch.
        platform: Platform whose native property/action names are retained.
        surface: Native surface constraining the collection.
        observed: Capture identifier and start/completion timestamps.
        roots: Requested native roots; related roots may also be collected.
        nodes: Objects collected within this read's budget and requested scope.
        requestedRelations: Relations requested from the provider.
        relations: Observed relationships, including continuation/status facts.
        unread: Known object tokens not expanded in this bounded read.
        stopReasons: Reasons the requested collection was incomplete.
        changeToken: Provider token for waiting for subsequent changes.
    """

    session: str
    platform: str
    surface: str
    observed: Record
    roots: list[str]
    nodes: list[FactNode]
    requestedRelations: list[str]
    relations: list[NativeRelation]
    unread: list[str]
    stopReasons: list[str]
    changeToken: NotRequired[str]


class ApplicationInfo(TypedDict):
    """One decoded installation/running application directory entry.

    Attributes:
        id: Exact provider identity for later directory/startup requests.
        displayName: User-facing name.
        isRunning: Whether this directory observed a running application.
        path: Installation path, or None when the provider omits it.
        identifier: Provider application identifier, when available.
        aliases: Additional names supplied by the provider.
        matchKeys: Application lookup keys supplied by the provider.
        windows: Additional selectable working-window choices.
    """

    id: str
    displayName: str
    isRunning: bool
    path: str | None
    identifier: str | None
    aliases: NotRequired[list[str]]
    matchKeys: NotRequired[list[str]]
    windows: NotRequired[list[Record]]


class SurfaceInfo(TypedDict):
    """One decoded surface; relationship certainty stays explicit.

    Attributes:
        window: Directory window identity.
        instance: Native working-instance identity.
        surface: Native input/capture surface identity.
        node: Accessibility root, or None when the surface lacks one.
        title: Observed title, which may be empty.
        kind: Native surface/window classification.
        parent: Confirmed owner identity, when known.
        parentState: Whether parent evidence is known, absent, or unknown.
        modal: Reported modality; None means unknown.
        nativeWindow: Whether a native window identity was observed.
        accessibility: Whether an accessibility root was observed.
        nativeIdentity: Optional host-local process/window IDs observed by the native provider.
    """

    window: str
    instance: str
    surface: str
    node: str | None
    title: str
    kind: str
    parent: str | None
    parentState: Literal["known", "none", "unknown"]
    modal: bool | None
    nativeWindow: bool
    accessibility: bool
    nativeIdentity: NotRequired[Record]


NativeDirectory: TypeAlias = Record
"""Decoded directory variant: applications, surfaces, or application roots.

All variants retain session/platform/observed/stopReasons. The selected kind
adds applications, surfaces, or roots; a surface/root directory's application
field is an identity string. The existing query/schema determines the variant.
"""


class RelationPage(TypedDict):
    """Request for an initial or continued page of native relationships.

    Attributes:
        property: Native relation to continue, when explicitly selected.
        cursor: Opaque continuation token, or None for the initial page.
        limit: Maximum target count requested from this page.
    """

    property: NotRequired[str]
    cursor: str | None
    limit: int


class TextPage(TypedDict):
    """A page from one immutable native captured-text source.

    Attributes:
        text: Text for the requested interval.
        offset: Starting position in the provider's native text units.
        total: Full immutable capture length in the same units.
        next: Next offset in native units, or None at the end of the capture.
    """

    text: str
    offset: int
    total: int
    next: int | None
