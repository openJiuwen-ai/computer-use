# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Direct, observation-local access to an element's complete captured facts."""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING

from computer_use.diagnostics import local_failure
from computer_use.interaction.read.presentation.details import format_read_issues
from computer_use.interaction.read.presentation.document import DisplayDocument, DisplayTree
from computer_use.interaction.read.presentation.fallback import UncompressedFacts, literal

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.preparation import CapturedSnapshot


_FIRST_FIELDS = (
    "role",
    "identifier",
    "url",
    "role_description",
    "name",
    "description",
    "control_value.value",
    "text.content",
    "numeric_value.value",
)


@dataclass(frozen=True)
class _Source:
    """One frozen capture and its published scope, with lazy native-record indexes."""

    snapshot: CapturedSnapshot
    scope: tuple[Hashable, ...]

    @cached_property
    def facts(self) -> UncompressedFacts:
        return UncompressedFacts(self.snapshot.facts, self.snapshot.refs)

    @cached_property
    def issue_indices(self) -> tuple[dict[str, tuple[int, ...]], tuple[int, ...]]:
        """Index node-specific and capture-wide issues without changing their order."""
        nodes: dict[str, list[int]] = {}
        global_indices: list[int] = []
        for ordinal, issue in enumerate(self.snapshot.native.issues):
            if len(issue.path) >= 2 and issue.path[0] == "nodes":
                nodes.setdefault(str(issue.path[1]), []).append(ordinal)
            else:
                global_indices.append(ordinal)
        return {node: tuple(indices) for node, indices in nodes.items()}, tuple(global_indices)

    def render(self, reference: str) -> str:
        """Render one native object, retaining uncollected endpoints and related read gaps."""
        snapshot = self.snapshot
        token = snapshot.refs[reference]
        identity = dict(
            backend=snapshot.native.backend,
            session=snapshot.native.session,
            observation=snapshot.native.id,
            node=token,
            scope=self.scope,
            observed=snapshot.facts.get("observed", {}),
        )
        lines = ["Source: " + literal(identity)]
        node = self.facts.nodes.get(token)
        if node is None:
            lines.append(reference + " native element: referenced but not collected in this capture.")
        else:
            if node.get("observed") is False:
                lines.append("Collection: referenced element was not expanded; retained boundary facts follow.")
            lines.extend(self.facts.records((reference,), first_fields=_FIRST_FIELDS))
        by_node, global_indices = self.issue_indices
        ordinals = sorted((*global_indices, *by_node.get(token, ())))
        issues, _ = format_read_issues(snapshot.native, self.facts.aliases, ordinals=ordinals)
        if issues:
            lines.append(issues)
        return "\n".join(lines)


@dataclass(frozen=True)
class CapturedElementDocument:
    """All captured records for one original element number, in publication order."""

    reference: str
    sources: tuple[_Source, ...]

    @property
    def text(self) -> str:
        return "\n\n".join(
            (f"Captured element {self.reference}:", *(source.render(self.reference) for source in self.sources))
        )


class CapturedElementDetails:
    """Resolve a display number against frozen sources without querying or projecting UI.

    Multiple captures of the same native identity remain separate records. Reusing a
    number for a different identity is rejected at lookup instead of selecting a source.
    """

    def __init__(self, sources: Iterable[tuple[CapturedSnapshot, tuple[Hashable, ...]]]) -> None:
        retained: list[_Source] = []
        seen: set[tuple[int, tuple[Hashable, ...]]] = set()
        for snapshot, scope in sources:
            key = (id(snapshot), scope)
            if key not in seen:
                seen.add(key)
                retained.append(_Source(snapshot, scope))
        self.sources = tuple(retained)

    @classmethod
    def from_document(cls, document: DisplayDocument) -> CapturedElementDetails:
        """Retain the document's prepared captures, including references folded from its tree."""
        return cls((part.prepared.snapshot, part.scope) for part in document.parts if isinstance(part, DisplayTree))

    @cached_property
    def _references(self) -> dict[str, tuple[_Source, ...]]:
        references: dict[str, list[_Source]] = {}
        for source in self.sources:
            for ref in source.snapshot.refs:
                references.setdefault(ref, []).append(source)
        return {ref: tuple(sources) for ref, sources in references.items()}

    def __call__(self, element: int) -> CapturedElementDocument:
        """Return this number's retained captures, or report an absent or conflicting alias."""
        if type(element) is not int or element < 0:
            raise local_failure(
                "details element must be a nonnegative integer", "use an element number from this observation"
            )
        reference = str(element)
        sources = self._references.get(reference, ())
        if not sources:
            raise local_failure(
                f"element {element} was not recorded in this observation",
                "use a number from this observation, or omit element for all captured details",
            )
        identities = {
            (source.snapshot.native.backend, source.snapshot.native.session, source.snapshot.refs[reference])
            for source in sources
        }
        instances = {source.scope[1] for source in sources if len(source.scope) > 1 and source.scope[1] is not None}
        if len(identities) != 1 or len(instances) > 1:
            raise local_failure(
                f"element {element} has conflicting captured identities",
                "omit element to inspect the separately scoped captured details",
            )
        return CapturedElementDocument(reference, sources)
