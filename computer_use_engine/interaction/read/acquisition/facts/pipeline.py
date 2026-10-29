# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native fact and relation collection with explicit profile/query boundaries.

No input route or presentation compression is available here. The existing
NativeReader remains the sole owner of its session reference allocator.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from computer_use.interaction.control import NativeScopeBody
from computer_use.interaction.read.acquisition.facts.model import OpenRelationRead
from computer_use.interaction.read.acquisition.facts.passes.check_fact_budget import (
    stage as check_fact_budget,
)
from computer_use.interaction.read.acquisition.facts.passes.collect_native_facts import (
    stage as collect_native_facts,
)
from computer_use.interaction.read.acquisition.facts.passes.collect_native_relation import (
    stage as collect_native_relation,
)
from computer_use.interaction.read.acquisition.facts.passes.normalize_native_facts import (
    stage as normalize_native_facts,
)
from computer_use.interaction.read.acquisition.facts.passes.plan_fact_profile import (
    stage as plan_fact_profile,
)
from computer_use.interaction.read.acquisition.facts.passes.resolve_fact_scope import (
    stage as resolve_fact_scope,
)
from computer_use.interaction.read.acquisition.facts.passes.validate_relation_request import (
    stage as validate_relation_request,
)
from computer_use.pipeline import Pipeline, Scope, Sequence

if TYPE_CHECKING:
    from typing import AsyncIterator

    from computer_use.interaction.read.acquisition.facts.model import FactReadRequest, RelationReadRequest
    from computer_use.interaction.read.acquisition.reader import NativeReader
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control, Step
    from computer_use.protocol.records import Record


class FactCollectionPipeline(Pipeline["FactReadRequest", "SemanticSnapshot"]):
    """Collect and normalize one bounded native fact batch.

    Resolve the native scope and choose fields and query options from provider capabilities.
    Read the batch, build semantic indexes with session aliases, and return the snapshot
    under the original deadline.

    Attributes:
        reader: Native reader owning scope validation, fact collection, and session display
            aliases.
    """

    def __init__(self, reader: NativeReader) -> None:
        self.reader = reader
        super().__init__(root=self.build())

    def build(self) -> Step[FactReadRequest, SemanticSnapshot]:
        reader = self.reader

        return Sequence(
            name="FactCollectionPipeline",
            steps=[
                resolve_fact_scope(reader=reader),
                plan_fact_profile(reader=reader),
                collect_native_facts(reader=reader),
                normalize_native_facts(reader=reader),
                check_fact_budget(),
            ],
        )


class RelationCollectionPipeline(Pipeline["RelationReadRequest", "Record"]):
    """Read a page of one observed native relationship.

    Validate the binding, relationship, cursor, and page size before opening its native
    operation. Return the driver's relationship page from that fixed element scope.

    Attributes:
        port: Native query and lifecycle services used for relationship continuation reads.
    """

    def __init__(self, port: NativePort) -> None:
        self.port = port
        super().__init__(root=self.build())

    def build(self) -> Step[RelationReadRequest, Record]:
        port = self.port

        @asynccontextmanager
        async def open_relation(request: RelationReadRequest, control: Control) -> AsyncIterator[OpenRelationRead]:
            async with port.calls.open(request.target, request.timeout_ms) as read:
                yield OpenRelationRead(request, read)

        return Sequence(
            name="RelationCollectionPipeline",
            steps=[
                validate_relation_request(),
                Scope(
                    name="NativeRelationReadScope",
                    using=open_relation,
                    body=NativeScopeBody(port, collect_native_relation(port=port), lambda value: value.read.remaining),
                ),
            ],
        )
