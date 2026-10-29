# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The staged v5 native read pipeline, independent of Agent/MCP and UI input."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.interaction.control import scope_control
from computer_use.interaction.read.acquisition.facts.helpers import validate_fact_scope
from computer_use.interaction.read.acquisition.facts.model import FactReadRequest, RelationReadRequest
from computer_use.interaction.read.acquisition.facts.pipeline import FactCollectionPipeline, RelationCollectionPipeline
from computer_use.interaction.read.acquisition.text.model import CapturedTextRequest
from computer_use.interaction.read.acquisition.text.pipeline import CapturedTextPipeline
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.pipeline import Control

if TYPE_CHECKING:
    from typing import Mapping

    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.interaction.records import Remaining
    from computer_use.native.contracts import Binding
    from computer_use.native.lifecycle import OperationLease
    from computer_use.native.port import NativePort
    from computer_use.protocol.records import FactNode, Record, RelationPage
    from computer_use.protocol.records import TextPage as NativeTextPage


@dataclass(frozen=True)
class CapturedField:
    """Retained text value or native text reference from one driver session.

    Attributes:
        session: Driver session that owns the retained native references.
        value: Complete text value retained locally, when supplied by the provider.
        text_ref: Immutable native text-capture reference used for paged reads.
        total_units: Captured text length in the provider's declared text units.
    """

    session: str
    value: str | None = None
    text_ref: str | None = None
    total_units: int | None = None


@dataclass(frozen=True)
class ChildContinuation:
    """Native relationship cursor with its consumed count and reported total.

    Attributes:
        cursor: Opaque native continuation token for the observed child relationship.
        offset: Starting position in the source's declared text or relation units.
        total: Total entries reported for the paged native relationship.
    """

    cursor: str
    offset: int
    total: int


def captured_children(
    snapshot: SemanticSnapshot,
    reference: str,
    *,
    relations: Mapping[str, NativeRelation] | None = None,
) -> ChildContinuation | None:
    """Extract the continuation for an observed element's native child relationship.

    Args:
        snapshot: Native facts and aliases from the element's source observation.
        reference: Display alias of the element whose children were collected.
        relations: Optional index of native child relationships by source token.

    Returns:
        The retained cursor, consumed child count, and reported total, when available.
    """
    token = snapshot.refs.get(reference)
    if relations is None:
        relations = {r["source"]: r for r in snapshot.facts["relations"] if r["kind"] == "children"}
    relation = relations.get(token)
    if relation is None or relation["certainty"] != "confirmed" or "cursor" not in relation:
        return None
    return ChildContinuation(
        relation["cursor"], relation.get("offset", 0) + len(relation["targets"]), relation["total"]
    )


def captured_field(
    snapshot: SemanticSnapshot,
    reference: str,
    field: str | None,
    *,
    nodes: Mapping[str, FactNode] | None = None,
) -> CapturedField | None:
    """Retain a text field as a local value or immutable native text reference.

    Args:
        snapshot: Native fact batch and field indexes for the source observation.
        reference: Display alias of the element that supplies the field.
        field: Native field name selected as this element's text source.
        nodes: Optional index of native nodes by token, for repeated lookups.

    Returns:
        Captured text metadata when the requested field exposes readable text.
    """
    source = snapshot.text_refs.get((reference, field))
    if source is not None and "textRef" in source:
        return CapturedField(
            snapshot.facts["session"], text_ref=source["textRef"], total_units=source.get("totalUnits")
        )
    token = snapshot.refs.get(reference)
    node = (
        nodes.get(token)
        if nodes is not None
        else next((node for node in snapshot.facts["nodes"] if node["node"] == token), None)
    )
    if node is None or field in node["fieldStatus"] or not isinstance(node["values"].get(field), str):
        return None
    return CapturedField(snapshot.facts["session"], value=node["values"][field])


class NativeReader:
    """Bounded native-fact reads with one session-owned display allocator.

    Attributes:
        port: Native query and operation-lifecycle port.
        _references: Allocator for the current driver generation, or None before
            its first collected fact batch; it is not owned by a read IR.
    """

    _references: DisplayReferences | None

    async def wait_for_change(
        self,
        target: Binding,
        snapshot: SemanticSnapshot | None,
        remaining: Remaining,
        *,
        delay_ms: int = 200,
    ) -> str:
        """Wait for a content-change event or the remaining polling delay.

        Windows providers with a matching change token use the native event wait.
        Other observations use the bounded polling delay before the next fact read.

        Args:
            target: Native scope to watch for content changes.
            snapshot: Last collected snapshot supplying the native change token.
            remaining: Milliseconds left on the enclosing call's deadline.
            delay_ms: Maximum event wait or polling delay in milliseconds.

        Returns:
            Native event reason, or ``poll`` when the polling delay completes.
        """
        started = time.monotonic()
        metadata = snapshot.collection if snapshot is not None else None
        token = metadata.change_token if metadata is not None else None
        matches_token = (
            True
            if (
                "nativeReadEvents" in self.port.connection.capabilities
                and token is not None
                and snapshot.native.session == target.session
                and metadata.surface == target.surface
            )
            else False
        )
        if matches_token:
            async with self.port.calls.operation(target, remaining) as read:
                result = await self.port.wait_for_change(read.token, read.target, token, delay_ms, read.remaining())
            if result["reason"] != "unavailable":
                return result["reason"]
        left = min(delay_ms / 1000 - (time.monotonic() - started), remaining() / 1000)
        if left > 0:
            await asyncio.sleep(left)
        return "poll"

    def __init__(self, port: NativePort) -> None:
        self.port = port
        self._references = None

    def display_references(self, session: str) -> DisplayReferences:
        """Borrow the allocator owned by this reader's current driver session.

        Collection may allocate aliases even if its later normalization fails.
        Selecting a new driver generation replaces only this reader's allocator.
        """
        if self._references is None or self._references.session != session:
            self._references = DisplayReferences(session)
        return self._references

    async def read(
        self,
        target: Binding,
        *,
        fields: list[str] | None = None,
        max_nodes: int = 1000,
        max_depth: int | None = None,
        timeout_ms: int = 30000,
        children_page: RelationPage | None = None,
        relation_page: RelationPage | None = None,
        scope_kind: str | None = None,
        _control: Control | None = None,
    ) -> SemanticSnapshot:
        self.validate(target, max_nodes, max_depth)
        async with self.port.calls.open(target, timeout_ms) as read:
            return await self.collect(
                read,
                fields=fields,
                max_nodes=max_nodes,
                max_depth=max_depth,
                children_page=children_page,
                relation_page=relation_page,
                scope_kind=scope_kind,
                _control=_control,
            )

    @staticmethod
    def validate(target: Binding, max_nodes: int, max_depth: int | None) -> None:
        validate_fact_scope(target, max_nodes, max_depth)

    async def collect(
        self,
        read: OperationLease[Binding],
        *,
        node: str | None = None,
        fields: list[str] | None = None,
        max_nodes: int = 1000,
        max_depth: int | None = None,
        children_page: RelationPage | None = None,
        relation_page: RelationPage | None = None,
        scope_kind: str | None = None,
        _control: Control | None = None,
    ) -> SemanticSnapshot:
        """Collect inside an existing node or window read operation."""
        request = FactReadRequest(read, node, fields, max_nodes, max_depth, children_page, relation_page, scope_kind)
        control = scope_control(self.port, read.remaining, _control or Control())
        return await FactCollectionPipeline(self).arun(request, control)

    async def read_relation(
        self,
        target: Binding,
        relation_property: str,
        *,
        cursor: str | None = None,
        limit: int = 128,
        timeout_ms: int = 30000,
    ) -> Record:
        """Read one page of an observed native relationship.

        Args:
            target: Element binding that owns the relationship.
            property: Exact native relationship name.
            cursor: Continuation token from the preceding page, or None for its first page.
            limit: Maximum relationship targets to return in this page.
            timeout_ms: Maximum duration of this relation read in milliseconds.

        Returns:
            Native relationship targets and continuation metadata.
        """
        return await RelationCollectionPipeline(self.port).arun(
            RelationReadRequest(target, relation_property, cursor, limit, timeout_ms)
        )

    async def read_text(
        self,
        snapshot: SemanticSnapshot,
        reference: str,
        field: str,
        *,
        offset: int = 0,
        length: int = 4096,
        timeout_ms: int = 30000,
    ) -> NativeTextPage:
        source = captured_field(snapshot, reference, field)
        if source is None:
            raise ValueError("This observed field has no immutable full-text capture")
        return await self.read_captured_text(source, offset=offset, length=length, timeout_ms=timeout_ms)

    async def read_captured_text(
        self, source: CapturedField, *, offset: int = 0, length: int = 4096, timeout_ms: int = 30000
    ) -> NativeTextPage:
        return await CapturedTextPipeline(self.port).arun(CapturedTextRequest(source, offset, length, timeout_ms))


if TYPE_CHECKING:
    from computer_use.protocol.records import NativeRelation
