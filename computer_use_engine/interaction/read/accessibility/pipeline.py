# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native fact and relation collection with explicit profile/query boundaries.

No input route or presentation compression is available here. The existing
NativeReader remains the sole owner of its session reference allocator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.facts.passes.collect_native_facts import (
    stage as collect_native_facts,
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
from computer_use.pipeline import Pipeline, Sequence

if TYPE_CHECKING:

    from computer_use.interaction.read.acquisition.facts.model import FactReadRequest
    from computer_use.interaction.read.accessibility.snapshot import ReadSnapshot
    from computer_use.interaction.read.acquisition.reader import NativeReader
    from computer_use.pipeline.core import Step


class AccessibilityReadPipeline(Pipeline["FactReadRequest", "ReadSnapshot"]):
    """Collect and normalize one bounded native fact batch.

    Resolve the native scope and choose fields and query options from provider capabilities.
    Read the batch, build semantic indexes with session aliases, and return the snapshot
    without discarding facts already returned when the query budget expires.

    Attributes:
        reader: Native reader owning scope validation, fact collection, and session display
            aliases.
    """

    def __init__(self, reader: NativeReader) -> None:
        self.reader = reader
        super().__init__(root=self.build())

    def build(self) -> Step[FactReadRequest, ReadSnapshot]:
        reader = self.reader

        return Sequence(
            name="AccessibilityReadPipeline",
            steps=[
                resolve_fact_scope(reader=reader),
                plan_fact_profile(reader=reader),
                collect_native_facts(reader=reader),
                normalize_native_facts(reader=reader),
            ],
        )
