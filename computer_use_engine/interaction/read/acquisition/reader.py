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
from computer_use.interaction.read.acquisition.facts.model import FactReadRequest
from computer_use.interaction.read.accessibility.pipeline import AccessibilityReadPipeline
from computer_use.interaction.read.acquisition.text.model import CapturedTextRequest
from computer_use.interaction.read.acquisition.text.pipeline import CapturedTextPipeline
from computer_use.interaction.read.numbering.references import DisplayReferences, read_references
from computer_use.pipeline import Control

if TYPE_CHECKING:

    from computer_use.interaction.read.accessibility.snapshot import ReadSnapshot
    from computer_use.interaction.records import Remaining
    from computer_use.native.contracts import Binding
    from computer_use.native.lifecycle import OperationLease
    from computer_use.native.port import NativePort
    from computer_use.protocol.records import RelationPage
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
        snapshot: ReadSnapshot | None,
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
        token = snapshot.change_token if snapshot is not None else None
        session = snapshot.session if snapshot is not None else None
        surface = snapshot.surface if snapshot is not None else None
        matches_token = (
            True
            if (
                "nativeReadEvents" in self.port.connection.capabilities
                and token is not None
                and session == target.session
                and surface == target.surface
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
            self._references = read_references(session, getattr(self.port, "platform", None))
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
        strict_window: bool = False,
        _control: Control | None = None,
    ) -> ReadSnapshot:
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
                strict_window=strict_window,
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
        query_remaining: Remaining | None = None,
        strict_window: bool = False,
        _control: Control | None = None,
    ) -> ReadSnapshot:
        """Collect inside an existing node or window read operation.

        A narrower query allowance limits the native request. Normalization
        preserves facts already collected; later queries still use the lease's
        original deadline.
        """
        request = FactReadRequest(
            read, node, fields, max_nodes, max_depth, children_page, relation_page, scope_kind, query_remaining,
            strict_window,
        )
        control = scope_control(self.port, read.remaining, _control or Control())
        return await AccessibilityReadPipeline(self).arun(request, control)

    async def read_captured_text(
        self, source: CapturedField, *, offset: int = 0, length: int = 4096, timeout_ms: int = 30000
    ) -> NativeTextPage:
        return await CapturedTextPipeline(self.port).arun(CapturedTextRequest(source, offset, length, timeout_ms))


if TYPE_CHECKING:
    pass
