# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Direct, observation-local access to an element's complete captured facts."""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from functools import cached_property

from computer_use.diagnostics import local_failure
from computer_use.interaction.read.accessibility.snapshot import NativeSnapshot
from computer_use.interaction.read.presentation.document import DisplayDocument, DisplayTree

@dataclass(frozen=True)
class _Source:
    """One frozen capture and its published scope, with lazy native-record indexes."""

    snapshot: NativeSnapshot
    scope: tuple[Hashable, ...]

    def identity(self, reference: str) -> tuple[str, str, str]:
        snapshot = self.snapshot
        return snapshot.backend, snapshot.session, snapshot.refs[reference]

    def render(self, reference: str) -> str:
        return self.snapshot.numbered.element_details(reference, self.scope)


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

    def __init__(self, sources: Iterable[tuple[NativeSnapshot, tuple[Hashable, ...]]]) -> None:
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
            raise local_failure("details element must be a nonnegative integer")
        reference = str(element)
        sources = self._references.get(reference, ())
        if not sources:
            raise local_failure(
                f"element {element} was not recorded in this observation",
            )
        identities = {source.identity(reference) for source in sources}
        instances = {source.scope[1] for source in sources if len(source.scope) > 1 and source.scope[1] is not None}
        if len(identities) != 1 or len(instances) > 1:
            raise local_failure(
                f"element {element} has conflicting captured identities",
            )
        return CapturedElementDocument(reference, sources)
