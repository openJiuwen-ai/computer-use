# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Account for native values at their actual presentation destinations.

The renderer records emissions, not property reads or string matches. Values
without an equivalent destination are grouped in a lossless supplement. This
module never resolves an execution target or acquires new native facts.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from computer_use.interaction.read.presentation.fallback import literal

if TYPE_CHECKING:
    from typing import Iterable, Mapping

    from computer_use.interaction.read.presentation.ir import FactKey, ProjectionInput, ProjectionResult
    from computer_use.interaction.read.presentation.records import NodeView, RenderedTree
    from computer_use.interaction.read.presentation.rendering.output import RenderedDocument
    from computer_use.protocol.records import FactNode, Record


@dataclass(frozen=True)
class SourceEmission:
    """One source fact represented by a rendered field or supplementary entry.

    Attributes:
        fact: Source node and field represented by this emission.
        value: Native or projected field value; provider-specific values remain heterogeneous.
        target: Display reference receiving the emitted value.
        field: Output field that represents the source fact.
        kind: Representation kind, such as a displayed field or residual supplement.
    """

    fact: FactKey
    value: object
    target: str
    field: str
    kind: str


class RenderedResult(dict):
    """Rendering fields with retained display structure and source audit evidence.

    Attributes:
        source_emissions: Captured fields accounted for by this rendering.
        source_input: Original projection facts used for field accounting.
        document: Immutable display sections used by incremental delivery. This metadata
            stays outside the serialized rendering fields.
    """

    def __init__(
        self,
        values: Record,
        emissions: Iterable[SourceEmission] = (),
        source: ProjectionInput | None = None,
        *,
        document: RenderedDocument | None = None,
    ) -> None:
        super().__init__(values)
        self.source_emissions, self.source_input = tuple(emissions), source
        self.document = document

    def __deepcopy__(self, memo: dict[int, Any]) -> RenderedResult:
        from copy import deepcopy

        return RenderedResult(
            deepcopy(dict(self), memo), self.source_emissions, self.source_input, document=self.document
        )


class RenderedFields:
    """Track displayed fields and source emissions for native-fact accounting."""

    def __init__(self, projection: ProjectionResult | None = None) -> None:
        self.entries = []
        self._by_ref = {}
        self._provenance = getattr(projection, "provenance", {})
        self.source_input = getattr(projection, "source", None)
        self.source_emissions = []

    def _append(self, entry: Record) -> None:
        self.entries.append(entry)
        self._by_ref.setdefault(entry["ref"], []).append(entry)

    def record(self, node: NodeView, field: str, *, kind: str = "display", target: str | None = None) -> None:
        """Record the source facts represented by one rendered display field.

        Args:
            node: Source/projected node in this exact view; its native identity is unchanged.
            kind: Declared local branch or representation kind.
            target: Native identity within the current application and window scope.
        """
        if field not in node:
            return
        presented = self._provenance.get((node["ref"], field))
        if presented is not None:
            for part in presented.parts:
                self.source_emissions.extend(
                    SourceEmission(fact, part.value, target or node["ref"], field, kind) for fact in part.facts
                )
        parts = (
            [part for part in node.get("textSources", ()) if part["field"] == field]
            if field in ("name", "description", "value") and kind != "continuation"
            else []
        )
        if parts:
            for part in parts:
                self._append(
                    dict(
                        ref=part["sourceRef"],
                        field=part["attribute"],
                        value=part["text"],
                        target=target or node["ref"],
                        kind="equivalent",
                    )
                )
        else:
            self._append(dict(ref=node["ref"], field=field, value=node[field], target=target or node["ref"], kind=kind))

    def equivalent_text(self, node: NodeView, field: str) -> None:
        """Compare caption text using the renderer's equivalence rules.

        Args:
            node: Source/projected node in this exact view; its native identity is unchanged.
        """
        value = node.get(field)
        if not isinstance(value, str) or not value:
            return
        claim = next(
            (
                entry
                for entry in self._by_ref.get(node["ref"], ())
                if entry["field"] != field and entry["value"] == value and entry["kind"] != "continuation"
            ),
            None,
        )
        if claim is not None:
            self.record(node, field, kind="equivalent", target=claim["target"])


def account_fields(
    members: Iterable[str],
    refs: Mapping[str, str],
    sources: Mapping[tuple[str, str], Record],
    native_nodes: Mapping[str, FactNode],
    rendered: RenderedTree,
) -> tuple[dict[str, list[str]], Record]:
    """Return grouped residuals and a destination for each collected value.

    Known normalization is explicit in sources; unknown values already have native annotations.
    A continuation is valid only for the publicly captured value, never for an arbitrary field
    which happens to have the same text.

    Args:
        members: Source references belonging to this exact branch or declared control.
        refs: Display-to-native identity map owned by the observation/session service.
        sources: Original sources or explicit source destinations retained by this stage.
        native_nodes: Original native-node facts indexed by their exact native tokens.
        rendered: Current render and its retained source/emission accounting.
    """
    # Validate the exact field provenance against the immutable source view.
    # Legacy coverage still determines the established supplements; migrating
    # their compression policy would be a separate observable behavior change.
    validate_source_emissions(rendered, members)
    claims = {}
    for claim in rendered.get("fieldCoverage", ()):
        claims.setdefault((claim["ref"], claim["field"]), []).append(claim)
    groups, destinations, actions = {}, [], []

    def supplement(ref: str, label: str) -> None:
        groups.setdefault(label, []).append(ref)

    for ref in members:
        native = native_nodes[refs[ref]]
        for key, raw in native["values"].items():
            source = sources[(ref, key)]
            if source["representation"] == "raw":
                continue
            destination: Record = dict(ref=ref, property=key)
            if source["representation"] == "annotation":
                destination["kind"] = "annotation"
            elif source["representation"] == "projection":
                projected = []
                for part in source["projections"]:

                    def _iter_claim(_source_items: Iterator[Any], projection_part: Any) -> Iterator[Any]:
                        for claim in _source_items:
                            if (
                                claim["kind"] != "continuation"
                                and type(claim["value"]) is type(projection_part["normalized"])
                                and claim["value"] == projection_part["normalized"]
                            ):
                                yield claim

                    claim = next(
                        _iter_claim(iter(claims.get((ref, part["field"]), ())), part),
                        None,
                    )
                    if claim is None:
                        break
                    projected.append(dict(field=part["field"], target=claim["target"], kind=claim["kind"]))
                if projected and len(projected) == len(source["projections"]):
                    destination.update(kind="equivalent", projections=projected)
                else:
                    supplement(ref, key + ": " + literal(raw))
                    destination["kind"] = "supplement"
            else:
                matching = [
                    claim
                    for claim in claims.get((ref, source["field"]), ())
                    if type(claim["value"]) is type(source["normalized"]) and claim["value"] == source["normalized"]
                ]
                status = native["fieldStatus"].get(key)
                captured = (
                    source["field"] == "value" and isinstance(raw, str) and (status is None or "textRef" in status)
                )
                claim = next((claim for claim in matching if claim["kind"] != "continuation" or captured), None)
                if claim is None:
                    supplement(ref, key + ": " + literal(raw))
                    destination["kind"] = "supplement"
                else:
                    destination.update(kind=claim["kind"], target=claim["target"])
            destinations.append(destination)

        # Primary actions may be omitted from the tree as "clickable" and
        # descriptions may alias a name. Preserve the complete native catalogue
        # once per distinct record, including its coverage and description status.
        catalogue = native["actions"]
        label = (
            "Native actions: none (complete)"
            if catalogue == dict(state="complete", items=[])
            else "Native actions: " + literal(catalogue)
        )
        supplement(ref, label)
        actions.append(dict(ref=ref, kind="supplement"))

    return groups, dict(values=destinations, actions=actions)


def validate_source_emissions(rendered: RenderedResult, members: Iterable[str]) -> None:
    """Check rendered emissions against the immutable source-fact ledger.

    Args:
        rendered: Current render and its retained source/emission accounting.
        members: Source references belonging to this exact branch or declared control.
    """
    source = getattr(rendered, "source_input", None)
    if source is None:
        return
    for emission in rendered.source_emissions:
        fact = emission.fact
        native = source.native_nodes.get(fact.source)
        invalid_members = (
            True
            if (
                fact.source not in members
                or native is None
                or fact.attribute not in native
                or native[fact.attribute] != emission.value
                or type(native[fact.attribute]) is not type(emission.value)
            )
            else False
        )
        if invalid_members:
            raise ValueError("Rendered field provenance has no matching source fact")
        if emission.target not in members:
            raise ValueError("Rendered field provenance crosses the native branch")
