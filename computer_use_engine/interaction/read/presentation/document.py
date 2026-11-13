# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Immutable semantic display document consumed by diff planning and final rendering."""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from dataclasses import dataclass, is_dataclass, replace
from functools import cached_property

from computer_use.interaction.read.presentation.content import DisplayText, text
from computer_use.interaction.read.accessibility.preparation import PreparedSnapshot
from computer_use.protocol.observation import NativeObservation, encode_observation


def _data(value: object) -> object:
    """Expose structured inputs for sizing and diagnostics without generating display text."""
    from collections.abc import Mapping

    if isinstance(value, NativeObservation):
        return encode_observation(value)
    if isinstance(value, DisplayText) and value.kind == "details_hint":
        prepared, ref = value.values
        return {"kind": value.kind, "ref": ref, "observed": _data(_observed(prepared))}
    if is_dataclass(value):
        return {name: _data(getattr(value, name)) for name in value.__dataclass_fields__}
    if isinstance(value, Mapping):
        return {str(key): _data(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_data(item) for item in value]
    return value


def _observed(prepared: PreparedSnapshot) -> object:
    return prepared.snapshot.capture.observed


@dataclass(frozen=True)
class DisplayTree:
    """Prepared snapshot and its exact display scope, before any text formatting.

    Attributes:
        prepared: Frozen original facts and compressed semantic roots.
        scope: Native source identity used to keep comparisons within the same tree.
        indent: Related-root indentation added by document composition.
        details: Whether this explicit view requests complete native supplements.
    """

    prepared: PreparedSnapshot
    scope: tuple[Hashable, ...] = ()
    indent: int = 0
    details: bool = False



@dataclass(frozen=True)
class DisplayDocument:
    """Ordered structured sections, trees, and immutable observation context.

    Attributes:
        parts: Captured text fields, templates, and prepared semantic snapshots in output order.
        context: Captured scope, document, and modal relationships; tree scopes establish diff identity.
    """

    parts: tuple[DisplayText | DisplayTree, ...]
    context: tuple[Hashable, ...] = ()

    @property
    def text(self) -> str:
        """Render this captured document in full at an explicit text-access boundary."""
        from computer_use.interaction.read.presentation.rendering.document import render_document

        return render_document(self)

    @classmethod
    def tree(cls, prepared: PreparedSnapshot, *, details: bool = False) -> DisplayDocument:
        """Wrap a prepared snapshot without rendering fields or layout."""
        return cls((DisplayTree(prepared, details=details),))

    @classmethod
    def join(cls, parts: Iterable[str | DisplayText | DisplayDocument], separator: str = "\n") -> DisplayDocument:
        """Compose structured sections without materializing their text."""
        result: list[DisplayText | DisplayTree] = []
        for index, part in enumerate(parts):
            if index:
                result.append(text(separator))
            result.extend(part.parts if isinstance(part, cls) else (text(part) if isinstance(part, str) else part,))
        return cls(tuple(result))

    def indented(self) -> DisplayDocument:
        """Indent a related presentation while preserving its native and display parentage."""
        return replace(
            self,
            parts=tuple(
                replace(part, indent=part.indent + 1)
                if isinstance(part, DisplayTree)
                else DisplayText("indent", (part,))
                for part in self.parts
            ),
        )

    def scoped(self, scope: tuple[Hashable, ...]) -> DisplayDocument:
        """Attach an exact source identity without changing native execution bindings."""
        return replace(
            self,
            parts=tuple(replace(part, scope=scope) if isinstance(part, DisplayTree) else part for part in self.parts),
        )

    def to_data(self) -> dict[str, object]:
        """Return captured semantic data for byte accounting, without invoking a renderer."""
        parts: list[object] = []
        for part in self.parts:
            if isinstance(part, DisplayTree):
                from computer_use.interaction.read.delivery.diff.baseline import ComparisonTree

                parts.append({
                    "kind": "native_tree",
                    "indent": part.indent,
                    "comparison": ComparisonTree.capture(part).to_data(),
                    "observed": _data(_observed(part.prepared)),
                    "refs": _data(part.prepared.snapshot.refs),
                })
            else:
                parts.append(_data(part))
        return dict(version=1, context=_data(self.context), parts=parts)

    @cached_property
    def estimated_bytes(self) -> int:
        """Estimate retained structured bytes for the bounded delivery baseline cache."""
        from .sizing import DocumentSize

        return DocumentSize().document(self)

    def summary_data(self) -> dict[str, object]:
        """Describe captured sections and source identities for pre-render diagnostics."""
        trees = []
        for part in self.parts:
            if isinstance(part, DisplayTree):
                refs = part.prepared.refs
                trees.append({
                    "scope": list(part.scope),
                    "nodes": len(refs),
                    "refs": list(refs),
                    "observed": _data(_observed(part.prepared)),
                })
        return dict(
            version=1,
            sections=len(self.parts),
            trees=trees,
        )
