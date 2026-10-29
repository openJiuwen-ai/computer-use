# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.accounting.relations import RelationPresentation
from computer_use.pipeline import Effect

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.records import RenderedTree, SemanticNode, SnapshotFacts
    from computer_use.protocol.records import FactNode, Record


PURE = frozenset({Effect.PURE})


PRESENTATION_FAILURES = (ArithmeticError, LookupError, TypeError, ValueError, AssertionError, RecursionError)


@dataclass(frozen=True)
class SnapshotRenderRequest:
    """Presentation options and read-only source facts for one snapshot.

    Attributes:
        snapshot: Facts captured for this observation; display edits do not mutate native
            evidence.
        trace: Whether to record per-pass presentation diagnostics for this observation.
        compact: Whether to use compact task-facing text while retaining full source details.
        public_api: Whether continuation hints use the current public element API syntax.
    """

    snapshot: SnapshotFacts
    trace: bool = False
    compact: bool = False
    public_api: bool = False


@dataclass(frozen=True)
class BranchFacts:
    """Read-only evidence for presenting one root and its collected members.

    Attributes:
        request: Presentation options and read-only source facts for one snapshot.
        root: Root of the requested semantic view; native bindings remain separate.
        members: Source references belonging to this exact branch or declared control.
        native_nodes: Original native-node facts indexed by their exact native tokens.
        relations: Accounting helper over the original native relationships for this snapshot.
        readable_text: Display references whose primary native text source supports continuation.
    """

    request: SnapshotRenderRequest
    root: SemanticNode
    members: tuple[str, ...]
    native_nodes: dict[str, FactNode]
    relations: RelationPresentation
    readable_text: set[str]

    @property
    def context(self) -> Record:
        return dict(
            includeBackground=True,
            recordFields=True,
            nativeFacts=True,
            readableTextRefs=self.readable_text,
            recordStages=self.request.trace,
            compact=self.request.compact,
            publicApi=self.request.public_api,
        )


@dataclass(frozen=True)
class FieldAccounting:
    """Rendered fields with their unrepresented native-value supplements.

    Attributes:
        rendered: Rendered text, references, and source-emission metadata.
        residuals: Native field values that require supplementary presentation.
    """

    rendered: RenderedTree
    residuals: Record


@dataclass(frozen=True)
class RenderedPart:
    """One rendered root with lossless field, action, and relation accounting.

    Attributes:
        rendered: Rendered text, references, and source-emission metadata.
        supplements: Supplementary native field and action entries grouped by label.
        relation_supplements: Supplementary relationship entries keyed by source reference and relation.
        relation_sources: Source evidence retained for separately rendered native relationships.
        uncompressed: Whether a presentation failure used the source-preserving native-fact fallback.
    """

    rendered: RenderedTree
    supplements: Record
    relation_supplements: dict[tuple[str, str], list[str]]
    relation_sources: list[Record]
    uncompressed: bool = False


@dataclass(frozen=True)
class SnapshotParts:
    """Ordered root renders belonging to one snapshot presentation request.

    Attributes:
        request: Presentation options and read-only source facts for one snapshot.
        parts: Source observations collected in the plan's order.
    """

    request: SnapshotRenderRequest
    parts: tuple[RenderedPart, ...]
