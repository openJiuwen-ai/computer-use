# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Normalize native facts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.facts.model import RESOURCE, CollectedFacts
from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.acquisition.reader import NativeReader
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.pipeline.core import Control


def create(*, reader: NativeReader) -> Callable[[CollectedFacts, Control], SemanticSnapshot]:
    def normalize(value: CollectedFacts, control: Control) -> SemanticSnapshot:
        """Normalize the collected batch using the reader's current session display aliases and
        provider facts.
        """
        target = value.profile.scope.target
        # normalizeFacts allocates/touches session aliases in its existing
        # order, including on a later normalization failure. This is a
        # RESOURCE stage, never falsely labelled as a pure transform.
        references = reader.display_references(target.session)
        result = normalize_facts(
            value.batch,
            references,
            driver_features=getattr(reader.port.connection, "capabilities", ()),
            scope_kind=value.profile.scope.request.scope_kind,
            operation_capabilities=value.metadata.operation_capabilities,
            diagnostic_fields=value.metadata.diagnostic_fields,
            field_sources=value.metadata.field_sources,
        )
        result.collection = value.metadata
        return result

    return normalize


stage = Pass.factory(
    name="normalize_native_facts",
    factory=create,
    contract=Contract(
        CollectedFacts,
        object,
        RESOURCE,
        requires=("raw native facts", "session reference owner"),
        writes=("session aliases", "normalized SemanticSnapshot"),
    ),
)
